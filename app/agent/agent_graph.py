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

import time
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
from app.agent.runtime import (
    RuntimeBudget,
    allowed_tools_for_subtask,
    budget_exceeded,
    detect_tool_policy_violations,
    extract_json_object,
    get_next_subtask_index,
    get_ready_subtasks,
    message_token_usage,
    new_run_id,
    normalize_plan,
    record_trace,
    repair_plan_for_capabilities,
    should_replan,
    trace_store,
    update_plan_after_subtask,
    verify_subtask_result,
)
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
    session_id: str#会话隔离 ID
    run_id: str#单次 Agent 运行 ID
    started_at: float#运行开始时间
    step_count: int#Runtime 总执行步数
    token_count: int#累计 Token 用量
    trace_enabled: bool#是否记录结构化 Trace
    stop_reason: str#预算或异常终止原因
    recent_tool_calls: List[str]#最近工具调用签名，用于重复调用检测


def _emit_runtime_metrics(state: Dict[str, Any], node: str, step_count: int | None = None, token_count: int | None = None, extra: Dict[str, Any] | None = None) -> None:
    started_at = float(state.get("started_at") or time.time())
    plan = state.get("plan") or {}
    subtasks = plan.get("subtasks") or []
    completed = sum(1 for st in subtasks if st.get("status") == "completed")
    failed = sum(1 for st in subtasks if st.get("status") == "failed")
    payload = {
        "step_count": step_count if step_count is not None else state.get("step_count", 0),
        "token_count": token_count if token_count is not None else state.get("token_count", 0),
        "elapsed_seconds": max(0, round(time.time() - started_at, 1)),
        "total_subtasks": len(subtasks),
        "completed_subtasks": completed,
        "failed_subtasks": failed,
        "current_subtask": plan.get("current_subtask", 1) if subtasks else 0,
        "stop_reason": state.get("stop_reason", ""),
    }
    if extra:
        payload.update(extra)
    console.emit_metrics(
        state.get("session_id") or "default",
        state.get("run_id") or "",
        node,
        payload,
    )


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
    step_count = state.get("step_count", 0) + 1
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
    record_trace(state, "node_start", "planning", {"step": step_count, "request": current_text[:500]})

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
    token_count = state.get("token_count", 0)
    try:
        complexity_msg = chat_model.invoke(complexity_prompt)
        token_count += message_token_usage(complexity_msg)
        complexity = complexity_msg.content.strip().lower()
        if complexity not in ("simple", "medium", "complex"):
            complexity = "medium"
    except Exception:
        complexity = "medium"

    print(f"  复杂度判定: {complexity}")
    console.emit_log("Planning", f"复杂度: {complexity}")

    previous_plan = state.get("plan") or {}
    is_replan = bool(previous_plan.get("subtasks"))
    failed_task = ""
    if is_replan:
        for st in previous_plan.get("subtasks", []):
            if st.get("status") == "adjust":
                failed_task = str(st.get("id"))
                break

    if complexity == "simple" and not is_replan:
        raw_plan = {
            "goal": current_text[:100],
            "subtasks": [{
                "id": "1",
                "desc": current_text if not history_context else f"{current_text}\n上下文: {history_context}",
                "expected_result": "直接回答用户请求并给出可交付结果",
                "task_type": "general",
                "risk_level": "low",
                "acceptance_checks": ["result_is_non_empty"],
                "allowed_tools": [],
                "max_tool_calls": 1,
                "skill": None,
                "depends_on": []
            }],
            "estimated_steps": 1
        }
    else:
        skills_available = skill_registry.list_all()
        skills_desc = "\n".join([
            f"  - {s['name']} ({s['category']}): {s['description']}"
            for s in skills_available
        ])

        replan_hint = ""
        if is_replan:
            replan_hint = (
                f"\n当前计划执行遇到问题，需要局部重新规划。\n"
                f"原计划 JSON:\n{previous_plan}\n"
                f"失败/需调整子任务 ID: {failed_task or '未知'}\n"
                f"请尽量保留已完成子任务，只替换不可继续的部分。\n"
            )

        plan_prompt = (
            f"你需要将以下用户任务拆解为可执行的子任务 DAG。\n\n"
            f"{user_request}\n"
            f"{replan_hint}\n\n"
            f"可用技能:\n{skills_desc}\n\n"
            f"相关记忆:\n{memory_ctx if memory_ctx else '无相关记忆'}\n\n"
            f"重要: 如果对话历史中包含具体信息（如文件名、路径、命令等），必须将其应用到子任务中。\n"
            f"不要猜测或编造文件路径/名称，只使用用户在对话历史中明确提到的信息。\n\n"
            f"规划约束:\n"
            f"- 子任务必须有唯一 id、清晰 desc、expected_result、depends_on。\n"
            f"- 每个子任务必须包含 task_type、risk_level、acceptance_checks、allowed_tools、max_tool_calls。\n"
            f"- skill 只能从可用技能列表中选择；不确定时用 null。\n"
            f"- task_type 只能取 research/read/write/execute/browse/analyze/create/verify/general。\n"
            f"- risk_level 只能取 low/medium/high；执行命令、启动应用、移动/覆盖文件必须标为 high。\n"
            f"- acceptance_checks 写成可验证的短句，例如 result_contains_exit_or_execution_status。\n"
            f"- allowed_tools 只列当前子任务真正需要的工具，避免把所有工具都暴露给 Executor。\n"
            f"- max_tool_calls 应按风险限制：low=1, medium=2, high<=3。\n"
            f"- 避免过度拆分，普通任务 1-3 步，复杂任务不超过 8 步。\n"
            f"- 依赖必须形成 DAG，不能循环依赖，不能依赖不存在的 id。\n"
            f"- 局部重新规划时保留已完成子任务，只替换失败子任务及其后续受影响任务。\n\n"
            f"请输出 JSON 格式的执行计划 (不要包含其他内容)：\n"
            f'{{"goal": "目标描述", "subtasks": ['
            f'{{"id": "1", "desc": "子任务1描述", "expected_result": "验收标准", "task_type": "read", "risk_level": "low", "acceptance_checks": ["result_is_non_empty"], "allowed_tools": ["read_file_content"], "max_tool_calls": 1, "skill": "推荐技能名或null", "depends_on": []}},'
            f'{{"id": "2", "desc": "子任务2描述", "expected_result": "验收标准", "task_type": "verify", "risk_level": "low", "acceptance_checks": ["result_contains_pass_fail_judgement"], "allowed_tools": ["execute_python_code"], "max_tool_calls": 1, "skill": "推荐技能名或null", "depends_on": ["1"]}}'
            f'], "estimated_steps": N}}'
        )

        try:
            response_msg = chat_model.invoke(plan_prompt)
            token_count += message_token_usage(response_msg)
            response = response_msg.content.strip()
            raw_plan = extract_json_object(response)
        except (ValueError, KeyError, IndexError) as e:
            print(f"[Planning] JSON 解析失败: {e}，使用默认计划")
            raw_plan = {
                "goal": user_request[:100],
                "subtasks": [{
                    "id": "1",
                    "desc": user_request,
                    "expected_result": "给出可交付的任务结果",
                    "task_type": "general",
                    "risk_level": "low",
                    "acceptance_checks": ["result_is_non_empty"],
                    "skill": None,
                    "depends_on": [],
                }],
                "estimated_steps": 1
            }

    plan = normalize_plan(
        raw_plan,
        goal=current_text,
        complexity=complexity,
        previous_plan=previous_plan if is_replan else None,
        replan_from=failed_task or None,
    )
    plan = repair_plan_for_capabilities(plan, skill_registry.list_all())
    if is_replan:
        completed = [st for st in previous_plan.get("subtasks", []) if st.get("status") == "completed"]
        if completed:
            completed_by_id = {str(st.get("id")): st for st in completed}
            for st in plan.get("subtasks", []):
                old = completed_by_id.get(str(st.get("id")))
                if old:
                    st.update({
                        "status": "completed",
                        "result": old.get("result", ""),
                        "critique": old.get("critique", ""),
                    })
            plan = update_plan_after_subtask(plan)

    agent_memory.set_working("plan", plan)
    if state.get("trace_enabled", True):
        trace_store.record_plan(state["run_id"], plan)
    record_trace(
        state,
        "plan_created" if not is_replan else "plan_replanned",
        "planning",
        {
            "plan_id": plan.get("plan_id"),
            "version": plan.get("version"),
            "ready_subtasks": plan.get("ready_subtasks", []),
            "validation_errors": plan.get("validation_errors", []),
            "quality": plan.get("quality", {}),
            "capability_repairs": plan.get("capability_repairs", []),
            "budget_warnings": plan.get("budget_warnings", []),
            "state_graph": plan.get("state_graph", {}),
        },
    )

    print(f"  子任务数: {len(plan.get('subtasks', []))}")
    print(f"  计划质量: {plan.get('quality', {}).get('score', 'N/A')} ({plan.get('quality', {}).get('level', 'unknown')})")
    if plan.get("capability_repairs"):
        console.emit_log("Planning", f"能力修复: {plan['capability_repairs']}")
    if plan.get("budget_warnings"):
        console.emit_log("Planning", f"预算约束: {plan['budget_warnings']}")
    subtask_list = []
    for i, st in enumerate(plan.get("subtasks", []), 1):
        skill_info = f" → 技能: {st['skill']}" if st.get("skill") else ""
        meta_info = f" [{st.get('task_type', 'general')}/{st.get('risk_level', 'low')}]"
        print(f"    [{i}] {st['desc'][:80]}{skill_info}{meta_info}")
        subtask_list.append(f"[{i}] {st['desc'][:60]}{skill_info}{meta_info}")
    console.emit_log("Planning", f"计划: {len(subtask_list)} 个子任务\n" + "\n".join(subtask_list))
    _emit_runtime_metrics(
        {**state, "plan": plan},
        "planning",
        step_count,
        token_count,
        {"status": "running", "phase": "规划完成"},
    )

    plan_summary = (
        f"[Plan] **任务规划完成**\n"
        f"  目标: {plan.get('goal', 'N/A')}\n"
        f"  复杂度: {plan.get('complexity', 'N/A')}\n"
        f"  子任务数: {len(plan.get('subtasks', []))}\n"
        f"  预计步骤: {plan.get('estimated_steps', 'N/A')}"
    )
    for st in plan.get("subtasks", []):
        skill_info = f" [技能: {st['skill']}]" if st.get("skill") else ""
        plan_summary += f"\n    {st['id']}. {st['desc'][:60]}{skill_info} [{st.get('task_type', 'general')}/{st.get('risk_level', 'low')}]"

    return {
        "plan": plan,
        "memory_context": memory_ctx,
        "messages": [AIMessage(content=plan_summary)],
        "final_output": "",
        "step_count": step_count,
        "token_count": token_count,
    }


