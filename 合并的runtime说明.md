# LightMe 统一 Runtime 合并方案实现说明

## 1. 合并目标

本次实现对应以下统一入口：

```text
用户请求
   ↓
统一 Runtime + Session Handoff
   ↓
简单问题 ───────────────→ Direct
   ↓
需要外部能力或续接任务
   ↓
Planner → Scheduler → Worker
                         ├─ knowledge_search
                         ├─ web_search
                         ├─ file tools
                         └─ shell / browser
```

“合并”不是把所有请求都强制走复杂规划，而是把原来彼此割裂的 Chat、RAG 和 Agent
入口收敛到同一个 Runtime 决策点。简单问题快速直答；需要检索、文件、系统、网络或
跨轮续接时进入同一套 Planner-Scheduler-Worker 执行协议。

## 2. 改造前的问题

改造前的顶层分支是：

```text
Router
├─ Chat
├─ RAG
└─ Agent
```

这会产生四类问题：

1. RAG 是独立回答链，不是 Agent 工具，Planner 无法在同一任务中组合知识库、文件和网络证据。
2. 下一轮短指令会重新分类，可能从 Agent 退回 Chat，上一轮执行状态因此没有被读取。
3. 设置项控制的是入口分支，不是本次运行的能力边界，难以解释 Worker 到底能使用哪些工具。
4. 前端保存了 Trace，但重新进入会话后只恢复聊天消息，用户看不到上一轮执行过程。

## 3. 改造后的顶层路由

顶层仅保留两类语义：

| 路由 | 适用场景 | 是否规划 | 是否调用工具 |
|---|---|---:|---:|
| `direct` | 闲聊、解释、改写、无需外部事实的简单问答 | 否 | 否 |
| `agent` | 知识库、联网、文件、代码、Shell、浏览器、复杂任务、跨轮续接 | 按复杂度决定 | 按任务授权 |

Router 会同时读取：

- 当前用户消息；
- 清理存储时间戳后的最近对话；
- 同一 Session 最近的 Execution Handoff；
- 当前设置允许的 Runtime 工具集合。

当消息包含“继续、刚才、按上面、重试、上个结果、那个文件”等明确引用，并且当前
Session 存在历史执行交接时，系统确定性地进入 `agent`，无需再让分类模型猜测。

## 4. RAG 如何并入 Agent

本地知识库现在通过一等工具暴露：

```text
knowledge_search(query: str) -> str
```

工具内部复用原有 `AdvancedRAG.hierarchical_search()`，返回有界的文档片段和来源。
它与 `web_search`、`read_file_content` 等工具一样，由 Planner 声明、Scheduler 调度、
Research Worker 调用，并把结果写入 Observation 和 Trace。

因此一个任务现在可以形成真实的组合执行：

```text
子任务 1：knowledge_search 查项目规范
子任务 2：read_file_content 检查当前实现
子任务 3：分析规范与实现差异
子任务 4：write_file_content 修改文件
子任务 5：execute_shell_command 运行测试
```

不再需要先走 RAG 得到一段答案，再由另一个 Agent 猜测那段答案来自哪里。

## 5. 设置项变成能力边界

设置不再决定三套重复执行链，而是生成本次运行的 `runtime_allowed_tools`：

| 设置状态 | 路由能力 |
|---|---|
| Agent 关闭、RAG 关闭 | 只允许 `direct` |
| Agent 关闭、RAG 开启 | 可进入受限 Agent，但只授予 `knowledge_search` |
| Agent 开启 | 授予默认工具和已加载 Skill 工具 |

Worker 最终可见工具是三个集合的交集：

```text
子任务允许工具
∩ Worker 职责工具
∩ Runtime 本次授权工具
```

这意味着仅开启知识库时，即使 Planner 产生了错误计划，Worker 也拿不到 Shell、文件写入
或系统操作工具。非 `general` 子任务若没有可用能力，会被标记为
`capability_blocked`，而不是假装执行成功。

## 6. 跨轮执行交接

每次 Agent 进入 Finalize 后会生成 Session 级 Execution Handoff：

```text
run_id / goal / status / final_summary
plan_id / plan_version / complexity
scheduler_epochs / replan_count / stop_reason
subtasks[]
artifacts[]
open_items[]
created_at
```

每个子任务保留：

```text
目标、状态、依赖、Worker
结果或错误
实际工具调用及脱敏参数
Observation 摘要及来源
Verifier 状态、问题、通过项
产物
```

下一轮开始时：

1. `chat_loop` 按 `session_id` 读取最近 Handoff。
2. Router 用它判断当前消息是否续接。
3. Planning 把它作为历史执行交接，而不是普通聊天文本。
4. Worker 同时获得有界交接摘要和当前依赖任务结果。
5. Trace 写入 `session_context_hydrated`，证明本轮确实加载过历史执行状态。

删除 Session 时，Run、Plan、Trace 和 Handoff 会同步清除，避免跨用户或跨会话串线。

## 7. 展示的是可审计过程

前端展示和持久化的是：

- 任务理解与复杂度判断；
- 计划版本、子任务和依赖；
- Scheduler 调度批次；
- Worker 分派和状态；
- 工具名称、调用状态和 Observation 摘要；
- 验收、重试、重规划和停止原因；
- 产物与最终结果；
- 跨轮上下文的加载和保存事件。

系统不保存模型逐字隐性思维链。逐字自述不可验证、成本高，也可能携带工具返回中的
注入内容。企业级可观测性应记录“做了什么、依据是什么、是否验收”，而不是无法复核的
内心独白。

## 8. 前端恢复与清晰度

切换或重新打开已有会话时，聊天页会：

