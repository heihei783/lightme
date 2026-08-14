"""
LangGraph 主控图 —— 统一对话路由与多 Agent 协作编排
====================================================

架构概览:
  用户输入 → Unified Runtime + Session Handoff
                    ├─ Direct: 简单问题直接回答
                    └─ Agent: Planner → Scheduler → Isolated Workers → Finalize
                                      ├─ knowledge_search
                                      ├─ web_search
                                      ├─ file tools
                                      └─ shell / browser

功能特性:
  1. 统一路由: 简单回答与外部能力执行共用一个 Runtime 入口
  2. 会话交接: 续接上一轮目标、证据、产物、验收和未完成项
  3. Agent 规划: 复杂任务自动拆解为结构化 DAG
  4. Worker 能力: 知识库、网络、文件、Shell 与浏览器统一工具化
  5. 可审计过程: 记录计划、调度、工具 Observation、验收和重规划
"""

import re
from typing import TypedDict, Annotated, List, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage
from utils.db_handler import add_message, get_session_history
from utils.file_handler import chat_prompt
from app.llm.chat_model import chat_model
from utils.config_handler import config_ai
from app.agent.agent import run_agent
from app.agent.runtime import format_handoff_context, is_continuation_request, trace_store

# ====================================================================
# 1. 定义主图状态
# ====================================================================

class MainState(TypedDict):
    """
    主图状态 —— 贯穿整个对话流程

    字段说明:
      messages: 对话消息列表 (LangGraph 使用 add_messages reducer 自动合并)
      route:    路由决策结果 (direct / agent)
    """
    messages: Annotated[List, add_messages]
    route: str
    session_id: str
    execution_context: str
    runtime_allowed_tools: List[str]


# ====================================================================
# 2. 路由节点 —— 意图识别与分流
# ====================================================================

def _build_intent_prompt(question: str, available_tools: List[str],
                         history: str = "", execution_context: str = "") -> str:
    """Build the direct/agent decision prompt for the unified Runtime."""

    parts = [
        "你是一个意图识别助手。请结合对话上下文判断用户当前问题的意图：",
        "",
        "1. direct - 普通闲聊、解释、改写或不需要外部能力的简单回答",
        "2. agent  - 需要知识库、联网、文件、代码、Shell、系统或浏览器能力，或者需要续接上一轮 Agent 执行",
        "",
        "重要：如果当前问题是上一轮的追问或延续（如'再看看'、'还有吗'、'继续'等），",
        "应沿用上一轮的意图而非重新判断。",
        "",
    ]

    if history:
        parts.append("【最近的对话历史】")
        parts.append(history)
        parts.append("")

    if execution_context:
        parts.append("【同一会话最近一次 Agent 执行摘要】")
        parts.append(execution_context[:3000])
        parts.append("如果当前问题在续接、修正、重试或引用该执行结果，应优先返回 agent。")
        parts.append("")

    if available_tools:
        from app.agent.agent import skill_registry, DEFAULT_TOOLS

        parts.append("【当前 Runtime 可用工具】")
        for tool in DEFAULT_TOOLS:
            if tool.name not in available_tools:
                continue
            desc = (tool.description or "").split("\n")[0][:80]
            parts.append(f"  - {tool.name}: {desc}")
        parts.append("")

        parts.append("【可用的技能】")
        for skill in skill_registry.list_all():
            parts.append(f"  - {skill['name']} ({skill['category']}): {skill['description']}")
        parts.append("")

    parts.append(f"【用户当前问题】{question}")
    parts.append("")
    parts.append("请只返回一个词：direct 或 agent。")

    return "\n".join(parts)


