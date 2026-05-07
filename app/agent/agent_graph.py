"""
LangGraph 通用 Agent 系统
========================
功能：
  1. 任务拆解与规划 (Task Decomposition & Planning)
  2. Agent Skill 技能系统 (动态注册/移除技能)
  3. 多 Agent 协作机制 (Coordinator + 多 Worker)
  4. Agent Memory 记忆系统 (短期/长期/情景/工作记忆，模拟人类学习与记忆)
  5. 工具调用与命令执行
"""

import json
from typing import TypedDict, Annotated, List, Literal, Any, Dict, Callable

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from app.llm.chat_model import chat_model
from app.agent.memory import agent_memory
from app.agent.skills import skill_registry, Skill
from app.agent.tools import DEFAULT_TOOLS
from utils.file_handler import txt_loader
from utils.path_tool import get_abs_path


# ====================================================================
# Agent 状态定义
# ====================================================================

class AgentState(TypedDict):
    messages: Annotated[List, add_messages]
    plan: Dict[str, Any]
    memory_context: str
    collaboration_log: List[str]
    active_skills: List[str]
    final_output: str
    tool_iterations: int
    subtask_retries: int


# ====================================================================
# 多 Agent 角色的 System Prompt
# ====================================================================

COORDINATOR_PROMPT = """你是一个智能协调者 (Coordinator Agent)，负责统筹整个任务执行流程。

你的职责：
1. **分析任务**：理解用户的需求，判断任务的复杂度和类型
2. **制定计划**：将复杂任务拆解为可执行的子任务序列
3. **分配任务**：根据子任务类型，分配给最合适的执行者或技能
4. **监控进度**：跟踪每个子任务的完成状态
5. **汇总结果**：将所有子任务的结果整合为完整回答

工作原则：
- 对于简单任务（单一问题），直接回答即可，无需拆解
- 对于复杂任务（多步骤、需要多种工具），先规划再逐步执行
- 每完成一个子任务，评估是否需要调整计划
- 最终输出要完整、准确、有条理"""

RESEARCHER_PROMPT = """你是一个专业研究者 (Researcher Agent)，负责信息检索和知识查询。

你的专长：
1. **联网搜索**：使用 web_search 工具获取最新信息
2. **文件分析**：使用 read_file_content 工具阅读和分析文件
3. **信息整理**：将检索到的信息结构化、去重、提炼关键点

工作原则：
- 多角度检索：用不同关键词进行多次搜索
- 去伪存真：对检索结果进行交叉验证
- 简洁汇报：以结构化的格式呈现检索结果"""

EXECUTOR_PROMPT = """你是一个专业执行者 (Executor Agent)，负责实际的代码执行和命令操作。

你的专长：
1. **Python 编程**：使用 execute_python_code 工具执行 Python 代码
2. **Shell 操作**：使用 execute_shell_command 工具执行系统命令
3. **文件操作**：使用 read_file_content 和 write_file_content 操作文件

工作原则：
- 安全第一：执行任何操作前评估风险
- 结果验证：执行后检查输出是否正确
- 错误处理：遇到错误时分析原因并提供解决建议"""

CRITIC_PROMPT = """你是一个专业评审者 (Critic Agent)，负责审查执行结果并提供反馈。

你的职责：
1. **质量检查**：评估执行结果的准确性、完整性和可用性
2. **错误发现**：指出结果中的错误、遗漏或不一致之处
3. **改进建议**：提出具体的优化方向
4. **最终确认**：判断任务是否真正完成，是否可以交付给用户

工作原则：
- 客观公正：基于事实和标准进行评审
- 建设性：不仅指出问题，还提供解决方案
- 简洁：评审意见直接明了，不拖泥带水"""


# ====================================================================
# System Prompt 构建
# ====================================================================

def _get_personality_prompt() -> str:
    """仅在最终面向用户输出时使用的人格 prompt"""
    try:
        return txt_loader(
            get_abs_path(r"app\llm\prompts\chat_prompt.txt")
        )[0].page_content
    except Exception:
        return "你是一个智能助手。"


