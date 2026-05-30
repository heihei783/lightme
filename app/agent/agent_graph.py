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
from typing import TypedDict, Annotated, List, Literal, Any, Dict

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from app.llm.chat_model import chat_model
from app.agent.memory import agent_memory
from app.agent.skill_loader import SkillDef, get_skill_tools
from app.agent.skills import skill_registry
from app.agent.tools import DEFAULT_TOOLS
from utils.file_handler import txt_loader
from utils.path_tool import get_abs_path
from utils.console_emitter import console


# ====================================================================
# Agent 状态定义
# ====================================================================

class AgentState(TypedDict):
    messages: Annotated[List, add_messages]
    plan: Dict[str, Any]#任务规划结构
    memory_context: str#长期/情景记忆检索相关背景知识
    collaboration_log: List[str]#多 Agent 协作日志
    active_skills: List[str]#当前子任务匹配到的技能名列表
    final_output: str#最终输出结果
    tool_iterations: int#工具调用迭代次数
    subtask_retries: int#子任务重试次数


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
- 信任工具返回值：工具的返回结果已经包含成功/失败信息，无需反复验证
- 见好就收：工具返回成功结果后，直接基于该结果给出回复，不要重复调用
- 错误处理：遇到错误时分析原因并调整策略，但不重复执行已成功的操作"""

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

def _get_user_request_with_context(state: AgentState) -> tuple[str, str]:
    """
    从状态中提取用户请求 + 对话历史上下文。
    返回 (带上下文的完整请求文本, 对话历史摘要)
    """
    all_msgs = state["messages"]
    last_msg = all_msgs[-1]
    current = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    # 提取最近几轮对话作为上下文（排除当前消息）
    if len(all_msgs) > 1:
        history_parts = []
        for msg in all_msgs[-7:-1]:  # 当前消息之前最多 6 条
            role = "用户" if getattr(msg, "type", "") == "human" else "助手"
            content = msg.content if hasattr(msg, "content") else str(msg)
            history_parts.append(f"[{role}]: {content[:200]}")
        history_context = "\n".join(history_parts) if history_parts else ""
    else:
        history_context = ""

    # 拼接上下文：历史对话 + 当前请求
    if history_context:
        full_request = (
            f"对话历史:\n{history_context}\n\n"
            f"用户最新消息 (需要执行的任务): {current}"
        )
    else:
        full_request = current

    return full_request, history_context


# -------------------- 节点1: 规划节点 --------------------
def planning_node(state: AgentState) -> AgentState:
    print("\n" + "=" * 60)
    print("📋 [Planning] 规划节点 —— 分析任务复杂度并拆解子任务")
    print("=" * 60)

    user_request, history_context = _get_user_request_with_context(state)
    last_msg = state["messages"][-1]
    current_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    print(f"  用户请求: {current_text[:100]}")
    if history_context:
        print(f"  历史上下文: {len(history_context)} 字符")
    console.emit_log("Planning", f"收到任务: {current_text[:120]}")

    memory_ctx = agent_memory.get_context_for_task(user_request)
    agent_memory.set_working("current_task", current_text)

    if memory_ctx:
        print(f"  相关记忆: {memory_ctx[:80]}...")

    complexity_prompt = (
        f"判断以下任务的复杂度，只返回一个词 (simple/medium/complex)：\n\n"
        f"{user_request}\n\n"
        f"标准: simple=可以直接回答 | medium=需要1-3步 | complex=需要多步+工具\n"
        f"注意: 如果对话历史中包含具体操作细节，应将其纳入考量。"
    )
    try:
        complexity = chat_model.invoke(complexity_prompt).content.strip().lower()
        if complexity not in ("simple", "medium", "complex"):
            complexity = "medium"
    except Exception:
        complexity = "medium"

    print(f"  复杂度判定: {complexity}")
    console.emit_log("Planning", f"复杂度: {complexity}")

    if complexity == "simple":
        plan = {
            "goal": current_text[:100],
            "complexity": "simple",
            "subtasks": [{
                "id": 1,
                "desc": current_text if not history_context else f"{current_text}\n上下文: {history_context}",
                "skill": None,
                "depends_on": []
            }],
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
            f"{user_request}\n\n"
            f"可用技能:\n{skills_desc}\n\n"
            f"相关记忆:\n{memory_ctx if memory_ctx else '无相关记忆'}\n\n"
            f"重要: 如果对话历史中包含具体信息（如文件名、路径、命令等），必须将其应用到子任务中。\n"
            f"不要猜测或编造文件路径/名称，只使用用户在对话历史中明确提到的信息。\n\n"
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

    print(f"  子任务数: {len(plan.get('subtasks', []))}")
    subtask_list = []
    for i, st in enumerate(plan.get("subtasks", []), 1):
        skill_info = f" → 技能: {st['skill']}" if st.get("skill") else ""
        print(f"    [{i}] {st['desc'][:80]}{skill_info}")
        subtask_list.append(f"[{i}] {st['desc'][:60]}{skill_info}")
    console.emit_log("Planning", f"计划: {len(subtask_list)} 个子任务\n" + "\n".join(subtask_list))

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

    print("\n" + "-" * 40)
    print(f"🎯 [SkillSelect] 技能选择 —— 子任务 {current_idx}: {task_desc[:60]}")
    print("-" * 40)

    if current_task.get("skill"):
        skill = skill_registry.get_by_name(current_task["skill"])
        if skill:
            print(f"  规划推荐技能: {skill.name}")
            agent_memory.set_working("current_skill", skill.name)
            return {
                "active_skills": [skill.name],
                "tool_iterations": 0,
                "messages": [AIMessage(content=f"[Tool] 选择技能: {skill.name} — {skill.description}")]
            }
        print(f"  规划推荐技能 '{current_task['skill']}' 未找到，尝试自动匹配...")

    matched_skill = skill_registry.match(task_desc)
    if matched_skill:
        print(f"  匹配结果: {matched_skill.name} [{matched_skill.category}]")
        console.emit_log("SkillSelect", f"匹配技能: {matched_skill.name} [{matched_skill.category}]")
        agent_memory.set_working("current_skill", matched_skill.name)
        return {
            "active_skills": [matched_skill.name],
            "tool_iterations": 0,
            "messages": [AIMessage(content=f"[Tool] 自动匹配技能: {matched_skill.name} — {matched_skill.description}")]
        }

    print("  未匹配到技能，使用通用 LLM")
    agent_memory.set_working("current_skill", "general_llm")
    return {
        "active_skills": ["general_llm"],
        "tool_iterations": 0,
        "messages": [AIMessage(content="[Tool] 使用通用 LLM 能力处理任务")]
    }


MAX_TOOL_ITERATIONS = 8
MAX_SUBTASK_RETRIES = 3


def _safe_truncate_history(messages: List, max_count: int = 5) -> List:
    """从末尾取最近 N 条消息，保证 tool_calls / ToolMessage 完整配对。

    处理三种截断破坏:
      1. 第一条是孤立的 ToolMessage → 向前找回 AIMessage(tool_calls)
      2. 开头有不完整的 AIMessage(tool_calls) → 移除该 AIMessage + 孤儿 ToolMessages
      3. 尾部有孤立的 AIMessage(tool_calls) → 移除（最后一个有 tool_calls 但无 ToolMessage）
    """
    if len(messages) <= max_count:
        return messages

    start_idx = len(messages) - max_count

    # --- 反向修复: 孤立的 ToolMessage 向前找回 AIMessage(tool_calls) ---
    while start_idx > 0 and start_idx < len(messages):
        msg = messages[start_idx]
        if hasattr(msg, "tool_call_id") and msg.tool_call_id:
            start_idx -= 1
        else:
            break

    result = messages[start_idx:]

    # --- 正向修复: 删除头部不完整的 AIMessage(tool_calls) 组 ---
    while result:
        first = result[0]
        if hasattr(first, "tool_calls") and first.tool_calls:
            needed_ids = set()
            for tc in first.tool_calls:
                tc_id = tc["id"] if isinstance(tc, dict) else tc.id
                needed_ids.add(tc_id)
            found_ids = set()
            for m in result[1:]:
                if hasattr(m, "tool_call_id") and m.tool_call_id:
                    found_ids.add(m.tool_call_id)
                else:
                    break
            if not needed_ids.issubset(found_ids):
                result = result[1:]
                while result and hasattr(result[0], "tool_call_id") and result[0].tool_call_id:
                    result = result[1:]
                continue
        break

    # --- 尾部修复: 删除末尾孤立的 AIMessage(tool_calls) ---
    # 扫描所有 AIMessage(tool_calls)，确保其后都有对应 ToolMessage。
    # 从后往前扫描，删除孤立的 tool_calls 组。
    i = len(result) - 1
    while i >= 0:
        msg = result[i]
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            # 收集这个 AIMessage 的所有 tool_call_id
            needed_ids = set()
            for tc in msg.tool_calls:
                tc_id = tc["id"] if isinstance(tc, dict) else tc.id
                needed_ids.add(tc_id)
            # 在它之后查找匹配的 ToolMessage
            found_ids = set()
            for j in range(i + 1, len(result)):
                m = result[j]
                if hasattr(m, "tool_call_id") and m.tool_call_id:
                    found_ids.add(m.tool_call_id)
                else:
                    break  # 被非 ToolMessage 打断
            if not needed_ids.issubset(found_ids):
                # 不完整 → 删除这个 AIMessage + 它的孤儿 ToolMessages
                del result[i]
                while i < len(result) and hasattr(result[i], "tool_call_id") and result[i].tool_call_id:
                    del result[i]
                continue
        i -= 1

    return result


# -------------------- 节点3: 执行节点 --------------------
def executor_node(state: AgentState) -> AgentState:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return state

    tool_iterations = state.get("tool_iterations", 0)

    print("\n" + "-" * 40)
    print(f"⚡ [Executor] 执行节点 —— 子任务 {current_idx}/{len(subtasks)} | 工具调用 #{tool_iterations + 1}")
    print("-" * 40)

    if tool_iterations >= MAX_TOOL_ITERATIONS:
        print(f"  ⚠ 断路器熔断! 已达上限 {MAX_TOOL_ITERATIONS} 次")
        return {
            "messages": [AIMessage(
                content=f"[CircuitBreaker] 工具调用已达上限 ({MAX_TOOL_ITERATIONS}次)，"
                        f"将基于已有结果继续。子任务: {subtasks[current_idx - 1].get('desc', '')}"
            )]
        }

    current_task = subtasks[current_idx - 1]
    task_desc = current_task.get("desc", "")

    # 确定当前激活的技能 (排除 general_llm 占位符)
    active_skills = state.get("active_skills", [])
    skill_name = active_skills[0] if active_skills and active_skills[0] != "general_llm" else None
    active_skill = skill_registry.get_by_name(skill_name) if skill_name else None

    # 动态绑定工具: DEFAULT_TOOLS + 当前技能专属工具
    skill_tools = get_skill_tools(active_skill) if active_skill else []
    all_tools = list(DEFAULT_TOOLS) + skill_tools
    if skill_tools:
        print(f"  技能工具已加载: {[t.name for t in skill_tools]}")
    model_with_tools = chat_model.bind_tools(all_tools)

    memory_ctx = state.get("memory_context", "")
    system_prompt = _get_system_prompt("executor")

    # 注入当前选中技能的指令指南
    if skill_name:
        skill_instructions = skill_registry.get_instructions(skill_name)
        if skill_instructions:
            print(f"  技能指南已注入: {skill_name} ({len(skill_instructions)} 字符)")
            system_prompt += (
                f"\n\n=== 当前技能指南: {skill_name} ===\n"
                f"{skill_instructions}\n"
                f"请严格按照以上技能指南完成当前子任务。"
            )
        else:
            if active_skill:
                print(f"  使用技能: {skill_name} (无详细指令)")
                system_prompt += (
                    f"\n\n=== 当前技能: {skill_name} ===\n"
                    f"描述: {active_skill.description}\n"
                    f"请使用可用工具完成当前子任务。"
                )
        # Midscene 特殊提醒：每次 midscene_act 都是新浏览器，多步必须用 midscene_flow
        if skill_name == "midscene_interaction":
            system_prompt += (
                f"\n\n=== ⚠️ 关键提醒 ===\n"
                f"midscene_act 每次调用都会启动全新浏览器并在执行后关闭。\n"
                f"因此，如果你需要在一个网页上执行多个操作（如打开网站→输入搜索→点击按钮→查看结果），\n"
                f"绝对不能分多次调用 midscene_act！请使用 midscene_flow，将所有操作打包成 flow_json：\n"
                f'{{"actions":['
                f'{{"action":"navigate","url":"https://..."}},'
                f'{{"action":"type","locate":"搜索框","input":"关键词"}},'
                f'{{"action":"click","instruction":"点击搜索按钮"}},'
                f'{{"action":"wait","instruction":"3000"}},'
                f'{{"action":"query","instruction":"列出结果"}}'
                f']}}\n'
            )
    else:
        mode = "通用 LLM" if (active_skills and active_skills[0] == "general_llm") else "无技能"
        print(f"  模式: {mode}")
        print(f"  子任务: {task_desc[:80]}")

    system_prompt += (
        f"\n\n当前子任务: {task_desc}\n"
        f"请专注于完成这个子任务，完成后给出明确的执行结果。"
    )

    messages = [SystemMessage(content=system_prompt)]
    if memory_ctx:
        messages.append(SystemMessage(content=f"相关背景知识：\n{memory_ctx}"))
    messages.extend(_safe_truncate_history(state["messages"], max_count=5))

    try:
        response = model_with_tools.invoke(messages)
    except Exception as e:
        print(f"  ❌ LLM 调用出错: {e}")
        response = AIMessage(content=f"执行出错: {str(e)}")

    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            args_str = str(tc.get('args', {}))[:150]
            print(f"  🔧 调用工具: {tc['name']}({args_str})")
            console.emit_tool(tc['name'], args_str)
    else:
        print(f"  💬 LLM 回复: {response.content[:100]}...")
        console.emit_log("Executor", f"LLM 回复: {response.content[:150]}")

    agent_memory.set_working(f"subtask_{current_idx}_result", response.content)

    return {
        "messages": [response],
        "tool_iterations": tool_iterations + 1,
        "_active_skill_tools": skill_tools,
    }


# 收集所有技能专属工具，合并到 DEFAULT_TOOLS 中
# ToolNode 需要预先知道所有工具，动态注册会有 config 传递问题
_ALL_SKILL_TOOLS = []
for s in skill_registry.list_all():
    skill = skill_registry.get_by_name(s["name"])
    if skill and skill.has_tools():
        st = get_skill_tools(skill)
        if st:
            _ALL_SKILL_TOOLS.extend(st)
            print(f"  [Tools] 技能 '{s['name']}' 已注册 {len(st)} 个工具: {[t.name for t in st]}")

ALL_TOOLS = list(DEFAULT_TOOLS) + _ALL_SKILL_TOOLS
tool_executor_node = ToolNode(ALL_TOOLS)


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

    print("\n" + "-" * 40)
    print(f"🔍 [Reflection] 反思节点 —— 评审子任务 {current_idx}: {task_desc[:50]}")
    print("-" * 40)

    try:
        critic_response = chat_model.invoke(critic_prompt).content.strip()
    except Exception:
        critic_response = "完成"

    print(f"  Critic 评审: {critic_response[:100]}...")

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

    print(f"  判定: {status} | 重试次数: {subtask_retries}/{MAX_SUBTASK_RETRIES}")
    console.emit_log("Reflection", f"子任务 {current_idx} 评审: {status} (重试: {subtask_retries}/{MAX_SUBTASK_RETRIES})")

    if status == "completed":
        plan["current_subtask"] = current_idx + 1
        print(f"  ✅ 子任务完成，推进到子任务 {plan['current_subtask']}")
        console.emit_log("Reflection", f"子任务 {current_idx} 完成，推进到 {plan['current_subtask']}")

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

    print("\n" + "=" * 60)
    print("🤝 [Collaboration] 协作节点 —— 多 Agent 协调")
    print("=" * 60)

    if plan.get("complexity") == "simple":
        print("  复杂度: simple，跳过协作")
        collaboration_log.append("[Coordinator] 任务简单，无需多 Agent 协作")
        return {"collaboration_log": collaboration_log}

    print(f"  复杂度: {plan.get('complexity')}，启动多 Agent 协作")

    user_request = agent_memory.get_working("current_task", "")
    plan_goal = plan.get("goal", user_request)

    collaboration_log.append(f"[Coordinator] 开始协调任务: {plan_goal[:100]}")

    research_needed = any(
        st.get("skill") in ("web_searcher", "file_reader") or
        any(kw in st.get("desc", "") for kw in ["搜索", "查找", "读取", "检索"])
        for st in plan.get("subtasks", [])
    )

    if research_needed:
        print("  [Coordinator → Researcher] 委派信息检索任务")
        collaboration_log.append("[Coordinator → Researcher] 委派信息检索任务")
        try:
            research_model = chat_model
            research_messages = [
                SystemMessage(content=_get_system_prompt("researcher")),
                HumanMessage(content=f"请检索以下相关信息: {plan_goal}")
            ]
            research_response = research_model.invoke(research_messages)
            print(f"  [Researcher → Coordinator] 检索完成: {research_response.content[:80]}...")
            collaboration_log.append(
                f"[Researcher → Coordinator] 检索完成: {research_response.content[:200]}..."
            )
            agent_memory.set_working("research_result", research_response.content)
        except Exception as e:
            print(f"  [Researcher] 检索出错: {e}")
            collaboration_log.append(f"[Researcher] 检索出错: {e}")

    execute_needed = any(
        st.get("skill") in ("python_executor", "shell_executor", "file_writer") or
        any(kw in st.get("desc", "") for kw in ["执行", "运行", "写入", "创建"])
        for st in plan.get("subtasks", [])
    )

    if execute_needed:
        print("  [Coordinator → Executor] 委派执行任务")
        collaboration_log.append("[Coordinator → Executor] 委派执行任务")

    print("  [Coordinator → Critic] 请求质量评审")
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
        print(f"  [Critic → Coordinator] 评审完成: {critic_response.content[:80]}...")
        collaboration_log.append(
            f"[Critic → Coordinator] 评审完成: {critic_response.content[:200]}..."
        )
        agent_memory.set_working("critic_feedback", critic_response.content)
    except Exception as e:
        print(f"  [Critic] 评审出错: {e}")
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

    print("\n" + "=" * 60)
    print("📦 [Finalize] 汇总节点 —— 整合所有子任务结果，注入人格")
    print("=" * 60)
    print(f"  子任务完成数: {len(results_parts)}/{len(subtasks)}")
    console.emit_log("Finalize", f"汇总 {len(results_parts)}/{len(subtasks)} 个子任务结果")

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
        print(f"  最终回答: {final_text[:100]}...")
    except Exception:
        final_text = all_results
        print(f"  ⚠ LLM 调用失败，使用原始结果")

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
        print(f"  ⏩ 路由: executor → tools (第 {tool_iterations + 1} 次工具调用)")
        return "tools"
    print(f"  ⏩ 路由: executor → reflection")
    return "reflect"


def should_continue_plan(state: AgentState) -> Literal["skill_select", "finalize"]:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        print("  ⏩ 路由: collaboration → finalize (无子任务)")
        return "finalize"
    if current_idx <= len(subtasks):
        current_task = subtasks[current_idx - 1]
        if current_task.get("status") == "retry":
            print(f"  ⏩ 路由: collaboration → skill_select (重试子任务 {current_idx})")
            return "skill_select"
        if current_task.get("status") == "adjust":
            print(f"  ⏩ 路由: collaboration → skill_select (调整子任务 {current_idx})")
            return "skill_select"
    print(f"  ⏩ 路由: collaboration → skill_select (子任务 {current_idx}/{len(subtasks)})")
    return "skill_select"


def decide_after_reflection(state: AgentState) -> Literal["planning", "skill_select", "finalize"]:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        print("  ⏩ 路由: reflection → finalize (全部子任务完成)")
        return "finalize"
    current_task = subtasks[current_idx - 1]
    if current_task.get("status") == "adjust":
        print("  ⏩ 路由: reflection → planning (需要重新规划)")
        return "planning"
    print(f"  ⏩ 路由: reflection → skill_select (继续子任务 {current_idx})")
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
    print("\n" + "█" * 60)
    print("█  Agent 系统启动")
    print("█" * 60)
    print(f"  输入消息数: {len(messages)}")

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

    final = result.get("final_output", "") or "抱歉，我没有处理好你的请求喵~"
    print(f"\n{'█' * 60}")
    print(f"█  Agent 完成 | 最终输出: {len(final)} 字符")
    print(f"{'█' * 60}\n")
    return final


def add_skill(name: str, description: str, instructions: str = "", keywords: List[str] = None, category: str = "general"):
    """注册一个新技能（Markdown 指令形式）。

    Args:
        name: 技能名称（全局唯一）
        description: 技能描述（用于 LLM 判断何时使用）
        instructions: 技能指令文本（注入 executor 系统提示词）
        keywords: 触发关键词列表
        category: 技能分类 (search / execute / analyze / create / general)
    """
    skill = SkillDef(
        name=name,
        description=description,
        instructions=instructions,
        keywords=keywords or [],
        category=category,
    )
    skill_registry.register(skill)


def remove_skill(name: str):
    skill_registry.unregister(name)


def list_skills() -> List[Dict]:
    return skill_registry.list_all()


def get_memory():
    from app.agent.memory import AgentMemory
    return agent_memory