# -------------------- 节点2: 技能选择节点 --------------------
def skill_select_node(state: AgentState) -> AgentState:
    step_count = state.get("step_count", 0) + 1
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return state

    current_task = subtasks[current_idx - 1]
    task_desc = current_task.get("desc", "")
    record_trace(
        state,
        "node_start",
        "skill_select",
        {"step": step_count, "subtask_id": current_task.get("id"), "desc": task_desc[:500]},
    )

    print("\n" + "-" * 40)
    print(f"🎯 [SkillSelect] 技能选择 —— 子任务 {current_idx}: {task_desc[:60]}")
    print("-" * 40)

    if current_task.get("skill"):
        skill = skill_registry.get_by_name(current_task["skill"])
        if skill:
            print(f"  规划推荐技能: {skill.name}")
            agent_memory.set_working("current_skill", skill.name)
            record_trace(state, "skill_selected", "skill_select", {"subtask_id": current_task.get("id"), "skill": skill.name, "source": "planner"})
            _emit_runtime_metrics(state, "skill_select", step_count, extra={"status": "running", "phase": f"选择技能 {skill.name}"})
            return {
                "active_skills": [skill.name],
                "tool_iterations": 0,
                "messages": [AIMessage(content=f"[Tool] 选择技能: {skill.name} — {skill.description}")],
                "step_count": step_count,
            }
        print(f"  规划推荐技能 '{current_task['skill']}' 未找到，尝试自动匹配...")

    matched_skill = skill_registry.match(task_desc)
    if matched_skill:
        print(f"  匹配结果: {matched_skill.name} [{matched_skill.category}]")
        console.emit_log("SkillSelect", f"匹配技能: {matched_skill.name} [{matched_skill.category}]")
        agent_memory.set_working("current_skill", matched_skill.name)
        record_trace(state, "skill_selected", "skill_select", {"subtask_id": current_task.get("id"), "skill": matched_skill.name, "source": "auto_match"})
        _emit_runtime_metrics(state, "skill_select", step_count, extra={"status": "running", "phase": f"匹配技能 {matched_skill.name}"})
        return {
            "active_skills": [matched_skill.name],
            "tool_iterations": 0,
            "messages": [AIMessage(content=f"[Tool] 自动匹配技能: {matched_skill.name} — {matched_skill.description}")],
            "step_count": step_count,
        }

    print("  未匹配到技能，使用通用 LLM")
    agent_memory.set_working("current_skill", "general_llm")
    record_trace(state, "skill_selected", "skill_select", {"subtask_id": current_task.get("id"), "skill": "general_llm", "source": "fallback"})
    _emit_runtime_metrics(state, "skill_select", step_count, extra={"status": "running", "phase": "通用 LLM"})
    return {
        "active_skills": ["general_llm"],
        "tool_iterations": 0,
        "messages": [AIMessage(content="[Tool] 使用通用 LLM 能力处理任务")],
        "step_count": step_count,
    }


