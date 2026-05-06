"""
LangGraph 通用 Agent 系统 — 兼容入口

功能模块已拆分到：
  - memory.py:     AgentMemory 记忆系统
  - tools.py:      工具定义 (@tool 函数 + DEFAULT_TOOLS)
  - skills.py:     Skill / SkillRegistry 技能系统
  - agent_graph.py: LangGraph 图节点、状态、路由、agent_graph 编译、对外接口
"""

# 从各子模块重导出，保持外部 import 路径兼容
from app.agent.memory import AgentMemory, agent_memory
from app.agent.tools import (
    search_knowledge_base,
    execute_python_code,
    read_file_content,
    write_file_content,
    execute_shell_command,
    DEFAULT_TOOLS,
)
from app.agent.skills import Skill, SkillRegistry, skill_registry
from app.agent.agent_graph import (
    AgentState,
    COORDINATOR_PROMPT,
    RESEARCHER_PROMPT,
    EXECUTOR_PROMPT,
    CRITIC_PROMPT,
    # 图节点
    planning_node,
    skill_select_node,
    executor_node,
    tool_executor_node,
    reflection_node,
    collaboration_node,
    finalize_node,
    # 路由
    should_continue_tools,
    should_continue_plan,
    decide_after_reflection,
    # 图
    agent_workflow,
    agent_graph,
    # 对外接口
    run_agent,
    add_skill,
    remove_skill,
    list_skills,
    get_memory,
)

__all__ = [
    "AgentMemory", "agent_memory",
    "search_knowledge_base", "execute_python_code", "read_file_content",
    "write_file_content", "execute_shell_command", "DEFAULT_TOOLS",
    "Skill", "SkillRegistry", "skill_registry",
    "AgentState",
    "COORDINATOR_PROMPT", "RESEARCHER_PROMPT", "EXECUTOR_PROMPT", "CRITIC_PROMPT",
    "planning_node", "skill_select_node", "executor_node", "tool_executor_node",
    "reflection_node", "collaboration_node", "finalize_node",
    "should_continue_tools", "should_continue_plan", "decide_after_reflection",
    "agent_workflow", "agent_graph",
    "run_agent", "add_skill", "remove_skill", "list_skills", "get_memory",
]


# ====================================================================
# 测试入口
# ====================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("LangGraph 增强 Agent 系统测试")
    print("=" * 60)

    # 测试1: 技能系统
    print("\n[*] 已注册技能:")
    for s in list_skills():
        print(f"  • {s['name']} [{s['category']}]: {s['description']}")

    # 测试2: 记忆系统
    print("\n[*] 记忆系统测试:")
    agent_memory.save_long_term("test_fact", "Python 是一种高级编程语言", importance=0.8)
    agent_memory.save_episodic(
        task="测试任务",
        approach="直接执行",
        result="成功",
        reflection="简单任务无需拆解",
        success=True,
        tags=["test"]
    )
    recalled = agent_memory.recall_long_term("test_fact")
    print(f"  长期记忆检索: {recalled}")

    similar = agent_memory.recall_similar_episodes("测试")
    print(f"  情景记忆检索: {len(similar)} 条相似经验")

    # 测试3: 技能匹配
    print("\n[*] 技能匹配测试:")
    skill = skill_registry.match("帮我搜索一下 Python 教程")
    if skill:
        print(f"  匹配到技能: {skill.name}")
    else:
        print("  未匹配到技能")

    print("\n[OK] Agent 系统就绪!")
