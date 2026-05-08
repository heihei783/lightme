import os

# 禁用 LiteLLM 遥测/日志/网络请求，避免国内网络拉取 GitHub 时报错
# 必须在 import litellm 之前设置
os.environ["LITELLM_MODE"] = "development"
os.environ["LITELLM_LOG"] = "ERROR"
os.environ["LITELLM_TELEMETRY"] = "False"
os.environ["LITELLM_LOCAL"] = "True"
os.environ["LITELLM_NO_PROXY"] = "True"
os.environ["LITELLM_DISABLE_UPDATE_CHECK"] = "True"
os.environ.pop("no_proxy", None)
os.environ.pop("NO_PROXY", None)

import litellm
from langchain_community.chat_models import ChatLiteLLM
from utils.config_handler import config_ai

litellm.set_verbose = False
litellm.suppress_debug_info = True

# 屏蔽 LangChain 的 deprecation 噪音
import warnings
warnings.filterwarnings("ignore", message=".*ChatLiteLLM.*")
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