def _get_system_prompt(role: str = "coordinator") -> str:
    """内部节点使用的理性系统 prompt，不含人格设定"""
    role_prompts = {
        "coordinator": COORDINATOR_PROMPT,
        "researcher": RESEARCHER_PROMPT,
        "executor": EXECUTOR_PROMPT,
        "critic": CRITIC_PROMPT,
    }

    role_prompt = role_prompts.get(role, COORDINATOR_PROMPT)

    skills_info = "\n".join([
        f"  - {s['name']}: {s['description']}"
        for s in skill_registry.list_all()
    ])

    tools_info = "\n".join([
        f"  - {t.name}: {(t.description or '').split(chr(10))[0][:100]}"
        for t in DEFAULT_TOOLS
    ])

    return (
        f"=== 角色职责 ===\n{role_prompt}\n\n"
        f"=== 可用技能 ===\n{skills_info}\n\n"
        f"=== 可用工具 ===\n{tools_info}\n"
    )


# ====================================================================
# 图节点定义
# ====================================================================

# -------------------- 节点1: 规划节点 --------------------
def planning_node(state: AgentState) -> AgentState:
    last_msg = state["messages"][-1]
    user_request = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    memory_ctx = agent_memory.get_context_for_task(user_request)
    agent_memory.set_working("current_task", user_request)

    complexity_prompt = (
        f"判断以下任务的复杂度，只返回一个词 (simple/medium/complex)：\n\n"
        f"任务: {user_request}\n\n"
        f"标准: simple=可以直接回答 | medium=需要1-3步 | complex=需要多步+工具"
    )
    try:
        complexity = chat_model.invoke(complexity_prompt).content.strip().lower()
        if complexity not in ("simple", "medium", "complex"):
            complexity = "medium"
    except Exception:
        complexity = "medium"

    if complexity == "simple":
        plan = {
            "goal": user_request[:100],
            "complexity": "simple",
            "subtasks": [{"id": 1, "desc": user_request, "skill": None, "depends_on": []}],
            "current_subtask": 1,
            "estimated_steps": 1
        }
    else:
        skills_available = skill_registry.list_all()
        skills_desc = "\n".join([
            f"  - {s['name']} ({s['category']}): {s['description']}"
            for s in skills_available
        ])

        plan_prompt = (
            f"你需要将以下用户任务拆解为可执行的子任务序列。\n\n"
            f"用户任务: {user_request}\n\n"
            f"可用技能:\n{skills_desc}\n\n"
            f"相关记忆:\n{memory_ctx if memory_ctx else '无相关记忆'}\n\n"
            f"请输出 JSON 格式的执行计划 (不要包含其他内容)：\n"
            f'{{"goal": "目标描述", "subtasks": ['
            f'{{"id": 1, "desc": "子任务1描述", "skill": "推荐技能名或null", "depends_on": []}},'
            f'{{"id": 2, "desc": "子任务2描述", "skill": "推荐技能名或null", "depends_on": [1]}}'
            f'], "estimated_steps": N}}'
        )

        try:
            response = chat_model.invoke(plan_prompt).content.strip()
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]
            plan = json.loads(response)
            plan["complexity"] = complexity
            plan["current_subtask"] = 1
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"[Planning] JSON 解析失败: {e}，使用默认计划")
            plan = {
                "goal": user_request[:100],
                "complexity": complexity,
                "subtasks": [{"id": 1, "desc": user_request, "skill": None, "depends_on": []}],
                "current_subtask": 1,
                "estimated_steps": 1
            }

    agent_memory.set_working("plan", plan)

    plan_summary = (
        f"[Plan] **任务规划完成**\n"
        f"  目标: {plan.get('goal', 'N/A')}\n"
        f"  复杂度: {plan.get('complexity', 'N/A')}\n"
        f"  子任务数: {len(plan.get('subtasks', []))}\n"
        f"  预计步骤: {plan.get('estimated_steps', 'N/A')}"
    )
    for st in plan.get("subtasks", []):
        skill_info = f" [技能: {st['skill']}]" if st.get("skill") else ""
        plan_summary += f"\n    {st['id']}. {st['desc'][:60]}{skill_info}"

    return {
        "plan": plan,
        "memory_context": memory_ctx,
        "messages": [AIMessage(content=plan_summary)],
        "final_output": ""
    }


