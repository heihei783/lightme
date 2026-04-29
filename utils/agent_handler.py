from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END

# 1. 定义状态（Agent 的大脑里存什么）
class AgentState(TypedDict):
    messages: Annotated[List, "对话历史"]
    next_step: str  

# 2. 定义节点（函数）
def call_model(state: AgentState):
    # 这里让 LLM 决定下一步干什么
    return {"messages": [("ai", "我计划读取 data/info.txt")]}

def execute_tool(state: AgentState):
    # 这里写你之前的文件读写逻辑
    return {"messages": [("ai", "读取成功：内容是...")]}

# 3. 构建图
workflow = StateGraph(AgentState)

# 添加节点
workflow.add_node("agent", call_model)
workflow.add_node("action", execute_tool)

# 设置连线
workflow.set_entry_point("agent") # 从 agent 开始
workflow.add_edge("agent", "action") # agent 走完去 action
workflow.add_edge("action", END)    # action 走完就结束

# 编译成可运行的对象
app = workflow.compile()