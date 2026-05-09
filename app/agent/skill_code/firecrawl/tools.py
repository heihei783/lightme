"""Firecrawl 工具 —— 网页搜索、抓取、爬取、URL发现、结构化提取

这些工具通过 web_interaction 技能触发，提供比 TAVILY 更强的网页内容获取能力。
"""

import json
from typing import Optional
from langchain_core.tools import tool

from app.agent.skill_code.firecrawl.client import get_firecrawl_client


@tool
def firecrawl_search(query: str, limit: int = 5) -> str:
    """
    使用 Firecrawl 搜索引擎查找网页。返回网页标题、URL 和内容摘要。
    适用于: 发现信息、查找资料、搜索最新内容。
    参数 query: 搜索关键词。
    参数 limit: 返回结果数量，默认5，最大10。
    """
    try:
        client = get_firecrawl_client()
        limit = min(limit, 10)
        result = client.search(query, limit=limit)
        items = result.data if hasattr(result, "data") else result.get("data", [])
        if not items:
            return "未找到相关搜索结果"

        lines = []
        for i, item in enumerate(items[:limit], 1):
            title = getattr(item, "title", "") or item.get("title", "")
            url = getattr(item, "url", "") or item.get("url", "")
            desc = getattr(item, "description", "") or item.get("description", "")
            lines.append(f"{i}. {title}\n   URL: {url}\n   摘要: {desc[:300]}")
        return f"搜索 '{query}' 结果:\n\n" + "\n\n".join(lines)
    except Exception as e:
        return f"Firecrawl 搜索出错: {str(e)}"


@tool
def firecrawl_scrape(url: str, only_main_content: bool = True) -> str:
    """
    抓取单个网页的完整 Markdown 内容。返回网页的标题、正文和元信息。
    适用于: 阅读文章、提取网页内容、获取页面详情。
    参数 url: 要抓取的网页地址。
    参数 only_main_content: 是否仅提取正文（True=去广告和导航栏，False=完整页面）。
    """
    try:
        client = get_firecrawl_client()
        result = client.scrape(url, only_main_content=only_main_content, formats=["markdown"])

        title = getattr(result, "title", "") or result.get("title", "") or ""
        content = getattr(result, "markdown", "") or result.get("markdown", "") or ""

        return (
            f"网页标题: {title}\n"
            f"URL: {url}\n"
            f"{'=' * 50}\n"
            f"{content[:5000]}"
        )
    except Exception as e:
        return f"Firecrawl 抓取出错: {str(e)}"


@tool
def firecrawl_crawl(url: str, limit: int = 10, prompt: str = "") -> str:
    """
    从起始 URL 开始爬取网站，获取多个相关页面的内容。
    适用于: 收集文档、批量获取文章、站点内容整理。
    参数 url: 起始爬取地址。
    参数 limit: 最多爬取页面数，默认10。
    参数 prompt: 可选的内容过滤提示，只爬取匹配的页面（如 "只获取API文档"）。
    """
    try:
        client = get_firecrawl_client()
        kwargs = {"limit": limit, "scrape_options": {"formats": ["markdown"]}}
        if prompt:
            kwargs["prompt"] = prompt

        result = client.crawl(url, **kwargs)

        pages = getattr(result, "pages", None) or getattr(result, "data", None)
        if not pages:
            pages = result.get("pages", []) if isinstance(result, dict) else []

        lines = [f"爬取完成。起始 URL: {url}\n"]
        for i, page in enumerate(pages[:limit], 1):
            title = getattr(page, "title", "") or page.get("title", "") or ""
            page_url = getattr(page, "url", "") or page.get("url", "") or ""
            content = getattr(page, "markdown", "") or page.get("markdown", "") or ""
            lines.append(f"--- 页面 {i}: {title} ---")
            lines.append(f"URL: {page_url}")
            lines.append(content[:1500])
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Firecrawl 爬取出错: {str(e)}"


@tool
def firecrawl_map(url: str, search: str = "", limit: int = 50) -> str:
    """
    发现网站上的所有 URL 链接，用于了解网站结构和内容分布。
    适用于: 了解网站结构、找到特定页面、站点地图生成。
    参数 url: 目标网站地址。
    参数 search: 可选的 URL 过滤关键词，只返回包含该词的链接。
    参数 limit: 最多返回链接数，默认50。
    """
    try:
        client = get_firecrawl_client()
        kwargs = {"limit": limit}
        if search:
            kwargs["search"] = search

        result = client.map(url, **kwargs)

        links = getattr(result, "links", None) or result.get("links", [])
        if not links:
            return f"未发现任何链接 (URL: {url})"

        lines = [f"发现 {len(links)} 个链接 (URL: {url}):"]
        for i, link in enumerate(links[:limit], 1):
            lines.append(f"  {i}. {link}")
        return "\n".join(lines)
    except Exception as e:
        return f"Firecrawl URL 发现出错: {str(e)}"


@tool
def firecrawl_extract(urls: str, prompt: str, json_schema: str = "") -> str:
    """
    从网页中提取结构化数据（如价格、名称、描述等）。
    适用于: 商品信息提取、数据采集、信息结构化。
    参数 urls: 用逗号分隔的 URL 列表。
    参数 prompt: 自然语言描述要提取什么信息。
    参数 json_schema: 可选的 JSON schema 定义输出结构。
    """
    try:
        client = get_firecrawl_client()
        url_list = [u.strip() for u in urls.split(",") if u.strip()]
        kwargs = {"urls": url_list, "prompt": prompt}
        if json_schema:
            kwargs["schema"] = json.loads(json_schema)

        result = client.extract(**kwargs)

        data = getattr(result, "data", None) or result.get("data", {})
        output = json.dumps(data, ensure_ascii=False, indent=2)
        return f"提取结果:\n{output[:5000]}"
    except json.JSONDecodeError as e:
        return f"schema JSON 解析出错: {str(e)}"
    except Exception as e:
        return f"Firecrawl 提取出错: {str(e)}"


# 本技能可用的工具列表
TOOLS = [
    firecrawl_search,
    firecrawl_scrape,
    firecrawl_crawl,
    firecrawl_map,
    firecrawl_extract,
]