# -------------------- 节点2: 技能选择节点 --------------------
def skill_select_node(state: AgentState) -> AgentState:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return state

    current_task = subtasks[current_idx - 1]
    task_desc = current_task.get("desc", "")

    if current_task.get("skill"):
        skill = skill_registry.get_by_name(current_task["skill"])
        if skill:
            agent_memory.set_working("current_skill", skill.name)
            return {
                "active_skills": [skill.name],
                "tool_iterations": 0,
                "messages": [AIMessage(content=f"[Tool] 选择技能: {skill.name} — {skill.description}")]
            }

    matched_skill = skill_registry.match(task_desc)
    if matched_skill:
        agent_memory.set_working("current_skill", matched_skill.name)
        return {
            "active_skills": [matched_skill.name],
            "tool_iterations": 0,
            "messages": [AIMessage(content=f"[Tool] 自动匹配技能: {matched_skill.name} — {matched_skill.description}")]
        }

    agent_memory.set_working("current_skill", "general_llm")
    return {
        "active_skills": ["general_llm"],
        "tool_iterations": 0,
        "messages": [AIMessage(content="[Tool] 使用通用 LLM 能力处理任务")]
    }


MAX_TOOL_ITERATIONS = 8
MAX_SUBTASK_RETRIES = 3


# -------------------- 节点3: 执行节点 --------------------
def executor_node(state: AgentState) -> AgentState:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return state

    tool_iterations = state.get("tool_iterations", 0)

    if tool_iterations >= MAX_TOOL_ITERATIONS:
        return {
            "messages": [AIMessage(
                content=f"[CircuitBreaker] 工具调用已达上限 ({MAX_TOOL_ITERATIONS}次)，"
                        f"将基于已有结果继续。子任务: {subtasks[current_idx - 1].get('desc', '')}"
            )]
        }

    current_task = subtasks[current_idx - 1]
    task_desc = current_task.get("desc", "")

    # 始终绑定工具——LLM 自行决定是否调用
    model_with_tools = chat_model.bind_tools(DEFAULT_TOOLS)

    memory_ctx = state.get("memory_context", "")
    system_prompt = _get_system_prompt("executor") + (
        f"\n\n当前子任务: {task_desc}\n"
        f"请专注于完成这个子任务，完成后给出明确的执行结果。"
    )

    messages = [SystemMessage(content=system_prompt)]
    if memory_ctx:
        messages.append(SystemMessage(content=f"相关背景知识：\n{memory_ctx}"))
    messages.extend(state["messages"][-5:])

    try:
        response = model_with_tools.invoke(messages)
    except Exception as e:
        response = AIMessage(content=f"执行出错: {str(e)}")

    agent_memory.set_working(f"subtask_{current_idx}_result", response.content)

    return {"messages": [response], "tool_iterations": tool_iterations + 1}


# -------------------- 节点4: 工具执行节点 --------------------
tool_executor_node = ToolNode(DEFAULT_TOOLS)


# -------------------- 节点5: 反思节点 --------------------
def reflection_node(state: AgentState) -> AgentState:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return state

    current_task = subtasks[current_idx - 1]
    task_desc = current_task.get("desc", "")

    recent_msgs = state["messages"][-3:]
    result_text = "\n".join([
        m.content[:300] if hasattr(m, "content") else str(m)[:300]
        for m in recent_msgs
    ])

    critic_prompt = (
        f"评审以下子任务的执行结果：\n\n"
        f"子任务: {task_desc}\n"
        f"执行结果: {result_text}\n\n"
        f"请回答：1) 结果是否满足子任务要求？(是/否)\n"
        f"2) 如果不足，需要如何改进？\n"
        f"3) 这个子任务是否真正完成？(完成/需要重试/需要调整)\n\n"
        f"请简洁回复。"
    )

    try:
        critic_response = chat_model.invoke(critic_prompt).content.strip()
    except Exception:
        critic_response = "完成"

    subtask_retries = state.get("subtask_retries", 0)

    if "需要重试" in critic_response:
        if subtask_retries >= MAX_SUBTASK_RETRIES:
            status = "completed"
            critic_response = f"[已达最大重试次数 {MAX_SUBTASK_RETRIES}] " + critic_response
        else:
            status = "retry"
            subtask_retries += 1
    elif "需要调整" in critic_response:
        if subtask_retries >= MAX_SUBTASK_RETRIES:
            status = "completed"
            critic_response = f"[已达最大重试次数 {MAX_SUBTASK_RETRIES}] " + critic_response
        else:
            status = "adjust"
            subtask_retries += 1
    else:
        status = "completed"
        subtask_retries = 0

    current_task["status"] = status
    current_task["result"] = result_text[:500]
    current_task["critique"] = critic_response[:300]

    if status == "completed":
        plan["current_subtask"] = current_idx + 1

    is_success = status == "completed"
    agent_memory.save_episodic(
        task=task_desc,
        approach=f"使用技能: {agent_memory.get_working('current_skill', 'general_llm')}",
        result=result_text[:300],
        reflection=critic_response[:300],
        success=is_success,
        tags=[agent_memory.get_working("current_skill", "general"), "subtask"]
    )

    reflection_msg = (
        f"[Review] **子任务 {current_idx} 评审**: {status}\n"
        f"  评审意见: {critic_response[:200]}"
    )

    return {
        "plan": plan,
        "subtask_retries": subtask_retries,
        "messages": [AIMessage(content=reflection_msg)]
    }


