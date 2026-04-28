from utils.db_handler import add_message,get_session_history
from utils.file_handler import chat_prompt
from app.llm.chat_model import chat_model
from langchain_core.output_parsers import StrOutputParser
from utils.config_handler import config_ai
from app.agent.agent import agent
from langchain_core.messages import AIMessage


# 普通对话链
chain = chat_prompt | chat_model | StrOutputParser()


def whether_agent(history, question):
    is_agent = config_ai.get("agent_open", False)
    
    if is_agent:
        input_messages = history + [("user", question)]
            # 改为 yield 每一个碎片
        try:
            def stream_generator():
                result = agent.stream({"messages": input_messages}, stream_mode="messages")
                for msg, metadata in result:
                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        continue
                    if msg.content:
                        yield msg.content # 这里的 yield 会把碎片直接喷出去
            return stream_generator() # 返回生成器对象
        
        except Exception as e:
            print(f"agent 出错: {e} ")

    else:
        #同样改为 yield
        def stream_generator():
            result = chain.stream({"input": question, "history_messages": history})
            for chunk in result:
                yield chunk
        return stream_generator()






def chat_loop(session_id, question):
    # 1. 获取历史记录
    try:
        history = get_session_history(session_id)
        
        # 2. 开启 AI 的原始碎片流
        # 假设 whether_agent 现在返回的是 yield 出来的碎片
        gen = whether_agent(history, question)
        
        full_response = "" # 这就是我们的“接水桶”

        # 3. 开始迭代：这是流式的灵魂
        for chunk in gen:
            full_response += chunk  # 这里的每一步都在往桶里存
            yield chunk             # 这里的每一步都在把碎片给前端
            
        # 4. 【魔法时刻】当循环结束，说明 AI 话讲完了！
        # 此时 full_response 已经自动拼成了一段完整的文字
        if full_response:
            print(f"--- 对话结束，存入数据库: {full_response[:20]}... ---")
            # 在这里执行你的数据库存储逻辑
            add_message(session_id, question, full_response)
    
    except Exception as e:
        print(f"chat_loop 出错: {e} ")
    


if __name__ == "__main__":
    chat_loop()

