"""
Agent Skill 技能系统 —— Markdown 技能定义与注册

技能 .md 文件放在此目录下，系统启动时自动发现并加载。
"""

import os

from app.agent.skill_loader import (
    SkillDef,
    Skill,
    SkillRegistry,
    parse_skill_md,
    scan_skill_files,
    load_skills_from_directory,
)

# 全局技能注册中心实例 (保持变量名不变以兼容现有代码)
skill_registry = SkillRegistry()

# === 启动：自动加载当前目录下所有 .md 技能文件 ===
_current_dir = os.path.dirname(os.path.abspath(__file__))
count = load_skills_from_directory(_current_dir, skill_registry)
print(f"[SkillRegistry] 已从 skills/ 加载 {count} 个技能定义 (.md)")
