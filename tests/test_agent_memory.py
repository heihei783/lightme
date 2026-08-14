import sqlite3
import tempfile
import unittest
from contextvars import Context
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.memory import AgentMemory


class AgentMemoryTest(unittest.TestCase):
    def make_memory(self, directory: str) -> AgentMemory:
        return AgentMemory(db_path=f"{directory}/memory.db")

    def test_legacy_database_is_migrated_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = f"{tmp}/memory.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE long_term_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_key TEXT UNIQUE,
                    content TEXT,
                    importance REAL,
                    access_count INTEGER,
                    created_at TEXT,
                    last_accessed TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE episodic_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT,
                    approach TEXT,
                    result TEXT,
                    reflection TEXT,
                    success INTEGER,
                    tags TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO long_term_memory
                    (memory_key, content, importance, access_count, created_at, last_accessed)
                VALUES ('legacy', '保留旧记忆', 0.7, 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                """
            )
            conn.commit()
            conn.close()

            memory = AgentMemory(db_path=db_path)

            conn = sqlite3.connect(db_path)
            long_columns = {row[1] for row in conn.execute("PRAGMA table_info(long_term_memory)")}
            episode_columns = {row[1] for row in conn.execute("PRAGMA table_info(episodic_memory)")}
            conn.close()
            self.assertIn("last_decayed", long_columns)
            self.assertIn("fingerprint", episode_columns)
            self.assertEqual(memory.recall_long_term("legacy")[0]["content"], "保留旧记忆")

    def test_long_term_search_prefers_relevant_chinese_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.save_long_term("cooking", "番茄炒蛋需要先炒鸡蛋", importance=0.8)
            memory.save_long_term(
                "agent_memory_recall",
                "代理记忆召回应结合任务相关性与重要度",
                importance=0.5,
            )

            results = memory.search_long_term("请优化代理记忆的召回排序")

            self.assertEqual(results[0]["key"], "agent_memory_recall")
            self.assertNotIn("cooking", {item["key"] for item in results})

    def test_high_importance_memory_can_be_recalled_globally(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.save_long_term("user_preference", "用户要求所有回答使用中文", importance=0.95)
            memory.save_long_term("test_failure", "Python 测试失败时先检查异常堆栈", importance=0.2)

            results = memory.search_long_term("分析 Python 单元测试失败原因")

            self.assertIn("user_preference", {item["key"] for item in results})
            self.assertEqual(results[0]["key"], "test_failure")

    def test_long_term_upsert_preserves_identity_and_access_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.save_long_term("preference", "偏好简洁回答", importance=0.7)
            memory.recall_long_term("preference")

            conn = sqlite3.connect(memory.db_path)
            before = conn.execute(
                "SELECT id, created_at, access_count FROM long_term_memory WHERE memory_key = 'preference'"
            ).fetchone()
            conn.close()

            memory.save_long_term("preference", "偏好简洁且使用中文", importance=0.4)

            conn = sqlite3.connect(memory.db_path)
            after = conn.execute(
                "SELECT id, content, importance, created_at, access_count FROM long_term_memory "
                "WHERE memory_key = 'preference'"
            ).fetchone()
            conn.close()
            self.assertEqual(after[0], before[0])
            self.assertEqual(after[1], "偏好简洁且使用中文")
            self.assertEqual(after[2], 0.7)
            self.assertEqual(after[3], before[1])
            self.assertEqual(after[4], before[2])

    def test_decay_is_incremental_instead_of_reapplying_full_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.save_long_term("old", "旧知识", importance=0.5)
            old_time = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(memory.db_path)
            conn.execute(
                "UPDATE long_term_memory SET last_accessed = ?, last_decayed = ? WHERE memory_key = 'old'",
                (old_time, old_time),
            )
            conn.commit()
            conn.close()

            memory.decay_memories(threshold=0.1, weekly_rate=0.05)
            conn = sqlite3.connect(memory.db_path)
            first = conn.execute(
                "SELECT importance FROM long_term_memory WHERE memory_key = 'old'"
            ).fetchone()[0]
            conn.close()

            memory.decay_memories(threshold=0.1, weekly_rate=0.05)
            conn = sqlite3.connect(memory.db_path)
            second = conn.execute(
                "SELECT importance FROM long_term_memory WHERE memory_key = 'old'"
            ).fetchone()[0]
            conn.close()
            self.assertAlmostEqual(first, 0.4, places=3)
            self.assertAlmostEqual(second, first, places=6)

    def test_episodic_memory_supports_chinese_recall_and_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            episode = {
                "task": "优化代理记忆检索",
                "approach": "加入中文二元组相关性排序",
                "result": "召回准确率提高",
                "reflection": "不能使用英文空格分词处理中文任务",
                "success": True,
                "tags": ["代理记忆", "中文检索"],
            }
            memory.save_episodic(**episode)
            memory.save_episodic(**episode)

            results = memory.recall_similar_episodes("改进中文代理记忆召回")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["task"], episode["task"])
            self.assertEqual(results[0]["occurrence_count"], 2)

    def test_corrupt_episode_tags_do_not_break_recall(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.save_episodic("分析测试失败", "读取日志", "定位异常", "先看堆栈", False)
            conn = sqlite3.connect(memory.db_path)
            conn.execute("UPDATE episodic_memory SET tags = '{broken json'")
            conn.commit()
            conn.close()

            results = memory.recall_similar_episodes("分析测试失败日志")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["tags"], [])

    def test_short_term_memory_is_session_scoped_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.add_short_term("user", "api_key=super-secret", session_id="session-a")
            memory.add_short_term("user", "普通内容", session_id="session-b")

            session_a = memory.get_short_term(session_id="session-a")
            session_b = memory.get_short_term(session_id="session-b")
            self.assertNotIn("super-secret", session_a[0]["content"])
            self.assertEqual(session_b[0]["content"], "普通内容")
            self.assertNotEqual(session_a, session_b)

    def test_unlabeled_known_token_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            token = "sk-abcdefghijklmnopqrstuvwxyz123456"
            memory.add_short_term("user", f"临时令牌是 {token}", session_id="session-a")

            content = memory.get_short_term(session_id="session-a")[0]["content"]

            self.assertNotIn(token, content)
            self.assertIn("[REDACTED TOKEN]", content)

    def test_working_memory_is_context_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.set_working("run_id", "parent")
            isolated = Context()
            isolated.run(memory.set_working, "run_id", "child")

            self.assertEqual(memory.get_working("run_id"), "parent")
            self.assertEqual(isolated.run(memory.get_working, "run_id"), "child")

    def test_sensitive_interaction_is_not_persisted_or_sent_for_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            with patch("app.agent.memory.chat_model") as model:
                memory.learn_from_interaction(
                    "请记住这个生产环境 api_key=super-secret-value 以后都使用它",
                    "好的",
                    success=True,
                    session_id="secret-session",
                )

            model.invoke.assert_not_called()
            self.assertEqual(memory.recall_long_term(), [])
            short_term = memory.get_short_term(session_id="secret-session")
            self.assertNotIn("super-secret-value", str(short_term))

    def test_learned_knowledge_uses_content_hash_for_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            extracted = SimpleNamespace(content="用户偏好使用中文并给出简洁结论")
            with patch("app.agent.memory.chat_model") as model:
                model.invoke.return_value = extracted
                for _ in range(2):
                    memory.learn_from_interaction(
                        "以后请始终使用中文回答，并把最重要的结论放在最前面。",
                        "明白，我会遵循这个稳定偏好。",
                        success=True,
                        session_id="preference-session",
                    )

            conn = sqlite3.connect(memory.db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM long_term_memory WHERE memory_key LIKE 'learned_%'"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)

    def test_task_context_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = self.make_memory(tmp)
            memory.save_long_term(
                "memory_optimization",
                "代理记忆优化" * 200,
                importance=0.7,
            )

            context = memory.get_context_for_task("优化代理记忆", max_chars=120)

            self.assertEqual(len(context), 120)
            self.assertTrue(context.endswith("…"))


if __name__ == "__main__":
    unittest.main()
