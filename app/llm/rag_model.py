from langchain_openai import OpenAIEmbeddings
from utils.config_handler import config_ai
from langchain_community.embeddings import DashScopeEmbeddings
from utils.path_tool import get_abs_path


def get_dashscope_model():
    embedding = DashScopeEmbeddings(
        dashscope_api_key=config_ai.get("EMBEDDING_MODEL_API_KEY"),
        model=config_ai.get("EMBEDDING_MODEL_NAME"),
    )
    return embedding

embedding = get_dashscope_model()


 
