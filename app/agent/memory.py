"""
Agent Memory 记忆系统
模拟人类记忆机制：短期记忆、长期记忆、情景记忆、工作记忆
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Any, Optional

from app.llm.chat_model import chat_model
from utils.path_tool import get_abs_path


class AgentMemory:
    """
    Agent 记忆系统 —— 让 Agent 像人一样学习和记忆

    记忆类型说明：
      - short_term (短期记忆):   当前对话的上下文，容量有限，自动淘汰旧信息
      - long_term  (长期记忆):   持久化存储的重要知识，SQLite 持久化，支持语义检索
      - episodic   (情景记忆):   记录任务执行的经验教训 (任务, 方法, 结果, 反思)
      - working    (工作记忆):   当前任务执行过程中的临时状态 (计划、中间结果等)
      - skill      (技能记忆):   记录技能使用频率和效果，用于技能选择优化
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = get_abs_path("data/agent_memory.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path

        self.short_term: List[Dict] = []
        self.short_term_max = 20

        self.working: Dict[str, Any] = {}

        self.episodic_cache: List[Dict] = []

        self._init_db()

    # -------------------- 数据库初始化 --------------------
    def _init_db(self):
        """创建长期记忆和情景记忆的数据表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_key TEXT UNIQUE,
                content TEXT,
                importance REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                created_at TEXT,
                last_accessed TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT,
                approach TEXT,
                result TEXT,
                reflection TEXT,
                success INTEGER DEFAULT 0,
                tags TEXT,
                created_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skill_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT UNIQUE,
                use_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                avg_score REAL DEFAULT 0.0,
                last_used TEXT
            )
        """)

        conn.commit()
        conn.close()

    # -------------------- 1. 短期记忆 --------------------
    def add_short_term(self, role: str, content: str):
        self.short_term.append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        if len(self.short_term) > self.short_term_max:
            self.short_term = self.short_term[-self.short_term_max:]

    def get_short_term(self, n: int = None) -> List[Dict]:
        if n is None:
            return self.short_term
        return self.short_term[-n:]

    def summarize_short_term(self) -> str:
        if not self.short_term:
            return ""
        recent = "\n".join([
            f"[{m['role']}]: {m['content'][:200]}" for m in self.short_term[-10:]
        ])
        summary_prompt = (
            f"请将以下对话片段总结为3-5条关键信息，每条一行，用中文：\n\n{recent}"
        )
        try:
            summary = chat_model.invoke(summary_prompt).content
            return summary.strip()
        except Exception:
            return recent

    # -------------------- 2. 长期记忆 --------------------
    def save_long_term(self, key: str, content: str, importance: float = 0.5):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO long_term_memory
            (memory_key, content, importance, access_count, created_at, last_accessed)
            VALUES (?, ?, ?, COALESCE((SELECT access_count FROM long_term_memory WHERE memory_key=?), 0), ?, ?)
        """, (key, content, importance, key, now, now))
        conn.commit()
        conn.close()

    def recall_long_term(self, key: str = None, limit: int = 5) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if key:
            cursor.execute(
                "SELECT memory_key, content, importance FROM long_term_memory WHERE memory_key=?",
                (key,)
            )
        else:
            cursor.execute(
                """SELECT memory_key, content, importance FROM long_term_memory
                   ORDER BY importance DESC, last_accessed DESC LIMIT ?""",
                (limit,)
            )

        rows = cursor.fetchall()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in rows:
            cursor.execute(
                "UPDATE long_term_memory SET access_count = access_count + 1, last_accessed = ? WHERE memory_key = ?",
                (now, row[0])
            )
        conn.commit()
        conn.close()

        return [{"key": r[0], "content": r[1], "importance": r[2]} for r in rows]

    def forget_long_term(self, key: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM long_term_memory WHERE memory_key=?", (key,))
        conn.commit()
        conn.close()

    def decay_memories(self, threshold: float = 0.1):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.now()

        cursor.execute("SELECT id, importance, last_accessed FROM long_term_memory")
        rows = cursor.fetchall()

        for mem_id, importance, last_accessed in rows:
            if last_accessed is None:
                continue
            try:
                last_dt = datetime.strptime(last_accessed, "%Y-%m-%d %H:%M:%S")
                days_since_access = (now - last_dt).days
                decay = days_since_access / 7 * 0.1
                new_importance = importance - decay

                if new_importance < threshold:
                    cursor.execute("DELETE FROM long_term_memory WHERE id=?", (mem_id,))
                else:
                    cursor.execute(
                        "UPDATE long_term_memory SET importance=? WHERE id=?",
                        (new_importance, mem_id)
                    )
            except (ValueError, TypeError):
                pass

        conn.commit()
        conn.close()

    # -------------------- 3. 情景记忆 (经验学习) --------------------
    def save_episodic(
        self, task: str, approach: str, result: str,
        reflection: str, success: bool, tags: List[str] = None
    ):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tags_json = json.dumps(tags or [], ensure_ascii=False)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO episodic_memory (task, approach, result, reflection, success, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (task, approach, result, reflection, 1 if success else 0, tags_json, now))
        conn.commit()
        conn.close()

        self.episodic_cache.append({
            "task": task, "approach": approach, "result": result,
            "reflection": reflection, "success": success, "tags": tags or []
        })
        if len(self.episodic_cache) > 50:
            self.episodic_cache = self.episodic_cache[-50:]

    def recall_similar_episodes(self, task_desc: str, limit: int = 3) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT task, approach, result, reflection, success, tags FROM episodic_memory ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return []

        task_words = set(task_desc.lower().split())

        scored = []
        for row in rows:
            stored_task = row[0]
            stored_tags = json.loads(row[5]) if row[5] else []
            task_overlap = len(task_words & set(stored_task.lower().split()))
            tag_overlap = len(task_words & set(" ".join(stored_tags).lower().split()))
            score = task_overlap + tag_overlap * 2

            if score > 0:
                scored.append({
                    "task": row[0], "approach": row[1], "result": row[2],
                    "reflection": row[3], "success": bool(row[4]),
                    "tags": stored_tags, "score": score
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    # -------------------- 4. 工作记忆 --------------------
    def set_working(self, key: str, value: Any):
        self.working[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        return self.working.get(key, default)

    def clear_working(self):
        self.working = {}

    # -------------------- 5. 综合记忆接口 --------------------
    def get_context_for_task(self, task: str) -> str:
        parts = []

        long_term = self.recall_long_term(limit=5)
        if long_term:
            parts.append("【长期记忆/已学知识】")
            for m in long_term:
                parts.append(f"  • {m['key']}: {m['content'][:300]}")

        similar = self.recall_similar_episodes(task, limit=3)
        if similar:
            parts.append("\n【历史经验/情景记忆】")
            for i, ep in enumerate(similar, 1):
                parts.append(
                    f"  {i}. 任务: {ep['task'][:100]}\n"
                    f"     方法: {ep['approach'][:150]}\n"
                    f"     结果: {'成功' if ep['success'] else '失败'}\n"
                    f"     经验: {ep['reflection'][:200]}"
                )

        if self.working:
            parts.append("\n【当前工作状态】")
            for k, v in self.working.items():
                v_str = str(v)[:200]
                parts.append(f"  • {k}: {v_str}")

        return "\n".join(parts) if parts else ""

    def learn_from_interaction(self, user_input: str, agent_response: str, success: bool):
        self.add_short_term("user", user_input)
        self.add_short_term("assistant", agent_response)

        if success and len(user_input) > 20:
            extract_prompt = (
                f"从以下用户请求中提取1个关键知识点（一句话概括，不超过50字）：\n"
                f"用户: {user_input}\n助手: {agent_response[:200]}"
            )
            try:
                knowledge = chat_model.invoke(extract_prompt).content.strip()
                key = f"learned_{int(time.time())}"
                self.save_long_term(key, knowledge, importance=0.3)
            except Exception:
                pass

        if len(self.short_term) % 10 == 0:
            self.decay_memories()

        if len(self.short_term) >= self.short_term_max:
            summary = self.summarize_short_term()
            if summary:
                self.save_long_term(
                    f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    summary,
                    importance=0.4
                )
            self.short_term = self.short_term[-5:]


# 全局记忆系统实例
agent_memory = AgentMemory()