MAX_TOOL_ITERATIONS = RuntimeBudget.from_config().max_steps
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
    step_count = state.get("step_count", 0) + 1
    budget = RuntimeBudget.from_config()
    stop_reason = budget_exceeded({**state, "step_count": step_count}, budget)
    if stop_reason:
        record_trace(state, "budget_stop", "executor", {"reason": stop_reason, "step": step_count})
        _emit_runtime_metrics(
            state,
            "executor",
            step_count,
            extra={"status": "stopped", "phase": "预算停止", "stop_reason": stop_reason},
        )
        return {
            "stop_reason": stop_reason,
            "messages": [AIMessage(content=f"[BudgetStop] {stop_reason}，将基于已有结果结束。")],
            "step_count": step_count,
        }

    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return state

    tool_iterations = state.get("tool_iterations", 0)
    current_task = subtasks[current_idx - 1]

    subtask_tool_limit = max(1, int(current_task.get("max_tool_calls") or MAX_TOOL_ITERATIONS))
    effective_tool_limit = min(MAX_TOOL_ITERATIONS, subtask_tool_limit)
    remaining_tool_calls = max(0, effective_tool_limit - tool_iterations)
    tool_budget_exhausted = remaining_tool_calls == 0

    print("\n" + "-" * 40)
    print(
        f"⚡ [Executor] 执行节点 —— 子任务 {current_idx}/{len(subtasks)} | "
        f"工具预算 {tool_iterations}/{effective_tool_limit}"
    )
    print("-" * 40)

    task_desc = current_task.get("desc", "")
    record_trace(
        state,
        "node_start",
        "executor",
        {
            "step": step_count,
            "subtask_id": current_task.get("id"),
            "subtask": task_desc[:500],
            "tool_calls_used": tool_iterations,
            "tool_calls_remaining": remaining_tool_calls,
        },
    )

    # 确定当前激活的技能 (排除 general_llm 占位符)
    active_skills = state.get("active_skills", [])
    skill_name = active_skills[0] if active_skills and active_skills[0] != "general_llm" else None
    active_skill = skill_registry.get_by_name(skill_name) if skill_name else None

    # 动态绑定工具: DEFAULT_TOOLS + 当前技能专属工具
    skill_tools = get_skill_tools(active_skill) if active_skill else []
    candidate_tools = list(DEFAULT_TOOLS) + skill_tools
    tool_by_name = {tool.name: tool for tool in candidate_tools}
    allowed_tool_names = allowed_tools_for_subtask(current_task, list(tool_by_name))
    all_tools = [tool_by_name[name] for name in allowed_tool_names if name in tool_by_name]
    if tool_budget_exhausted:
        all_tools = []
        record_trace(
            state,
            "tool_budget_exhausted",
            "executor",
            {
                "subtask_id": current_task.get("id"),
                "max_tool_calls": effective_tool_limit,
                "action": "synthesize_existing_results",
            },
        )
    if skill_tools:
        print(f"  技能工具已加载: {[t.name for t in skill_tools]}")
    print(f"  工具策略: {current_task.get('task_type', 'general')}/{current_task.get('risk_level', 'low')} → {allowed_tool_names or ['无工具']}")
    record_trace(
        state,
        "executor_tool_policy",
        "executor",
        {
            "subtask_id": current_task.get("id"),
            "task_type": current_task.get("task_type", "general"),
            "risk_level": current_task.get("risk_level", "low"),
            "allowed_tools": allowed_tool_names,
            "max_tool_calls": effective_tool_limit,
            "tool_calls_used": tool_iterations,
            "tool_calls_remaining": remaining_tool_calls,
        },
    )
    model_with_tools = chat_model.bind_tools(all_tools) if all_tools else chat_model

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
        f"任务类型: {current_task.get('task_type', 'general')}\n"
        f"风险等级: {current_task.get('risk_level', 'low')}\n"
        f"预期结果: {current_task.get('expected_result') or '未提供'}\n"
        f"验收检查: {current_task.get('acceptance_checks', [])}\n"
        f"本子任务允许使用的工具: {allowed_tool_names or ['无工具，仅用模型回答']}\n"
        f"本子任务最多调用工具 {effective_tool_limit} 次，已调用 {tool_iterations} 次，"
        f"剩余 {remaining_tool_calls} 次。\n"
        f"请专注于完成这个子任务；如果没有允许的工具，不要声称已经执行外部操作。完成后给出明确的执行结果和证据。"
    )
    if tool_budget_exhausted:
        system_prompt += (
            "\n工具预算已经用尽。请读取对话中已有的工具返回结果，直接整理当前子任务的结果和证据；"
            "不要再次请求工具，也不要把预算用尽视为任务失败。"
        )

    messages = [SystemMessage(content=system_prompt)]
    if memory_ctx:
        messages.append(SystemMessage(content=f"相关背景知识：\n{memory_ctx}"))
    messages.extend(_safe_truncate_history(state["messages"], max_count=5))

    try:
        response = model_with_tools.invoke(messages)
        token_count = state.get("token_count", 0) + message_token_usage(response)
    except Exception as e:
        print(f"  ❌ LLM 调用出错: {e}")
        response = AIMessage(content=f"执行出错: {str(e)}")
        token_count = state.get("token_count", 0)

    requested_tool_calls = list(getattr(response, "tool_calls", []) or [])
    if len(requested_tool_calls) > remaining_tool_calls:
        trimmed_calls = requested_tool_calls[:remaining_tool_calls]
        record_trace(
            state,
            "tool_budget_trimmed",
            "executor",
            {
                "subtask_id": current_task.get("id"),
                "requested": len(requested_tool_calls),
                "accepted": len(trimmed_calls),
                "max_tool_calls": effective_tool_limit,
            },
        )
        response = AIMessage(content=response.content or "", tool_calls=trimmed_calls)

    if hasattr(response, "tool_calls") and response.tool_calls:
        recent_tool_calls = list(state.get("recent_tool_calls", []))
        repeated_signatures: List[str] = []
        policy_violations = detect_tool_policy_violations(response.tool_calls, allowed_tool_names)
        if policy_violations:
            stop_reason = "tool policy violation"
            record_trace(
                state,
                "tool_policy_violation",
                "executor",
                {
                    "subtask_id": current_task.get("id"),
                    "violations": policy_violations,
                    "allowed_tools": allowed_tool_names,
                },
            )
            console.emit_log("Executor", f"工具越权: {policy_violations}")
            return {
                "messages": [AIMessage(content=f"[ToolPolicy] 当前子任务不允许调用这些工具: {policy_violations}")],
                "tool_iterations": tool_iterations + len(response.tool_calls),
                "recent_tool_calls": recent_tool_calls,
                "step_count": step_count,
                "token_count": token_count,
                "stop_reason": stop_reason,
            }
        for tc in response.tool_calls:
            args_str = str(tc.get('args', {}))[:150]
            signature = f"{tc.get('name')}:{tc.get('args', {})}"
            recent_tool_calls.append(signature)
            if recent_tool_calls.count(signature) >= 3:
                repeated_signatures.append(signature)
            print(f"  🔧 调用工具: {tc['name']}({args_str})")
            console.emit_tool(tc['name'], args_str)
            record_trace(
                state,
                "tool_call_requested",
                "executor",
                {
                    "subtask_id": current_task.get("id"),
                    "tool": tc.get("name"),
                    "args": tc.get("args", {}),
                },
            )
        recent_tool_calls = recent_tool_calls[-12:]
        if repeated_signatures:
            stop_reason = "repeated tool call detected"
            record_trace(
                state,
                "loop_detected",
                "executor",
                {
                    "subtask_id": current_task.get("id"),
                    "repeated_signatures": repeated_signatures,
                },
            )
            _emit_runtime_metrics(
                state,
                "executor",
                step_count,
                token_count,
                {"status": "stopped", "phase": "检测到重复工具调用", "stop_reason": stop_reason},
            )
            return {
                "messages": [AIMessage(content=f"[LoopGuard] 检测到重复工具调用，已停止继续调用工具: {repeated_signatures[0][:160]}")],
                "tool_iterations": tool_iterations + len(response.tool_calls),
                "recent_tool_calls": recent_tool_calls,
                "step_count": step_count,
                "token_count": token_count,
                "stop_reason": stop_reason,
            }
    else:
        recent_tool_calls = list(state.get("recent_tool_calls", []))
        print(f"  💬 LLM 回复: {response.content[:100]}...")
        console.emit_log("Executor", f"LLM 回复: {response.content[:150]}")
        record_trace(
            state,
            "model_observation",
            "executor",
            {"subtask_id": current_task.get("id"), "content": response.content[:1000]},
        )

    agent_memory.set_working(f"subtask_{current_idx}_result", response.content)
    _emit_runtime_metrics(
        state,
        "executor",
        step_count,
        token_count,
        {"status": "running", "phase": f"执行子任务 {current_idx}/{len(subtasks)}"},
    )

    return {
        "messages": [response],
        "tool_iterations": tool_iterations + len(getattr(response, "tool_calls", []) or []),
        "step_count": step_count,
        "token_count": token_count,
        "recent_tool_calls": recent_tool_calls,
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
    step_count = state.get("step_count", 0) + 1
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    current_idx = plan.get("current_subtask", 1)

    if not subtasks or current_idx > len(subtasks):
        return state

    current_task = subtasks[current_idx - 1]
    task_desc = current_task.get("desc", "")
    record_trace(
        state,
        "node_start",
        "reflection",
        {"step": step_count, "subtask_id": current_task.get("id"), "desc": task_desc[:500]},
    )

    recent_msgs = state["messages"][-3:]
    result_text = "\n".join([
        m.content[:300] if hasattr(m, "content") else str(m)[:300]
        for m in recent_msgs
    ])

    critic_prompt = (
        f"评审以下子任务的执行结果：\n\n"
        f"子任务: {task_desc}\n"
        f"预期结果: {current_task.get('expected_result') or '未提供'}\n"
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
        critic_msg = chat_model.invoke(critic_prompt)
        critic_response = critic_msg.content.strip()
        token_count = state.get("token_count", 0) + message_token_usage(critic_msg)
    except Exception:
        critic_response = "完成"
        token_count = state.get("token_count", 0)

    print(f"  Critic 评审: {critic_response[:100]}...")

    subtask_retries = state.get("subtask_retries", 0)
    has_tool_evidence = any(getattr(message, "type", "") == "tool" for message in recent_msgs)
    verifier = verify_subtask_result(
        current_task,
        result_text,
        state.get("stop_reason", ""),
        has_tool_evidence=has_tool_evidence,
    )
    if verifier.get("issues"):
        print(f"  Verifier: {verifier['status']} | {verifier['issues']}")
        console.emit_log("Verifier", f"子任务 {current_idx}: {verifier['status']} | {', '.join(verifier['issues'])}")

    if verifier["status"] == "adjust" or "需要调整" in critic_response:
        if should_replan(plan, current_task, verifier, subtask_retries, MAX_SUBTASK_RETRIES):
            status = "adjust"
            subtask_retries += 1
        else:
            status = "retry"
            subtask_retries += 1
    elif verifier["status"] == "retry" or "需要重试" in critic_response:
        if subtask_retries >= MAX_SUBTASK_RETRIES:
            status = "adjust"
            critic_response = f"[触发局部重规划: 已重试 {subtask_retries} 次] " + critic_response
        else:
            status = "retry"
            subtask_retries += 1
    else:
        status = "completed"
        subtask_retries = 0

    if verifier.get("issues"):
        critic_response = f"[Verifier: {verifier['status']}; {'; '.join(verifier['issues'])}] {critic_response}"
    current_task["status"] = status
    current_task["result"] = result_text[:500]
    current_task["critique"] = critic_response[:300]
    current_task["verifier"] = verifier
    current_task["retry_count"] = subtask_retries
    if status == "failed":
        current_task["error"] = critic_response[:300]
    plan = update_plan_after_subtask(plan)

    print(f"  判定: {status} | 重试次数: {subtask_retries}/{MAX_SUBTASK_RETRIES}")
    console.emit_log("Reflection", f"子任务 {current_idx} 评审: {status} (重试: {subtask_retries}/{MAX_SUBTASK_RETRIES})")

    if status in ("completed", "failed"):
        print(f"  ✅ 子任务结束，推进到子任务 {plan['current_subtask']}")
        console.emit_log("Reflection", f"子任务 {current_idx} 结束，推进到 {plan['current_subtask']}")

    record_trace(
        state,
        "subtask_reviewed",
        "reflection",
        {
            "subtask_id": current_task.get("id"),
            "status": status,
            "retry_count": subtask_retries,
            "critique": critic_response[:500],
            "verifier": verifier,
            "ready_subtasks": plan.get("ready_subtasks", []),
            "state_graph": plan.get("state_graph", {}),
        },
    )
    if state.get("trace_enabled", True):
        trace_store.record_plan(state["run_id"], plan)

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
    _emit_runtime_metrics(
        {**state, "plan": plan},
        "reflection",
        step_count,
        token_count,
        {"status": "running", "phase": f"评审子任务 {current_idx}: {status}"},
    )

    return {
        "plan": plan,
        "subtask_retries": subtask_retries,
        "messages": [AIMessage(content=reflection_msg)],
        "step_count": step_count,
        "token_count": token_count,
    }


# -------------------- 节点6: 多 Agent 协作节点 --------------------
def collaboration_node(state: AgentState) -> AgentState:
    step_count = state.get("step_count", 0) + 1
    token_count = state.get("token_count", 0)
    plan = state.get("plan", {})
    collaboration_log = state.get("collaboration_log", [])
    record_trace(
        state,
        "node_start",
        "collaboration",
        {"step": step_count, "plan_id": plan.get("plan_id"), "version": plan.get("version")},
    )

    print("\n" + "=" * 60)
    print("🤝 [Collaboration] 协作节点 —— 多 Agent 协调")
    print("=" * 60)

    if plan.get("complexity") == "simple":
        print("  复杂度: simple，跳过协作")
        collaboration_log.append("[Coordinator] 任务简单，无需多 Agent 协作")
        _emit_runtime_metrics(state, "collaboration", step_count, token_count, {"status": "running", "phase": "跳过协作"})
        return {"collaboration_log": collaboration_log, "step_count": step_count, "token_count": token_count}

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
            token_count += message_token_usage(research_response)
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
        token_count += message_token_usage(critic_response)
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
    _emit_runtime_metrics(
        state,
        "collaboration",
        step_count,
        token_count,
        {"status": "running", "phase": "多 Agent 协作完成"},
    )

    return {
        "collaboration_log": collaboration_log,
        "messages": [AIMessage(content=summary_msg)],
        "step_count": step_count,
        "token_count": token_count,
    }


# -------------------- 节点7: 汇总输出节点 --------------------
def finalize_node(state: AgentState) -> AgentState:
    step_count = state.get("step_count", 0) + 1
    token_count = state.get("token_count", 0)
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])

    completed_count = sum(1 for st in subtasks if st.get("status") == "completed")
    failed_count = sum(1 for st in subtasks if st.get("status") == "failed")
    results_parts = []
    for st in subtasks:
        if st.get("result"):
            results_parts.append(f"[子任务{st['id']}]: {st['result'][:300]}")

    print("\n" + "=" * 60)
    print("📦 [Finalize] 汇总节点 —— 整合所有子任务结果，注入人格")
    print("=" * 60)
    print(f"  子任务完成数: {completed_count}/{len(subtasks)}")
    console.emit_log("Finalize", f"汇总 {completed_count}/{len(subtasks)} 个已完成子任务")
    record_trace(
        state,
        "node_start",
        "finalize",
        {"step": step_count, "completed": completed_count, "total": len(subtasks), "stop_reason": state.get("stop_reason", "")},
    )

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
        token_count += message_token_usage(final_response)
        final_text = final_response.content
        print(f"  最终回答: {final_text[:100]}...")
    except Exception:
        final_text = all_results
        print(f"  ⚠ LLM 调用失败，使用原始结果")

    stop_reason = state.get("stop_reason", "")
    if failed_count:
        terminal_status = "failed"
    elif stop_reason or completed_count < len(subtasks):
        terminal_status = "stopped"
    else:
        terminal_status = "completed"
    is_success = terminal_status == "completed"
    agent_memory.learn_from_interaction(
        user_input=user_request,
        agent_response=final_text[:500],
        success=is_success
    )

    metrics = {
        "total_subtasks": len(subtasks),
        "completed_subtasks": completed_count,
        "failed_subtasks": failed_count,
        "step_count": step_count,
        "token_count": token_count,
        "tool_calls": sum(1 for msg in state.get("messages", []) if hasattr(msg, "tool_call_id")),
        "stop_reason": stop_reason,
    }
    record_trace(state, "run_finalized", "finalize", {"final_output": final_text[:1000], "metrics": metrics})
    if state.get("trace_enabled", True):
        trace_store.finish_run(
            state["run_id"],
            terminal_status,
            final_text,
            metrics,
        )

    agent_memory.clear_working()
    _emit_runtime_metrics(
        state,
        "finalize",
        step_count,
        token_count,
        {
            "status": terminal_status,
            "phase": "任务完成" if terminal_status == "completed" else "任务未完整完成",
            **metrics,
        },
    )

    return {
        "final_output": final_text,
        "messages": [AIMessage(content=final_text)],
        "step_count": step_count,
        "token_count": token_count,
    }


