import os
from langchain_core.messages import  AIMessage
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

# ============================================================
# Monkey-patch: 修复 LiteLLM 未传递 DeepSeek reasoning_content 的问题
# ============================================================
# 问题1(入站): _convert_dict_to_message 在 streaming=False 时不会从 API 响应中
#             捕获 reasoning_content，导致 AIMessage.additional_kwargs 中缺失该字段。
# 问题2(出站): _convert_message_to_dict 不会将 additional_kwargs 中的
#             reasoning_content 写回 API 请求 dict。
# DeepSeek thinking 模式下，后续请求必须携带之前 assistant 消息的 reasoning_content，
# 否则报错: "The reasoning_content in the thinking mode must be passed back to the API"

import langchain_community.chat_models.litellm as _litellm_mod

# ---- 修复入站 ----
_orig_convert_dict_to_message = _litellm_mod._convert_dict_to_message


def _patched_convert_dict_to_message(_dict):
    message = _orig_convert_dict_to_message(_dict)
    if (isinstance(message, AIMessage)
            and _dict.get("reasoning_content")):
        message.additional_kwargs["reasoning_content"] = _dict["reasoning_content"]
    return message


_litellm_mod._convert_dict_to_message = _patched_convert_dict_to_message

# ---- 修复出站 ----
_orig_convert_message_to_dict = _litellm_mod._convert_message_to_dict


def _patched_convert_message_to_dict(message):
    message_dict = _orig_convert_message_to_dict(message)
    if (hasattr(message, "additional_kwargs")
            and "reasoning_content" in message.additional_kwargs):
        message_dict["reasoning_content"] = message.additional_kwargs["reasoning_content"]
    return message_dict


_litellm_mod._convert_message_to_dict = _patched_convert_message_to_dict
# ============================================================


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