def router_node(state: MainState) -> MainState:
    """
    意图识别节点 —— 用 AI 判断用户意图后分流

    流程:
      1. 根据开关建立本次 Runtime 的工具能力边界
      2. 显式续接请求直接进入 Agent
      3. 其余请求只判断 direct / agent
    """
    last_msg = state["messages"][-1].content

    # 提取最近几轮对话作为上下文（当前消息除外）
    all_msgs = state["messages"]
    history_parts = []
    for msg in all_msgs[-7:-1]:  # 取当前消息之前最多 6 条
        role = "用户" if getattr(msg, "type", "") == "human" else "助手"
        content = msg.content if hasattr(msg, "content") else str(msg)
        history_parts.append(f"[{role}]: {content[:200]}")
    history_context = "\n".join(history_parts) if history_parts else ""

    rag_open = config_ai.get("rag_open", False)
    agent_open = config_ai.get("agent_open", False)
    execution_context = str(state.get("execution_context") or "")
    from app.agent.agent import DEFAULT_TOOLS, skill_registry
    from app.agent.skill_loader import get_skill_tools

    if agent_open:
        available_tool_names = {tool.name for tool in DEFAULT_TOOLS}
        for skill_info in skill_registry.list_all():
            skill = skill_registry.get_by_name(skill_info.get("name"))
            if skill:
                available_tool_names.update(tool.name for tool in get_skill_tools(skill))
        available_tools = sorted(available_tool_names)
    elif rag_open:
        available_tools = ["knowledge_search"]
    else:
        available_tools = []

    if not available_tools:
        print("\n" + "=" * 60)
        print("[Runtime] 路由 -> direct (外部能力未启用)")
        print("=" * 60)
        return {"route": "direct", "runtime_allowed_tools": []}

    if execution_context and is_continuation_request(last_msg):
        print("\n" + "=" * 60)
        print("[Runtime] 路由 -> agent (续接上一轮执行)")
        print("=" * 60)
        return {"route": "agent", "runtime_allowed_tools": available_tools}

    # AI 意图识别（含对话历史）
    intent_prompt = _build_intent_prompt(
        last_msg,
        available_tools,
        history_context,
        execution_context,
    )
    try:
        intent = chat_model.invoke(intent_prompt).content.strip().lower()
        if intent in ("chat", "rag"):
            intent = "direct" if intent == "chat" else "agent"
        if intent not in ("direct", "agent"):
            intent = "direct"
    except Exception:
        intent = "direct"

    print("\n" + "=" * 60)
    print(f"[Runtime] 路由 -> {intent}")
    print("=" * 60)

    return {"route": intent, "runtime_allowed_tools": available_tools}


# ====================================================================
# 3. 响应节点 —— Direct / Agent
# ====================================================================

def direct_node(state: MainState) -> MainState:
    """
    普通闲聊节点

    使用聊天提示词模版 + 历史记录，生成自然对话回复。
    适用于: 日常寒暄、简单问答、不需要外部知识的对话。
    """
    all_msgs = state["messages"]
    history = all_msgs[:-1]           # 历史消息 (不含当前问题)
    question = all_msgs[-1].content    # 当前用户问题

    # 使用聊天模版格式化消息
    prompt_msgs = chat_prompt.format_messages(
        input=question,
        history_messages=history
    )
    response = chat_model.invoke(prompt_msgs)
    return {"messages": [response]}


def agent_node(state: MainState) -> MainState:
    """
    增强 Agent 节点 —— 系统的"自主思考核心"

    调用 LangGraph 增强 Agent，内部流程:
      1. Planning:  分析任务，生成结构化子任务 DAG
      2. Scheduler: 选择安全执行前沿并控制并行度
      3. Workers:   使用隔离上下文和最小工具集合执行
      4. Verify:    根据证据验收、重试或局部重规划
      5. Finalize:  汇总结果，并保存下一轮可复用的执行交接

    Agent 可用的能力:
      - web_search:             联网搜索最新信息
      - execute_python_code:    执行 Python 代码
      - read_file_content:      读取文件
      - write_file_content:     写入文件
      - execute_shell_command:  执行 Shell 命令
      - 以及通过 add_skill() 动态注册的任何自定义技能

    记忆机制:
      - 每次交互自动学习，提取关键知识存入长期记忆
      - 执行新任务时检索相似历史经验
      - 情景记忆记录成功/失败经验，持续优化策略
    """
    # 调用增强 Agent，传入当前对话消息
    # run_agent 内部执行完整的 规划→调度→Worker→验收→汇总 流程。
    # 逐字隐性推理不对外暴露，可审计的决策、证据和产物会写入 Trace 与交接记忆。
    final_output = run_agent(
        state["messages"],
        session_id=state.get("session_id", "default"),
        runtime_allowed_tools=state.get("runtime_allowed_tools") or [],
    )
    return {"messages": [AIMessage(content=final_output)]}


# ====================================================================
# 4. 条件路由
# ====================================================================

def decide_route(state: MainState) -> Literal["direct", "agent"]:
    """
    根据 router_node 的决策结果，将请求路由到对应节点

    路由选项:
      direct → 普通 AI 对话
      agent  → Planner-Scheduler-Worker Runtime
    """
    return state["route"]


# ====================================================================
# 5. 构建主图
# ====================================================================

main_workflow = StateGraph(MainState)

# ---- 注册节点 ----
main_workflow.add_node("router", router_node)    # 入口: 意图识别
main_workflow.add_node("direct", direct_node)
main_workflow.add_node("agent", agent_node)

# ---- 设置入口 ----
main_workflow.set_entry_point("router")

