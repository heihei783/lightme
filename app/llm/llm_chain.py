"""
LangGraph 主控图 —— 统一对话路由与多 Agent 协作编排
====================================================

架构概览:
  用户输入 → Router(意图识别) → Chat(闲聊) / RAG(知识问答) / Agent(智能体)
                                                            │
                                          ┌─────────────────┼─────────────────┐
                                          ▼                 ▼                  ▼
                                    Planning(规划)   Collaboration(协作)   Skill(技能)
                                          │                 │                  │
                                          └─────────────────┼─────────────────┘
                                                            ▼
                                                     Executor(执行)
                                                            │
                                                            ▼
                                                     Reflection(反思)
                                                            │
                                                            ▼
                                                      Finalize(汇总)

功能特性:
  1. 智能路由:   自动判断用户意图，分流到闲聊/知识库/Agent
  2. Agent 规划:  复杂任务自动拆解为子任务序列
  3. Skill 技能:  可动态注册/移除的插件化技能系统
  4. 多Agent协作: Coordinator + Researcher + Executor + Critic 四位一体
  5. 记忆学习:   短期/长期/情景记忆，像人类一样从经验中学习
"""

import os
from typing import TypedDict, Annotated, List, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage
from utils.db_handler import add_message, get_session_history
from utils.file_handler import chat_prompt, rag_prompt
from utils.path_tool import get_abs_path
from app.llm.chat_model import chat_model
from utils.config_handler import config_ai
from utils.rag_handler import AdvancedRAG
from app.agent.agent import run_agent

rag = AdvancedRAG()

# ====================================================================
# 1. 定义主图状态
# ====================================================================

class MainState(TypedDict):
    """
    主图状态 —— 贯穿整个对话流程

    字段说明:
      messages: 对话消息列表 (LangGraph 使用 add_messages reducer 自动合并)
      route:    路由决策结果 (chat / rag / agent)
      context:  RAG 检索到的知识上下文 (仅 rag 路由使用)
    """
    messages: Annotated[List, add_messages]
    route: str
    context: str


# ====================================================================
# 2. 路由节点 —— 意图识别与分流
# ====================================================================

def _build_intent_prompt(question: str, rag_open: bool, agent_open: bool,
                         history: str = "") -> str:
    """动态构建意图识别 prompt，包含对话历史、知识库文档名、工具列表、技能列表"""

    parts = [
        "你是一个意图识别助手。请结合对话上下文判断用户当前问题的意图：",
        "",
        "1. chat  - 普通闲聊、日常问候、简单问答，不涉及知识库文档和工具调用",
        "2. rag   - 需要查询知识库中的文档来回答用户问题",
        "3. agent - 需要使用工具来执行代码、读写文件、联网搜索、浏览器自动化或网页操作",
        "",
        "重要：如果当前问题是上一轮的追问或延续（如'再看看'、'还有吗'、'继续'等），",
        "应沿用上一轮的意图而非重新判断。",
        "",
    ]

    if history:
        parts.append("【最近的对话历史】")
        parts.append(history)
        parts.append("")

    if rag_open:
        rag_dir = get_abs_path("data/rag_file")
        doc_names = []
        if os.path.isdir(rag_dir):
            doc_names = [
                f for f in os.listdir(rag_dir)
                if os.path.isfile(os.path.join(rag_dir, f))
            ]
        if doc_names:
            parts.append("【知识库中的文档】")
            for name in doc_names:
                parts.append(f"  - {name}")
        else:
            parts.append("【知识库中的文档】暂无文档。")
        parts.append("")

    if agent_open:
        from app.agent.agent import skill_registry, DEFAULT_TOOLS

        parts.append("【可用的工具】")
        for tool in DEFAULT_TOOLS:
            desc = (tool.description or "").split("\n")[0][:80]
            parts.append(f"  - {tool.name}: {desc}")
        parts.append("")

        parts.append("【可用的技能】")
        for skill in skill_registry.list_all():
            parts.append(f"  - {skill['name']} ({skill['category']}): {skill['description']}")
        parts.append("")

    parts.append(f"【用户当前问题】{question}")
    parts.append("")
    parts.append("请只返回一个词：chat、rag 或 agent。")

    return "\n".join(parts)


