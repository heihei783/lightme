from utils.db_handler import rag_search,add_message,get_session_history
from utils.file_handler import chat_prompt
from app.llm.chat_model import chat_model
from langchain_core.output_parsers import StrOutputParser
from utils.config_handler import config_ai
from app.llm.agent import agent


# 普通对话链
chain = chat_prompt | chat_model | StrOutputParser()


def weather_agent(history,question):
    is_agent = config_ai.get("agent_open", False)
    is_stream = config_ai.get("STREAM", False)
    if is_agent:
            input_messages = history + [("user", question)]
            if not is_stream:
                result = agent.invoke({"messages": input_messages})
                response_text = result["messages"][-1].content
                print(response_text)
                return response_text
            else:
                full_text = ""
                result = agent.stream({"messages": input_messages}, stream_mode="messages")
                for msg, metadata in result:
                    if msg.content and not msg.tool_calls:
                        full_text += msg.content
                        print(msg.content, end="", flush=True)
                print() 
                return full_text
    else:
        if not is_stream:
            response_text = chain.invoke({
                "input": question,
                "history_messages": history
            })
            print(response_text)
            return response_text
        else:
            full_text = ""
            result = chain.stream({
                "input": question,
                "history_messages": history
            })
            for chunk in result:
                full_text += chunk
                print(chunk, end="", flush=True)
            print()
            return full_text






def chat_loop():
    while True:
        question = input("请输入问题：")
        session_id = "xiaohundun_test"
    
        history = get_session_history(session_id)
        
        response_text = weather_agent(history,question)

        add_message(session_id, question, response_text)


if __name__ == "__main__":
    chat_loop()

