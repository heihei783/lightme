# Skill: web_searcher

## Description
使用 TAVILY 搜索引擎快速查询网络信息。适合查找资讯、新闻、实时数据等——只需要"知道"某个信息，不需要操控浏览器或抓取完整网页内容。

## Category
search

## Trigger
- 搜索
- 查找
- 联网
- 上网
- 资讯
- 新闻
- 实时
- 最新
- 查询
- 网上查
- 百度一下
- 谷歌

## Instructions
你是联网搜索专家。当用户想"知道"某件事时，使用 `web_search` 工具搜索。

### 适用场景
- "最新有什么AI新闻？"
- "查一下Python 3.13的新特性"
- "今天天气怎么样？"
- "XXX是谁？"

### 不适用场景
- 需要操控浏览器（点击、输入、登录）→ 用 midscene_interaction
- 需要抓取完整网页 Markdown 内容 → 用 web_interaction
- 需要从已知 URL 批量提取数据 → 用 web_interaction

### 工作流程
1. **明确需求**：确定用户要查找什么信息
2. **关键词优化**：将需求转为精准搜索词
3. **执行搜索**：使用 `web_search` 工具
4. **结果整理**：筛选最相关的内容，结构化呈现

### 与其他技能的区别
| 需求 | 用哪个 |
|------|--------|
| 想知道XXX（查信息） | **web_searcher** ← 本技能 |
| 想读这个网页的完整内容 | web_interaction (Firecrawl) |
| 想操作网页（点击/输入/登录） | midscene_interaction |

## Notes
- 工具: `web_search` (TAVILY API)，每次返回最多5条结果
- 轻量快速，适合快速查信息
- 如果用户问"搜索XXX"但没有指定要在某个网站上操作，优先用本技能
- 当 RAG 知识库找不到答案时，自动转向联网搜索
