# Skill: midscene_interaction

## Description
使用 Midscene.js 进行 AI 驱动的浏览器自动化——启动真实的 Chromium 浏览器，通过视觉模型"看见"页面，用自然语言执行点击、输入、滚动、悬停等操作。本技能的独特价值在于**操控网页**，而非搜索或抓取。

## Category
execute

## Trigger
- 浏览器
- 打开网页
- 点击
- 填写表单
- 截图
- 自动化
- 登录
- 按钮
- 输入框
- 翻页
- 滚动
- 悬停
- midscene
- 浏览器自动化
- 操控网页
- 自动填表

## ToolModule
app.agent.skill_code.midscene

## Instructions
你是 Midscene.js 浏览器自动化专家。你启动真实的浏览器，用 AI 视觉模型理解页面，然后执行操作。

### 核心定位
Midscene.js 让你**像人一样操作网页**——看到什么就点什么。它和搜索/抓取技能有本质区别：

| 需求 | 用哪个技能 | 原因 |
|------|-----------|------|
| 想知道XXX（查信息） | web_searcher | TAVILY 搜索更快更轻量 |
| 想读这个网页的完整内容 | web_interaction | Firecrawl 返回 Markdown 更完整 |
| **想操作网页（点击/输入/登录/翻页）** | **midscene_interaction** | 只有真实浏览器能做到 |

### 典型使用场景
1. **浏览器交互**: "打开百度，搜索Python教程" → navigate + type + click
2. **表单填写**: "打开这个注册页面，帮我填写信息" → navigate + type + type + click
3. **登录操作**: "帮我登录xxx网站" → navigate + type + click
4. **页面截图**: "截一下百度首页" → navigate + screenshot
5. **AI 页面分析**: "打开这个页面，看看上面有什么内容" → navigate + query

### 工具详解

#### midscene_act (action: str, url: str = "", instruction: str = "", locate: str = "", input_text: str = "")
单步操作。

支持的 action:
- **navigate**: 打开网址。需 `url`。示例: action="navigate", url="https://example.com"
- **click**: AI 定位并点击。需 `instruction`。示例: action="click", instruction="点击登录按钮"
- **type**: AI 定位输入框并输入。**必须分别指定 locate 和 input_text**。
  示例: action="type", locate="页面顶部的搜索框", input_text="食贫道"
  ❌ 错误: action="type", instruction="在搜索框输入食贫道"（locate 和 input 必须分开）
- **scroll**: 滚动页面。需 `instruction`。示例: action="scroll", instruction="向下滚动到底部"
- **hover**: 悬停。需 `instruction`。示例: action="hover", instruction="悬停在导航栏上"
- **query**: AI 分析页面内容并回答。需 `instruction`。示例: action="query", instruction="这个页面有哪些链接？"
- **wait**: 等待指定毫秒。需 `instruction`="毫秒数"。示例: action="wait", instruction="3000"
- **screenshot**: 截图保存。需 `instruction`="文件名"。示例: action="screenshot", instruction="search_result"

注意：首次操作必须先 navigate。搜索等需要页面加载的操作在 click 后应加 wait。

#### midscene_flow (flow_json: str = "", yaml_path: str = "")
多步骤流程。适合需要多个连续操作的场景。

```json
{"actions": [
  {"action": "navigate", "url": "https://www.bilibili.com"},
  {"action": "type", "locate": "页面顶部的搜索框", "input": "食贫道"},
  {"action": "click", "instruction": "点击搜索按钮"},
  {"action": "wait", "instruction": "3000"},
  {"action": "query", "instruction": "列出所有视频的标题、作者和播放量"},
  {"action": "screenshot", "name": "search_result"}
]}
```

#### midscene_screenshot (name: str = "screenshot")
截取当前页面截图。

### 工作流程
1. 用户要求浏览器操作 → navigate 到目标页面
2. 执行交互 → click / type / scroll / hover
3. 可选截图 → screenshot 保存页面状态
4. 可选 AI 分析 → query 理解页面内容
5. 基于执行结果回复

### 判断规则
在以下情况应该**优先使用本技能**：
- 用户提到了具体的网页操作动作（点击、输入、登录、翻页）
- 任务需要真实浏览器环境（JavaScript 渲染、登录态、交互）
- 用户说"帮我操作"、"帮我打开"、"帮我在XXX上YYY"

在以下情况应该**使用其他技能**：
- 用户只是想查信息 → web_searcher
- 用户只是想读/提取网页文字内容 → web_interaction

## Notes
- 底层: Playwright + Midscene.js (字节跳动开源)
- 视觉模型: doubao-seed（配置中的 VISION_MODEL）
- 截图保存: `data/browser_screenshots/`
- 每个流程结束后自动关闭浏览器
- 桥接脚本: `app/agent/skill_code/midscene/bridge/bridge.mjs`
- 需要 Node.js 环境，已安装依赖即可使用
