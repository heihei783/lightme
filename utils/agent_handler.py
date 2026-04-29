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
print(app)

# 4. 准备初始数据（模拟用户说了一句话）
initial_input = {
    "messages": [("user", "星弥，帮我看看资料库里写了什么")]
}

# 5. 启动运行（流式输出每个节点的成果）
print("--- 开始执行星弥的自主规划流程 ---")
for event in app.stream(initial_input):
    for node_name, output in event.items():
        print(f"\n[进入节点]: {node_name}")
        print(f"[节点产出]: {output['messages'][-1][1]}") # 打印最后一条消息内容

