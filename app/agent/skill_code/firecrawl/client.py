"""Firecrawl 客户端单例"""

from firecrawl import Firecrawl
from utils.config_handler import config_ai

_client = None


def get_firecrawl_client() -> Firecrawl:
    global _client
    if _client is None:
        api_key = config_ai.get("FIRECRAWL_API_KEY", "")
        _client = Firecrawl(api_key=api_key)
    return _client
