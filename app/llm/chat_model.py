from openai import OpenAI
from utils.config_handler import config_ai


client = OpenAI(
    api_key=config_ai["CHAT_MODEL_API_KEY"],
    model = config_ai["CHAT_MODEL_NAME"],
    base_url= config_ai["CHAT_MODEL_URL"],
)