# ====================================================================
# 路由函数
# ====================================================================

def should_continue_tools(state: AgentState) -> Literal["tools", "reflect", "finalize"]:
    if state.get("stop_reason"):
        print("  ⏩ 路由: executor → finalize (预算终止)")
        return "finalize"
    last_msg = state["messages"][-1]
    tool_iterations = state.get("tool_iterations", 0)
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        print(f"  ⏩ 路由: executor → tools (累计 {tool_iterations} 次工具调用)")
        return "tools"
    print(f"  ⏩ 路由: executor → reflection")
    return "reflect"


def should_continue_plan(state: AgentState) -> Literal["skill_select", "finalize"]:
    plan = state.get("plan", {})
    subtasks = plan.get("subtasks", [])
    plan["ready_subtasks"] = get_ready_subtasks(plan)
    current_idx = get_next_subtask_index(plan) or plan.get("current_subtask", 1)
    plan["current_subtask"] = current_idx

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
    plan["ready_subtasks"] = get_ready_subtasks(plan)
    current_idx = get_next_subtask_index(plan) or plan.get("current_subtask", 1)
    plan["current_subtask"] = current_idx

    if not subtasks or current_idx > len(subtasks):
        print("  ⏩ 路由: reflection → finalize (全部子任务完成)")
        return "finalize"
    if not plan.get("ready_subtasks"):
        print("  ⏩ 路由: reflection → finalize (无可执行子任务)")
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
    {"tools": "tools", "reflect": "reflection", "finalize": "finalize"}
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

