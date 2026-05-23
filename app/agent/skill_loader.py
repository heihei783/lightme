"""
Skill 加载器 —— Markdown 技能定义的加载、解析与匹配
====================================================

技能 .md 文件放在 app/agent/skills/ 目录下，系统启动时自动扫描加载。
每个 .md 文件定义一套工作流指南，告诉 LLM 如何组合已有工具完成任务。

格式：
  # Skill: <name>
  ## Description
  ...
  ## Category
  <search|execute|analyze|create|general>
  ## Trigger
  - keyword1
  ## Instructions
  1. Step one...
  ## Notes
  ...
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ====================================================================
# 1. SkillDef 数据类
# ====================================================================

@dataclass
class SkillDef:
    """技能定义 —— 从 .md 文件解析出的技能指令文档"""
    name: str
    description: str = ""
    category: str = "general"
    keywords: List[str] = field(default_factory=list)
    instructions: str = ""
    notes: str = ""
    source_file: str = ""
    tool_module: str = ""  # 技能的 Python 工具模块路径，如 "app.agent.skill_code.firecrawl"

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "category": self.category,
            "instructions": self.instructions,
        }

    def has_tools(self) -> bool:
        return bool(self.tool_module)


# 向后兼容别名
Skill = SkillDef


# ====================================================================
# 2. Markdown 解析器
# ====================================================================

def parse_skill_md(filepath: str) -> Optional[SkillDef]:
    """解析单个 .md 技能文件，返回 SkillDef 或 None"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        print(f"[SkillLoader] [WARN] 无法读取 {filepath}: {e}")
        return None

    match = re.search(r'^#\s+Skill:\s*(.+)$', text, re.MULTILINE)
    if not match:
        print(f"[SkillLoader] [WARN] {filepath} 缺少 '# Skill: <name>' 标题，跳过")
        return None

    name = match.group(1).strip()
    description = ""
    category = "general"
    keywords: List[str] = []
    instructions = ""
    notes = ""
    tool_module = ""

    current_section = None
    section_lines: List[str] = []

    def flush_section():
        nonlocal description, category, instructions, notes, tool_module
        if not current_section or not section_lines:
            return
        body = "\n".join(section_lines).strip()
        sec = current_section.lower()
        if sec == "description":
            description = body
        elif sec == "category":
            category = body.split("\n")[0].strip().lower()
        elif sec == "trigger":
            for line in section_lines:
                kw = line.strip().lstrip("- ").strip()
                if kw:
                    keywords.append(kw)
        elif sec == "instructions":
            instructions = body
        elif sec == "notes":
            notes = body
        elif sec in ("toolmodule", "tool_module"):
            tool_module = body.split("\n")[0].strip()

    for line in text.split("\n"):
        section_match = re.match(r'^##\s+(.+)$', line)
        if section_match:
            flush_section()
            current_section = section_match.group(1).strip()
            section_lines = []
        elif current_section:
            section_lines.append(line)

    flush_section()

    return SkillDef(
        name=name,
        description=description,
        category=category,
        keywords=keywords,
        instructions=instructions,
        notes=notes,
        source_file=os.path.basename(filepath),
        tool_module=tool_module,
    )


# ====================================================================
# 3. 目录扫描
# ====================================================================

