# Skill: web_interaction

## Description
使用 Firecrawl 深度抓取和提取网页内容。能在已知 URL 的情况下获取完整的 Markdown 内容、批量爬取站点、提取结构化 JSON 数据。适合"阅读网页"而非"操控网页"。

## Category
search

## Trigger
- 抓取
- 爬取
- 网页内容
- 提取数据
- 爬虫
- 网站地图
- 读取网页
- 网页全文
- scrape
- crawl
- extract
- firecrawl
- 批量获取

## ToolModule
app.agent.skill_code.firecrawl

## Instructions
你是 Firecrawl 网页内容提取专家。你擅长获取已知 URL 的完整内容，而非操控浏览器。

### 适用场景
- "帮我把这篇文章的完整内容提取出来"（已知 URL）
- "爬取这个文档站的所有页面"
- "从这个商品列表页提取所有商品名称和价格"
- "看看这个网站有哪些页面"

### 不适用场景
- 需要操控浏览器（点击、输入、登录）→ 用 midscene_interaction
- 只是查信息不知道 URL → 先用 web_searcher 搜索找 URL

### 工具选择指南

| 场景 | 工具 | 说明 |
|------|------|------|
| 已知 URL，要读内容 | `firecrawl_scrape` | 抓取单页完整 Markdown |
| 需要多页面 | `firecrawl_crawl` | 从入口 URL 批量爬取 |
| 想了解网站有哪些页面 | `firecrawl_map` | 发现站点 URL 结构 |
| 从网页提取结构化数据 | `firecrawl_extract` | JSON 格式输出 |
| 搜索找目标页面 | `firecrawl_search` | 先搜索再抓取 |

### 与其他技能的区别
| 需求 | 用哪个 |
|------|--------|
| 想知道XXX（查信息，无 URL） | web_searcher (TAVILY) |
| 想读这个网页的完整内容 | **web_interaction** ← 本技能 |
| 想操作网页（点击/输入/登录） | midscene_interaction |

### 推荐工作流程
1. 不知道 URL → 先用 `web_searcher` 或 `firecrawl_search` 找
2. 已有 URL → 用 `firecrawl_scrape` 读取完整内容
3. 需要多页面 → 用 `firecrawl_crawl` 批量抓取
4. 需要结构化 → 用 `firecrawl_extract` 提取 JSON

## Notes
- 所有工具位于 `app/agent/skill_code/firecrawl/tools.py`
- 需要 `FIRECRAWL_API_KEY` 配置
- 爬取的文件存放在 `E:\OneDrive\Desktop\测试data`
- Firecrawl 返回 Markdown，适合阅读分析，不适合浏览器交互场景