# ---- 条件路由: router → direct/agent ----
main_workflow.add_conditional_edges(
    "router",
    decide_route,
    {
        "direct": "direct",
        "agent": "agent"
    }
)

# ---- 所有分支结束后 → 主图结束 ----
main_workflow.add_edge("direct", END)
main_workflow.add_edge("agent", END)

# 编译主图
main_graph = main_workflow.compile()


# ====================================================================
# 6. 对外接口 —— 保持与 web_py.py 的完全兼容
# ====================================================================

def _vision_chat(image_b64: str, question: str, history: list) -> str:
    """使用视觉模型分析图片并返回文本回复（每次动态读取配置，确保使用最新模型）"""
    import base64 as b64
    from openai import OpenAI
    from utils.config_handler import load_configai_config

    cfg = load_configai_config()
    api_key = cfg.get("VISION_MODEL_API_KEY")
    model_name = cfg.get("VISION_MODEL_NAME", "")
    api_base = cfg.get("VISION_MODEL_URL") or "https://ark.cn-beijing.volces.com/api/v3"

    client = OpenAI(api_key=api_key, base_url=api_base)

    content_parts = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    if question and question.strip():
        content_parts.insert(0, {"type": "text", "text": question})
    else:
        content_parts.insert(0, {"type": "text", "text": "请描述这张图片的内容"})

    messages = [{"role": "user", "content": content_parts}]
    response = client.chat.completions.create(model=model_name, messages=messages, stream=False)
    return response.choices[0].message.content


_HISTORY_TIMESTAMP_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\]\s*")


def _clean_history_messages(messages: list) -> list:
    """Keep timestamps in storage but remove them from model-facing context."""
    cleaned = []
    for message in messages:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            content = _HISTORY_TIMESTAMP_RE.sub("", content, count=1)
        if hasattr(message, "model_copy"):
            cleaned.append(message.model_copy(update={"content": content}))
        else:
            cleaned.append(message)
    return cleaned


def chat_loop(session_id: str, question: str, image_b64: str | None = None):
    """
    对话主循环 —— 与 web 层接口完全兼容

    当 image_b64 不为空时，使用视觉模型分析图片并返回回复。

    注意: 用 finally 保证消息持久化，即使客户端中途断开 (GeneratorExit)
         也能把已收集到的回复存入数据库。
    """
    full_response = ""
    try:
        history = _clean_history_messages(get_session_history(session_id))
        input_messages = history + [HumanMessage(content=question)]
        try:
            handoffs = trace_store.get_session_handoffs(session_id, limit=2)
            execution_context = format_handoff_context(handoffs, max_chars=4500)
        except Exception:
            execution_context = ""

        if image_b64:
            # 视觉模式：调用视觉模型
            reply = _vision_chat(image_b64, question, history)
            full_response = reply
            yield reply
        else:
            # 普通模式：token 级别流式输出（stream_mode="messages"）
            for msg_chunk, metadata in main_graph.stream(
                {
                    "messages": input_messages,
                    "session_id": session_id,
                    # Router uses this to keep short follow-up requests on the Agent path.
                    "execution_context": execution_context,
                },
                stream_mode="messages",
                config={"recursion_limit": 150}
            ):
                # 只输出 direct/agent 节点的 token，过滤掉 Runtime 路由判断。
                node = metadata.get("langgraph_node", "")
                if node in ("direct", "agent") and msg_chunk.content:
                    full_response += msg_chunk.content
                    yield msg_chunk.content

    except Exception as e:
        print(f"chat_loop 出错: {e}")
    finally:
        # finally 无论正常结束、异常、还是 GeneratorExit 都会执行
        # 确保消息不会因为客户端断开而丢失
        if full_response:
            print(f"--- 对话结束，存入数据库: {full_response[:20]}... ---")
            add_message(session_id, question, full_response)


# ====================================================================
# 7. 测试入口
# ====================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("LightMe LangGraph 主控图测试")
    print("=" * 60)

    # 测试1: 普通闲聊
    print("\n📌 测试闲聊路由:")
    for chunk in chat_loop("test_session", "你好，请介绍一下你自己"):
        print(chunk, end="", flush=True)

    print("\n\n📌 测试 Agent 路由 (需要 config_ai.agent_open = true):")
    print(f"  当前 agent_open 状态: {config_ai.get('agent_open', False)}")
    if config_ai.get("agent_open", False):
        for chunk in chat_loop("test_agent", "帮我搜索知识库中关于 Python 的资料，然后总结要点"):
            print(chunk, end="", flush=True)
    else:
        print("  Agent 未开启，请在 config/config_ai.yaml 中设置 agent_open: true")
