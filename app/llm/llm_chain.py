from typing import TypedDict, Annotated, List, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage
from utils.db_handler import add_message, get_session_history
from utils.file_handler import chat_prompt, rag_prompt
from app.llm.chat_model import chat_model
from utils.config_handler import config_ai
from utils.rag_handler import AdvancedRAG
from app.agent.agent import agent_graph

rag = AdvancedRAG()

# ==================== 1. 定义状态 ====================

class MainState(TypedDict):
    messages: Annotated[List, add_messages]
    route: str
    context: str

# ==================== 2. 路由节点 ====================

def router_node(state: MainState) -> MainState:
    """替代原来的 whether_agent()：判断意图，决定走 chat / rag / agent"""
    last_msg = state["messages"][-1].content
    docs = rag.run_pipeline(last_msg)

    if docs is not None:
        context = "\n\n".join([doc.page_content for doc in docs])
        return {"route": "rag", "context": context}

    if config_ai.get("agent_open", False):
        return {"route": "agent"}

    return {"route": "chat"}

# ==================== 3. 响应节点 ====================

def chat_node(state: MainState) -> MainState:
    """普通闲聊"""
    all_msgs = state["messages"]
    history = all_msgs[:-1]
    question = all_msgs[-1].content

    prompt_msgs = chat_prompt.format_messages(
        input=question,
        history_messages=history
    )
    response = chat_model.invoke(prompt_msgs)
    return {"messages": [response]}


def rag_node(state: MainState) -> MainState:
    """知识库问答"""
    all_msgs = state["messages"]
    history = all_msgs[:-1]
    question = all_msgs[-1].content

    prompt_msgs = rag_prompt.format_messages(
        input=question,
        history_messages=history,
        context=state["context"]
    )
    response = chat_model.invoke(prompt_msgs)
    return {"messages": [response]}


def agent_node(state: MainState) -> MainState:
    """调用 LangGraph Agent（带工具循环）"""
    result = agent_graph.invoke({"messages": state["messages"]})
    # agent 内部循环产生的所有消息都在 result["messages"] 里
    # 只把 agent 新生成的消息追加到主图状态
    new_msgs = result["messages"][len(state["messages"]):]
    return {"messages": new_msgs}

# ==================== 4. 条件路由 ====================

def decide_route(state: MainState) -> Literal["chat", "rag", "agent"]:
    return state["route"]

# ==================== 5. 构建图 ====================

main_workflow = StateGraph(MainState)

main_workflow.add_node("router", router_node)
main_workflow.add_node("chat", chat_node)
main_workflow.add_node("rag", rag_node)
main_workflow.add_node("agent", agent_node)

main_workflow.set_entry_point("router")

main_workflow.add_conditional_edges(
    "router",
    decide_route,
    {"chat": "chat", "rag": "rag", "agent": "agent"}
)

main_workflow.add_edge("chat", END)
main_workflow.add_edge("rag", END)
main_workflow.add_edge("agent", END)

main_graph = main_workflow.compile()

# ==================== 6. 对外接口（保持原接口不变） ====================

def chat_loop(session_id, question):
    """与 web_py.py 的接口完全兼容"""
    try:
        history = get_session_history(session_id)

        input_messages = history + [HumanMessage(content=question)]

        full_response = ""

        # stream_mode="updates" → 每个节点完成后返回 {节点名: 状态更新}
        for event in main_graph.stream(
            {"messages": input_messages},
            stream_mode="updates"
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


if __name__ == "__main__":
    for chunk in chat_loop("test_session", "你好，请介绍一下你自己"):
        print(chunk, end="", flush=True)
