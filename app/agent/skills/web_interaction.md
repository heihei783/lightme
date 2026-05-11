# Skill: web_interaction

## Description
使用 Firecrawl 进行深度网页交互：搜索、抓取完整网页内容、批量爬取、URL 发现和结构化数据提取。相较于 TAVILY 的简单搜索，Firecrawl 能获取网页的完整 Markdown 内容，适合需要阅读、分析、整理网页信息的场景。

## Category
search

## Trigger
- 抓取
- 爬取
- 网页
- 网页内容
- 提取数据
- 爬虫
- 网页搜索
- 网站地图
- scrape
- crawl
- extract
- web content
- firecrawl

## ToolModule
app.agent.skill_code.firecrawl

## Instructions
你是 Firecrawl 网页交互专家。根据任务类型选择正确的工具：

### 工具选择指南

| 场景 | 工具 | 说明 |
|------|------|------|
| 搜索网页，找相关信息 | `firecrawl_search` | 按关键词搜索，返回标题+URL+摘要 |
| 已知 URL，要读网页内容 | `firecrawl_scrape` | 抓取单个页面完整 Markdown |
| 需要大量页面内容 | `firecrawl_crawl` | 从入口 URL 开始批量爬取 |
| 想了解网站有哪些页面 | `firecrawl_map` | 发现站点所有链接 |
| 从网页提取结构化数据 | `firecrawl_extract` | 按描述提取 JSON 格式数据 |

### 工具详解

#### firecrawl_search (query: str, limit: int = 5)
搜索网络，返回网页标题、URL 和内容摘要。适用于发现信息源、初步查找。
- `limit` 默认 5，最大 10

#### firecrawl_scrape (url: str, only_main_content: bool = True)
抓取单个网页的完整 Markdown 内容（标题+正文）。适用于阅读文章、提取页面详情。
- `only_main_content=True` 自动过滤广告和导航栏
- 返回最长 5000 字符

#### firecrawl_crawl (url: str, limit: int = 10, prompt: str = "")
从起始 URL 爬取整个网站，获取多页面内容。适用于文档收集、批量抓取。
- `prompt` 可选过滤："只获取API文档"、"只获取博客文章"
- 每个页面最多返回 1500 字符

#### firecrawl_map (url: str, search: str = "", limit: int = 50)
发现网站 URL 结构。适用于了解网站布局、找到特定页面。

#### firecrawl_extract (urls: str, prompt: str, json_schema: str = "")
从网页提取结构化数据（JSON）。适用于商品信息、价格对比等。
- `urls`: 逗号分隔的多个 URL
- `prompt`: 用自然语言描述要提取什么
- `json_schema`: 可选 JSON schema 约束输出结构

### 与 web_search (TAVILY) 的区别
- **web_search**: 轻量级搜索，返回摘要片段，适合快速查信息
- **Firecrawl**: 获取完整网页内容，适合需要深度阅读、分析、提取数据的场景

### 工作流程
1. 需求不明 → 先用 `firecrawl_search` 找目标页面
2. 已有 URL → 用 `firecrawl_scrape` 读完整内容
3. 需要多页面 → 用 `firecrawl_crawl` 批量获取
4. 工具返回后直接基于结果回复，不要重复调用

## Notes
- 所有 Firecrawl 工具代码位于 `app/agent/skill_code/firecrawl/tools.py`
- 需要 `FIRECRAWL_API_KEY` 配置在 `config/config_ai.yaml` 中
- 工具会返回明确的成功或错误信息，信任返回值，无需验证
- 爬取的文件存放在`E:\OneDrive\Desktop\测试data`的文件夹下

