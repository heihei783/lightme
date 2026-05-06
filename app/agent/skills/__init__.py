"""
Agent Skill 技能系统 —— 可动态注册/移除的技能，支持关键词匹配和 LLM 语义匹配

插件目录：
  下载的 skill 插件直接放入此目录下的 .py 文件，
  在文件中调用 skill_registry.register() 注册即可，系统启动时自动发现加载。
"""

import os
import importlib
from typing import Dict, List, Optional, Callable

from app.llm.chat_model import chat_model


class Skill:
    """
    技能定义 —— Agent 可调用的能力单元

    每个技能包含：
      - name: 技能名称 (唯一标识)
      - description: 技能描述 (用于 LLM 理解何时使用)
      - func: 技能的执行函数
      - keywords: 触发关键词列表 (用于快速匹配)
      - category: 技能分类 (search / execute / analyze / create)
    """

    def __init__(
        self, name: str, description: str, func: Callable,
        keywords: List[str] = None, category: str = "general"
    ):
        self.name = name
        self.description = description
        self.func = func
        self.keywords = keywords or []
        self.category = category

    def execute(self, **kwargs) -> str:
        """执行技能"""
        return self.func(**kwargs)

    def to_dict(self) -> Dict:
        """序列化为字典 (供 LLM 理解)"""
        return {
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "category": self.category
        }


class SkillRegistry:
    """
    技能注册中心 —— 管理所有 Agent 技能

    功能：
      1. register():    注册新技能 (随时添加)
      2. unregister():  移除技能
      3. match():       根据任务描述匹配最合适的技能
      4. list_all():    列出所有可用技能
      5. get_by_name(): 按名称获取技能
    """

    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill
        print(f"[SkillRegistry] [OK] 技能已注册: {skill.name} ({skill.category})")

    def unregister(self, name: str):
        if name in self._skills:
            del self._skills[name]
            print(f"[SkillRegistry] [X] 技能已移除: {name}")

    def get_by_name(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_all(self) -> List[Dict]:
        return [s.to_dict() for s in self._skills.values()]

    def match_by_keywords(self, task_desc: str) -> List[Skill]:
        task_lower = task_desc.lower()
        scored = []
        for skill in self._skills.values():
            score = 0
            for kw in skill.keywords:
                if kw.lower() in task_lower:
                    score += 1
            if score > 0:
                scored.append((skill, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored]

    def match_by_llm(self, task_desc: str) -> Optional[Skill]:
        if not self._skills:
            return None

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

    def match(self, task_desc: str, use_llm: bool = True) -> Optional[Skill]:
        keyword_matches = self.match_by_keywords(task_desc)
        if keyword_matches and len(task_desc) < 50:
            return keyword_matches[0]

        if use_llm:
            return self.match_by_llm(task_desc)

        return keyword_matches[0] if keyword_matches else None


# 全局技能注册中心实例
skill_registry = SkillRegistry()


def _register_default_skills():
    """注册系统内置的默认技能"""
    from app.agent.tools import (
        search_knowledge_base, execute_python_code,
        read_file_content, write_file_content, execute_shell_command
    )

    skill_registry.register(Skill(
        name="knowledge_search",
        description="在本地知识库中搜索文档和信息",
        func=search_knowledge_base,
        keywords=["搜索", "查找", "知识库", "检索", "搜索知识", "查资料", "search", "find"],
        category="search"
    ))

    skill_registry.register(Skill(
        name="python_executor",
        description="执行 Python 代码，用于计算、数据处理、自动化任务",
        func=execute_python_code,
        keywords=["执行", "运行", "python", "代码", "计算", "处理数据", "编程", "code", "run"],
        category="execute"
    ))

    skill_registry.register(Skill(
        name="file_reader",
        description="读取项目中的文件内容",
        func=read_file_content,
        keywords=["读取", "打开", "查看", "文件", "read", "open", "file"],
        category="search"
    ))

    skill_registry.register(Skill(
        name="file_writer",
        description="将内容写入文件，可用于创建或覆盖文件",
        func=write_file_content,
        keywords=["写入", "保存", "创建文件", "写文件", "write", "save", "create"],
        category="create"
    ))

    skill_registry.register(Skill(
        name="shell_executor",
        description="执行 Shell 命令，用于系统操作和脚本执行",
        func=execute_shell_command,
        keywords=["命令", "shell", "终端", "cmd", "执行命令", "terminal", "bash", "command"],
        category="execute"
    ))


def _load_plugins():
    """
    自动发现并加载 skills/ 目录下的所有 skill 插件。

    原理很简单：
      - 遍历本目录下不以下划线开头的 .py 文件
      - 挨个 import 进来
      - import 时模块顶层的 skill_registry.register() 会自动执行，技能就注册好了

    所以你只需要把插件 .py 文件丢进这个目录，启动时就会被自动 import，
    不需要手动在任何地方写 import 语句，这就是"自动加载"的全部含义。
    """
    _current_dir = os.path.dirname(__file__)
    for filename in sorted(os.listdir(_current_dir)):
        if filename.startswith("_") or not filename.endswith(".py"):
            continue
        module_name = filename[:-3]  # 去掉 .py 后缀
        try:
            importlib.import_module(f"app.agent.skills.{module_name}")
            print(f"[SkillRegistry] 已加载插件模块: {module_name}")
        except Exception as e:
            print(f"[SkillRegistry] [WARN] 加载插件失败 ({module_name}): {e}")


# === 启动：注册内置技能 + 自动发现加载插件 ===
_register_default_skills()
_load_plugins()
