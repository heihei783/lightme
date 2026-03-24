from langchain_openai import ChatOpenAI
from utils.config_handler import config_ai

#聊天大模型
def get_chat_model():
    Chat_model = ChatOpenAI(
        api_key=config_ai['CHAT_MODEL_API_KEY'],
        model=config_ai['CHAT_MODEL_NAME'],
        base_url=config_ai['CHAT_MODEL_URL']
    )
    return Chat_model





if __name__ == "__main__":
    chat_model = get_chat_model()
    print(chat_model)