def scan_skill_files(skills_dir: str) -> List[SkillDef]:
    """扫描目录下所有 .md 文件，返回解析成功的 SkillDef 列表"""
    skills: List[SkillDef] = []
    if not os.path.isdir(skills_dir):
        return skills
    for filename in sorted(os.listdir(skills_dir)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(skills_dir, filename)
        skill = parse_skill_md(filepath)
        if skill:
            print(f"[SkillLoader] [OK] 已加载: {skill.name} ← {filename}")
            skills.append(skill)
    return skills


# ====================================================================
# 4. 技能注册中心
# ====================================================================

class SkillRegistry:
    """技能注册中心 —— 管理所有 Agent 技能"""

    def __init__(self):
        self._skills: Dict[str, SkillDef] = {}

    def register(self, skill: SkillDef):
        self._skills[skill.name] = skill
        print(f"[SkillRegistry] [OK] 技能已注册: {skill.name} ({skill.category})")

    def unregister(self, name: str):
        if name in self._skills:
            del self._skills[name]
            print(f"[SkillRegistry] [X] 技能已移除: {name}")

    def get_by_name(self, name: str) -> Optional[SkillDef]:
        return self._skills.get(name)

    def get_instructions(self, name: str) -> Optional[str]:
        """获取技能的完整指令文本，用于注入 executor system prompt"""
        skill = self._skills.get(name)
        if skill and skill.instructions:
            return skill.instructions
        return None

    def list_all(self) -> List[Dict]:
        return [s.to_dict() for s in self._skills.values()]

    def match_by_keywords(self, task_desc: str) -> List[SkillDef]:
        task_lower = task_desc.lower()
        scored = []
        for skill in self._skills.values():
            score = sum(1 for kw in skill.keywords if kw.lower() in task_lower)
            if score > 0:
                scored.append((skill, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored]

    def match_by_llm(self, task_desc: str) -> Optional[SkillDef]:
        if not self._skills:
            return None
        from app.llm.chat_model import chat_model

        skills_info = "\n".join([
            f"- {s.name}: {s.description} (关键词: {', '.join(s.keywords)})"
            for s in self._skills.values()
        ])

        prompt = (
            f"你是一个技能匹配器。根据任务描述，从可用技能中选择最合适的一个。\n\n"
            f"可用技能：\n{skills_info}\n\n"
            f"任务描述：{task_desc}\n\n"
            f"请只返回最合适的技能名称 (只返回技能名，不要其他内容)。"
            f"如果没有合适的技能，返回 'none'。"
        )

        try:
            response = chat_model.invoke(prompt).content.strip().lower()
            if response == "none":
                return None
            for name in self._skills:
                if name.lower() in response:
                    return self._skills[name]
            return None
        except Exception:
            keyword_matches = self.match_by_keywords(task_desc)
            return keyword_matches[0] if keyword_matches else None

    def match(self, task_desc: str, use_llm: bool = True) -> Optional[SkillDef]:
        """根据任务描述匹配最合适的技能。优先使用 LLM 语义理解，失败时回退关键词。"""
        if use_llm:
            result = self.match_by_llm(task_desc)
            if result:
                return result
        # LLM 失败或无结果 → 回退到关键词匹配
        keyword_matches = self.match_by_keywords(task_desc)
        return keyword_matches[0] if keyword_matches else None


# ====================================================================
# 5. 批量加载
# ====================================================================

def load_skills_from_directory(skills_dir: str, registry: SkillRegistry) -> int:
    """扫描目录下所有 .md 文件并注册到 registry。返回加载数量。"""
    skills = scan_skill_files(skills_dir)
    for skill in skills:
        registry.register(skill)
    return len(skills)


# ====================================================================
# 6. 技能工具加载
# ====================================================================

def get_skill_tools(skill: SkillDef) -> List:
    """根据 SkillDef 的 tool_module 字段，动态导入对应模块并返回 TOOLS 列表。

    tool_module 示例: "app.agent.skill_code.firecrawl"
    该模块必须导出 TOOLS (List[BaseTool])。
    """
    if not skill.tool_module:
        return []
    try:
        import importlib
        mod = importlib.import_module(skill.tool_module)
        if hasattr(mod, "TOOLS"):
            tools = mod.TOOLS
            if tools:
                return list(tools)
    except ImportError as e:
        print(f"[SkillLoader] [WARN] 无法导入 {skill.tool_module}: {e}")
    except Exception as e:
        print(f"[SkillLoader] [WARN] 加载 {skill.tool_module} 工具出错: {e}")
    return []
