from utils.config_handler import config_ai
from langchain_litellm import LiteLLMEmbeddings
from utils.path_tool import get_abs_path

# def get_dashscope_model():
#     embedding = LiteLLMEmbeddings(
#         api_key=config_ai.get("EMBEDDING_MODEL_API_KEY"),
#         model=config_ai.get("EMBEDDING_MODEL_NAME"),
#         api_base=config_ai.get("EMBEDDING_MODEL_URL"),
#     )
#     return embedding

# embedding = get_dashscope_model()


#搞半天litellmembeddings，都不成功，要么是litellm无法识别最新模型，要么是对应接口参数总是不对，故放弃
import requests
from typing import List
from langchain_core.embeddings import Embeddings

class Embedding(Embeddings):
    def __init__(self, api_key: str, model_name: str, api_base: str):
        self.api_key = api_key
        self.model_name = model_name
        self.api_url = f"{api_base.rstrip('/')}"

    def _get_embedding(self, text: str) -> List[float]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "input": [text] # 确保是列表格式
        }
        response = requests.post(self.api_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        # 这里为了稳定，循环调用单次，或者按需实现批量请求
        return [self._get_embedding(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._get_embedding(text)

def get_embedding_model():
    # 使用自定义类绕过所有库的封装问题
    return Embedding(
        api_key=config_ai.get("EMBEDDING_MODEL_API_KEY"),
        model_name=config_ai.get("EMBEDDING_MODEL_NAME"),
        api_base=config_ai.get("EMBEDDING_MODEL_URL")
    )

embedding = get_embedding_model()




 