def run_agent(messages: List, session_id: str = "default") -> str:
    print("\n" + "█" * 60)
    print("█  Agent 系统启动")
    print("█" * 60)
    print(f"  输入消息数: {len(messages)}")
    budget = RuntimeBudget.from_config()
    run_id = new_run_id()
    goal = ""
    if messages:
        last = messages[-1]
        goal = last.content if hasattr(last, "content") else str(last)
    if budget.trace_enabled:
        trace_store.start_run(run_id, session_id, goal)
    console.emit_metrics(
        session_id,
        run_id,
        "runtime",
        {
            "status": "running",
            "phase": "任务启动",
            "step_count": 0,
            "token_count": 0,
            "elapsed_seconds": 0,
            "total_subtasks": 0,
            "completed_subtasks": 0,
            "failed_subtasks": 0,
            "current_subtask": 0,
            "stop_reason": "",
        },
    )

    initial_state = {
        "messages": messages,
        "plan": {},
        "memory_context": "",
        "collaboration_log": [],
        "active_skills": [],
        "final_output": "",
        "tool_iterations": 0,
        "subtask_retries": 0,
        "session_id": session_id,
        "run_id": run_id,
        "started_at": time.time(),
        "step_count": 0,
        "token_count": 0,
        "trace_enabled": budget.trace_enabled,
        "stop_reason": "",
        "recent_tool_calls": [],
    }
    record_trace(
        initial_state,
        "run_started",
        "runtime",
        {
            "session_id": session_id,
            "run_id": run_id,
            "budget": {
                "max_steps": budget.max_steps,
                "max_runtime_seconds": budget.max_runtime_seconds,
                "max_tokens": budget.max_tokens,
                "planner_enabled": budget.planner_enabled,
                "planner_parallelism": budget.planner_parallelism,
            },
        },
    )

    try:
        result = agent_graph.invoke(
            initial_state,
            config={"recursion_limit": max(20, budget.max_steps * 4)}
        )
    except Exception as e:
        if budget.trace_enabled:
            trace_store.finish_run(run_id, "failed", str(e), {"error": str(e)})
        console.emit_metrics(
            session_id,
            run_id,
            "runtime",
            {
                "status": "failed",
                "phase": "任务失败",
                "step_count": 0,
                "token_count": 0,
                "elapsed_seconds": max(0, round(time.time() - initial_state["started_at"], 1)),
                "stop_reason": str(e),
            },
        )
        raise

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