def router_node(state: MainState) -> MainState:
    """
    意图识别节点 —— 用 AI 判断用户意图后分流

    流程:
      1. agent_open / rag_open 均关闭 → 直接走 chat
      2. 至少一个开启 → 构建意图识别 prompt (含对话历史/文档名/工具/技能列表)
      3. AI 判断意图 → chat / rag / agent
      4. rag 意图时用 search_only 检索 (跳过 RAG 内部的重复路由判断)
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

    # 两个开关都关 → 直接闲聊
    if not rag_open and not agent_open:
        print("\n" + "=" * 60)
        print("🧭 [Router] 意图识别 → chat (开关均关闭)")
        print("=" * 60)
        return {"route": "chat"}

    # AI 意图识别（含对话历史）
    intent_prompt = _build_intent_prompt(last_msg, rag_open, agent_open, history_context)
    try:
        intent = chat_model.invoke(intent_prompt).content.strip().lower()
        if intent not in ("chat", "rag", "agent"):
            intent = "rag" if rag_open else "chat"
    except Exception:
        intent = "rag" if rag_open else "chat"

    print("\n" + "=" * 60)
    print(f"🧭 [Router] 意图识别 → {intent}")
    print("=" * 60)

    if intent == "rag" and rag_open:
        docs = rag.search_only(last_msg)
        if docs:
            context = "\n\n".join([doc.page_content for doc in docs])
            return {"route": "rag", "context": context}

    if intent == "agent" and agent_open:
        return {"route": "agent"}

    return {"route": "chat"}


# ====================================================================
# 3. 响应节点 —— 三种不同的对话处理模式
# ====================================================================

def chat_node(state: MainState) -> MainState:
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


def rag_node(state: MainState) -> MainState:
    """
    知识库问答节点 (RAG - Retrieval Augmented Generation)

    将检索到的知识上下文注入提示词，让 LLM 基于知识库内容回答。
    适用于: 需要查阅文档、资料的专业问题。
    """
    all_msgs = state["messages"]
    history = all_msgs[:-1]
    question = all_msgs[-1].content

    # 使用 RAG 模版，注入检索到的上下文
    prompt_msgs = rag_prompt.format_messages(
        input=question,
        history_messages=history,
        context=state["context"]
    )
    response = chat_model.invoke(prompt_msgs)
    return {"messages": [response]}


def agent_node(state: MainState) -> MainState:
    """
    增强 Agent 节点 —— 系统的"自主思考核心"

    调用 LangGraph 增强 Agent，内部流程:
      1. Planning:     分析任务，拆解为子任务序列
      2. Collaboration: 多 Agent 协作 (Coordinator/Researcher/Executor/Critic)
      3. Skill Select:  为每个子任务智能匹配最佳技能
      4. Executor:      使用工具+LLM 逐步执行子任务
      5. Reflection:    评审执行结果，决定是否重试或调整
      6. Finalize:      汇总所有结果，生成最终回答

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
    # run_agent 内部执行完整的 规划→协作→执行→反思→汇总 流程
    # 只返回最终汇总结果，中间过程消息不对外暴露
    final_output = run_agent(state["messages"])
    return {"messages": [AIMessage(content=final_output)]}


# ====================================================================
# 4. 条件路由
# ====================================================================

def decide_route(state: MainState) -> Literal["chat", "rag", "agent"]:
    """
    根据 router_node 的决策结果，将请求路由到对应节点

    路由选项:
      chat  → 普通 AI 对话
      rag   → 基于知识库的增强问答
      agent → 自主智能体 (规划+工具+协作)
    """
    return state["route"]


# ====================================================================
# 5. 构建主图
# ====================================================================

main_workflow = StateGraph(MainState)

# ---- 注册节点 ----
main_workflow.add_node("router", router_node)    # 入口: 意图识别
main_workflow.add_node("chat", chat_node)         # 分支1: 闲聊
main_workflow.add_node("rag", rag_node)           # 分支2: 知识问答
main_workflow.add_node("agent", agent_node)       # 分支3: 增强智能体

# ---- 设置入口 ----
main_workflow.set_entry_point("router")

# ---- 条件路由: router → chat/rag/agent ----
main_workflow.add_conditional_edges(
    "router",
    decide_route,
    {
        "chat": "chat",
        "rag": "rag",
        "agent": "agent"
    }
)

# ---- 所有分支结束后 → 主图结束 ----
main_workflow.add_edge("chat", END)
main_workflow.add_edge("rag", END)
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


def chat_loop(session_id: str, question: str, image_b64: str | None = None):
    """
    对话主循环 —— 与 web 层接口完全兼容

    当 image_b64 不为空时，使用视觉模型分析图片并返回回复。
    """
    try:
        history = get_session_history(session_id)
        input_messages = history + [HumanMessage(content=question)]

        full_response = ""

        if image_b64:
            # 视觉模式：调用视觉模型
            reply = _vision_chat(image_b64, question, history)
            full_response = reply
            yield reply
        else:
            # 普通模式：流式执行主图
            for event in main_graph.stream(
                {"messages": input_messages},
                stream_mode="updates",
                config={"recursion_limit": 150}
            ):
                for _node_name, update in event.items():
                    if "messages" in update:
                        for msg in update["messages"]:
                            if hasattr(msg, "content") and msg.content:
                                full_response += msg.content
                                yield msg.content

        if full_response:
            print(f"--- 对话结束，存入数据库: {full_response[:20]}... ---")
            add_message(session_id, question, full_response)

    except Exception as e:
        print(f"chat_loop 出错: {e}")


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