1. 加载普通消息历史；
2. 查询该 Session 最近的 Agent Run；
3. 加载完整 Plan 和 Trace；
4. 插入“已恢复上一轮 Agent 执行”面板；
5. 标记 `SESSION MEMORY`；
6. 提供完整执行拓扑入口。

过程面板的主要可读性调整：

| 元素 | 改造前 | 改造后 |
|---|---:|---:|
| 面板最大宽度 | 760px | 940px |
| 标题 | 11px | 14px |
| 子任务正文 | 9px | 12px |
| Worker 列 | 72px / 8px | 116px / 10.5px |
| 事件标题 | 9px | 12.5px |
| 事件详情 | 8px | 11.5px |
| 展开高度 | 560px | 720px |

长节点名和事件详情允许换行，并提高弱文本对比度，解决截图中字体过小、模糊和截断的问题。

## 9. 与《红岩网校.md》的对应

| 要求 | 本次实现 |
|---|---|
| 模型、工具、Observation 多轮 Loop | Worker 独立工具循环并记录 Observation |
| 保存任务、工具调用和执行结果状态 | Trace + Plan Version + Handoff |
| Session 隔离 | Handoff 和 Run 均按 `session_id` 查询与删除 |
| 完整运行 Trace | 计划、调度、Worker、工具、验证、重规划、结束事件 |
| 统一模型和工具适配 | Direct/Agent 统一入口，知识库转为标准工具 |
| 运行过程回放 | 聊天页恢复最近 Run，工作流页查看完整 Trace |
| Token、时间、步骤预算 | `RuntimeBudget` 统一限制 |
| 清晰状态机 | Planner → Scheduler → Worker → Verify → Finalize |
| 工具能力修正 | Planner 修复 + Worker 三层工具白名单 |
| 动态 DAG 与安全并行 | Scheduler 只并行无依赖、低风险前沿 |
| 局部重规划 | 失败时保留已完成节点并更新计划版本 |
| 自动验证 | 子任务 Verifier 输出 completed/retry/adjust/failed |

仍需继续完成的考核项主要是：不少于 20 个独立自动评测任务，以及与简单 Agent Loop
在成功率、步骤数、耗时、恢复能力和工具调用数上的对照实验。

## 10. 关键实现文件

| 文件 | 职责 |
|---|---|
| `app/llm/llm_chain.py` | 统一 Direct/Agent 路由、续接判断、能力边界 |
| `app/agent/tools.py` | `knowledge_search` 与默认工具注册 |
| `app/agent/runtime.py` | 预算、状态、Handoff、Trace、工具策略 |
| `app/agent/agent_graph.py` | Planner、Scheduler、Finalize 和交接注入 |
| `app/agent/workers.py` | Worker 协议、隔离循环、三层工具授权 |
| `web/web_py.py` | Agent Run/Trace API 与 Session 级联清理 |
| `web/js/index.js` | 过程流渲染和上一轮执行恢复 |
| `web/js/terminal.js` | 可观测事件输出 |
| `web/css/workbench.css` | 过程面板可读性与响应式样式 |
| `tests/test_unified_runtime.py` | 顶层路由和知识库能力测试 |
| `tests/test_agent_runtime.py` | Handoff、Trace、计划和策略测试 |
| `tests/test_agent_workers.py` | Worker 隔离、并行和授权边界测试 |

## 11. 当前边界

1. Handoff 解决“任务轮次之间”的连续性，运行中进程崩溃后的断点续跑仍需持久化 LangGraph Checkpoint。
2. Handoff 使用有界摘要，不会把无限长工具输出重新塞入模型上下文。
3. 明确续接使用规则判断；隐含续接仍依赖 Router 模型，可能存在少量误判。
4. 知识库检索复用了现有向量库；索引质量、重排和引用精度仍取决于原 RAG 数据质量。
5. 当前已有单元测试覆盖核心协议，但还需要建设 20+ 任务的离线评测集和基线对照。

## 12. 可审计思考动态

Agent 执行时新增统一的 `reasoning_update` Trace 事件。它不是模型逐字隐性思维链，
而是由真实运行状态生成的公开工作摘要：

```text
理解 understand  -> 当前目标、上下文和能力需求
回忆 recall      -> 读取了哪些 Session Handoff
计划 plan        -> 规划策略、子任务数量、首批就绪节点
决策 decide      -> 为什么在当前子任务调用某个工具
观察 observe     -> 工具返回的有界、脱敏摘要
验证 verify      -> 验收状态、问题和证据数量
下一步 next      -> 调度批次、重试、重规划或汇总方向
汇总 summarize   -> 完成度、失败项和交接保存动作
```

事件 payload 包含 `phase`、`title`、`summary`、`next_action`、`status` 和可选
`subtask_id`。所有文本都会单行化、限制长度，并对 API Key、Token、Password、Secret、
Authorization、Bearer Token 和常见 `sk-` 密钥进行脱敏。

聊天页在执行面板顶部展示最近 9 条“思考动态”，完整技术事件仍在下方时间线；终端 Trace
也会显示相同事件。由于事件持久化在 Run Trace 中，重新打开 Session 后仍可回放。

## 13. 本次验证

- Python 全量单元测试：50 项全部通过。
- Python 核心模块编译检查通过。
- `index.js`、`terminal.js`、`workflow.js` 语法检查通过。
- 本地服务重启后，主页、配置、Session、Agent Run 和 Trace 接口正常响应。
- 浏览器实际恢复出最近一次 `SESSION MEMORY` 执行面板。
- 真实 Agent Run 生成并回放 9 条思考摘要，覆盖计划、决策、观察、验证、下一步与汇总。
- 1280×720 桌面视口无横向溢出。
- 390×844 移动视口中面板保持可见、9 条摘要完整渲染且无横向溢出。
