from typing import TypedDict, Annotated, List, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.tools import tool
from app.llm.chat_model import chat_model
from utils.file_handler import txt_loader
from utils.path_tool import get_abs_path

# ==================== 1. 工具定义 ====================

@tool
def search_knowledge_base(query: str) -> str:
    """在本地知识库中搜索相关文档。当需要查找资料、文件内容或专业知识时使用此工具。
    参数 query: 搜索查询字符串。"""
    from utils.rag_handler import AdvancedRAG
    rag = AdvancedRAG()
    docs = rag.run_pipeline(query)
    if docs is None:
        return "未在知识库中找到相关文档"
    return "\n\n".join([doc.page_content for doc in docs])


tools = [search_knowledge_base]

# ==================== 2. 定义状态 ====================

class AgentState(TypedDict):
    messages: Annotated[List, add_messages]

# ==================== 3. 定义节点 ====================

def agent_node(state: AgentState) -> AgentState:
    """Agent 思考节点：LLM 决定是调用工具还是直接回答"""
    model_with_tools = chat_model.bind_tools(tools)
    response = model_with_tools.invoke(
        [("system", _get_system_prompt())] + state["messages"]
    )
    return {"messages": [response]}


tool_node = ToolNode(tools)

# ==================== 4. 路由逻辑 ====================

def should_continue(state: AgentState) -> Literal["tools", "end"]:
    """判断 Agent 是否需要继续调用工具"""
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"
    return "end"

# ==================== 5. 构建图 ====================

agent_workflow = StateGraph(AgentState)

agent_workflow.add_node("agent", agent_node)
agent_workflow.add_node("tools", tool_node)

agent_workflow.set_entry_point("agent")

# 条件边：需要工具 → tools，不需要 → 结束
agent_workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "tools", "end": END}
)
# tools 执行完 → 回到 agent 继续思考（形成循环）
agent_workflow.add_edge("tools", "agent")

agent_graph = agent_workflow.compile()

# ==================== 6. 辅助函数 ====================

def _get_system_prompt() -> str:
    """组合人格提示词 + 工具提示词"""
    chat_prompt_text = txt_loader(
        get_abs_path(r"app\llm\prompts\chat_prompt.txt")
    )[0].page_content

    return (
        f"人格设定：\n{chat_prompt_text}\n\n"
        f"你是一个能够使用工具的智能助手。\n"
        f"当需要查找资料时，使用 search_knowledge_base 工具搜索本地知识库。\n"
        f"使用工具找到资料后，请用自然的语气向主人汇报结果，不要死板地复述。\n"
        f"如果不需要使用工具，直接与主人对话即可。"
    )
