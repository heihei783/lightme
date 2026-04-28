from langchain_community.chat_models import ChatLiteLLM
from utils.config_handler import config_ai


#聊天大模型
def get_chat_model():
    # 你可以随意更换 model 参数，格式为 "服务商/模型名"
    Chat_model = ChatLiteLLM(
        api_key=config_ai['CHAT_MODEL_API_KEY'],
        model=config_ai['CHAT_MODEL_NAME'],
        custom_llm_provider=config_ai['CHAT_MODEL_PROVIDER'],
        api_base=config_ai['CHAT_MODEL_URL'],
        streaming=False,
    )
    return Chat_model


chat_model = get_chat_model()



if __name__ == "__main__":
    chat_model = get_chat_model()
    print(chat_model)