# -------------------- 节点6: 多 Agent 协作节点 --------------------
def collaboration_node(state: AgentState) -> AgentState:
    plan = state.get("plan", {})
    collaboration_log = state.get("collaboration_log", [])

    if plan.get("complexity") == "simple":
        collaboration_log.append("[Coordinator] 任务简单，无需多 Agent 协作")
        return {"collaboration_log": collaboration_log}

    user_request = agent_memory.get_working("current_task", "")
    plan_goal = plan.get("goal", user_request)

    collaboration_log.append(f"[Coordinator] 开始协调任务: {plan_goal[:100]}")

    research_needed = any(
        st.get("skill") in ("knowledge_search", "file_reader") or
        any(kw in st.get("desc", "") for kw in ["搜索", "查找", "读取", "检索"])
        for st in plan.get("subtasks", [])
    )

    if research_needed:
        collaboration_log.append("[Coordinator → Researcher] 委派信息检索任务")
        try:
            research_model = chat_model
            research_messages = [
                SystemMessage(content=_get_system_prompt("researcher")),
                HumanMessage(content=f"请检索以下相关信息: {plan_goal}")
            ]
            research_response = research_model.invoke(research_messages)
            collaboration_log.append(
                f"[Researcher → Coordinator] 检索完成: {research_response.content[:200]}..."
            )
            agent_memory.set_working("research_result", research_response.content)
        except Exception as e:
            collaboration_log.append(f"[Researcher] 检索出错: {e}")

    execute_needed = any(
        st.get("skill") in ("python_executor", "shell_executor", "file_writer") or
        any(kw in st.get("desc", "") for kw in ["执行", "运行", "写入", "创建"])
        for st in plan.get("subtasks", [])
    )

    if execute_needed:
        collaboration_log.append("[Coordinator → Executor] 委派执行任务")

    collaboration_log.append("[Coordinator → Critic] 请求质量评审")

    try:
        critic_model = chat_model
        critic_messages = [
            SystemMessage(content=_get_system_prompt("critic")),
            HumanMessage(content=(
                f"请评审以下任务执行情况:\n"
                f"目标: {plan_goal}\n"
                f"已完成子任务: {plan.get('current_subtask', 1) - 1}/{len(plan.get('subtasks', []))}\n"
                f"研究结果: {agent_memory.get_working('research_result', '无')[:300]}"
            ))
        ]
        critic_response = critic_model.invoke(critic_messages)
        collaboration_log.append(
            f"[Critic → Coordinator] 评审完成: {critic_response.content[:200]}..."
        )
        agent_memory.set_working("critic_feedback", critic_response.content)
    except Exception as e:
        collaboration_log.append(f"[Critic] 评审出错: {e}")

    collaboration_log.append("[Coordinator] 汇总所有 Agent 的输出")

    summary_parts = []
    for log in collaboration_log[-6:]:
        summary_parts.append(log)

    summary_msg = (
        "[Collab] **多 Agent 协作摘要**\n" +
        "\n".join(f"  {p}" for p in summary_parts)
    )

    return {
        "collaboration_log": collaboration_log,
        "messages": [AIMessage(content=summary_msg)]
    }


# -------------------- 节点7: 汇总输出节点 --------------------
def finalize_node(state: AgentState) -> AgentState:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])

    results_parts = []
    for st in subtasks:
        if st.get("result"):
            results_parts.append(f"[子任务{st['id']}]: {st['result'][:300]}")

    all_results = "\n\n".join(results_parts) if results_parts else "任务已直接完成"

    user_request = agent_memory.get_working("current_task", "")

    personality = _get_personality_prompt()

    summarize_prompt = (
        f"=== 人格设定（必须严格遵守） ===\n{personality}\n\n"
        f"=== 用户请求 ===\n{user_request}\n\n"
        f"=== 各子任务执行结果 ===\n{all_results}\n\n"
        f"请将以上所有结果整合为一个完整、连贯、有条理的最终回答。"
        f"必须使用人格设定中指定的语气、称呼和行为风格。"
    )

    try:
        final_response = chat_model.invoke(summarize_prompt)
        final_text = final_response.content
    except Exception:
        final_text = all_results

    is_success = len(results_parts) > 0
    agent_memory.learn_from_interaction(
        user_input=user_request,
        agent_response=final_text[:500],
        success=is_success
    )

    agent_memory.clear_working()

    return {
        "final_output": final_text,
        "messages": [AIMessage(content=final_text)]
    }


# ====================================================================
# 路由函数
# ====================================================================

def should_continue_tools(state: AgentState) -> Literal["tools", "reflect"]:
    last_msg = state["messages"][-1]
    tool_iterations = state.get("tool_iterations", 0)
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls and tool_iterations < MAX_TOOL_ITERATIONS:
        return "tools"
    return "reflect"


def should_continue_plan(state: AgentState) -> Literal["skill_select", "finalize"]:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return "finalize"
    if current_idx <= len(subtasks):
        current_task = subtasks[current_idx - 1]
        if current_task.get("status") == "retry":
            return "skill_select"
        if current_task.get("status") == "adjust":
            return "skill_select"
    return "skill_select"


def decide_after_reflection(state: AgentState) -> Literal["planning", "skill_select", "finalize"]:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return "finalize"
    current_task = subtasks[current_idx - 1]
    if current_task.get("status") == "adjust":
        return "planning"
    return "skill_select"


# ====================================================================
# 构建增强版 Agent 图
# ====================================================================

agent_workflow = StateGraph(AgentState)

agent_workflow.add_node("planning", planning_node)
agent_workflow.add_node("collaboration", collaboration_node)
agent_workflow.add_node("skill_select", skill_select_node)
agent_workflow.add_node("executor", executor_node)
agent_workflow.add_node("tools", tool_executor_node)
agent_workflow.add_node("reflection", reflection_node)
agent_workflow.add_node("finalize", finalize_node)

agent_workflow.set_entry_point("planning")

agent_workflow.add_edge("planning", "collaboration")

agent_workflow.add_conditional_edges(
    "collaboration",
    should_continue_plan,
    {"skill_select": "skill_select", "finalize": "finalize"}
)

agent_workflow.add_edge("skill_select", "executor")

agent_workflow.add_conditional_edges(
    "executor",
    should_continue_tools,
    {"tools": "tools", "reflect": "reflection"}
)

agent_workflow.add_edge("tools", "executor")

agent_workflow.add_conditional_edges(
    "reflection",
    decide_after_reflection,
    {"planning": "planning", "skill_select": "skill_select", "finalize": "finalize"}
)

agent_workflow.add_edge("finalize", END)

agent_graph = agent_workflow.compile()


# ====================================================================
# 对外接口
# ====================================================================

def run_agent(messages: List) -> str:
    initial_state = {
        "messages": messages,
        "plan": {},
        "memory_context": "",
        "collaboration_log": [],
        "active_skills": [],
        "final_output": "",
        "tool_iterations": 0,
        "subtask_retries": 0
    }

    result = agent_graph.invoke(
        initial_state,
        config={"recursion_limit": 100}
    )

    return result.get("final_output", "") or "抱歉，我没有处理好你的请求喵~"


def add_skill(name: str, description: str, func: Callable, keywords: List[str] = None, category: str = "general"):
    skill = Skill(name, description, func, keywords, category)
    skill_registry.register(skill)


def remove_skill(name: str):
    skill_registry.unregister(name)


def list_skills() -> List[Dict]:
    return skill_registry.list_all()


def get_memory():
    from app.agent.memory import AgentMemory
    return agent_memory
