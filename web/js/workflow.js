(function () {
    const POS_KEY = 'planner_scheduler_worker_positions_v4';
    const NODE_W = 190;
    const NODE_H = 86;
    const REFRESH_INTERVAL = 8000;

    const state = {
        nodes: [],
        edges: [],
        runs: [],
        trace: null,
        runtime: null,
        selectedId: 'planner',
        selectedRunId: '',
        selectedPlanVersion: null,
        filter: 'all',
        positions: readPositions(),
        live: true,
        refreshTimer: null,
    };

    const els = {};
    const typeLabel = {
        core: 'CORE',
        skill: 'SKILL',
        tool: 'TOOL',
        trace: 'TRACE',
    };
    const nodeAliases = {
        planning: 'planner',
        planner: 'planner',
        scheduler: 'scheduler',
        skill_select: 'worker_registry',
        skillselect: 'worker_registry',
        research_worker: 'workers',
        browser_worker: 'workers',
        execution_worker: 'workers',
        verification_worker: 'workers',
        general_worker: 'workers',
        executor: 'workers',
        tool: 'workers',
        tools: 'tools_box',
        reflection: 'scheduler',
        verifier: 'scheduler',
        collaboration: 'scheduler',
        finalize: 'finalize',
        trace: 'trace',
    };

    const baseNodes = [
        { id: 'input', type: 'core', title: '用户输入', subtitle: '目标 / 上下文 / 约束', x: 70, y: 180, desc: '接收用户消息、图片和任务约束，创建隔离的运行状态。' },
        { id: 'planner', type: 'core', title: 'Planner', subtitle: '结构化拆解与预算', x: 278, y: 62, desc: '生成带依赖、验收条件、风险级别和工具策略的结构化子任务 DAG。' },
        { id: 'scheduler', type: 'core', title: 'Scheduler', subtitle: 'DAG 前沿与风险调度', x: 520, y: 178, desc: '选择可执行前沿；只并行无写冲突的低风险任务，并归并结构化 WorkerResult。' },
        { id: 'worker_registry', type: 'core', title: 'Worker Registry', subtitle: '按能力选择执行者', x: 282, y: 350, desc: '根据 task_type、skill 和工具能力选择 Research、Browser、Execution、Verification 或 General Worker。' },
        { id: 'workers', type: 'core', title: 'Isolated Workers', subtitle: '独立上下文真实执行', x: 766, y: 292, desc: '每个子任务拥有独立消息和最小工具集合，返回证据、产物、验收状态及资源用量。' },
        { id: 'skills_box', type: 'skill', title: 'Skills Library', subtitle: '已注册技能', x: 70, y: 466, desc: '同步展示当前项目注册的 Skills；Worker 只加载当前子任务需要的技能。' },
        { id: 'tools_box', type: 'tool', title: 'Tools Library', subtitle: '可调用工具集合', x: 930, y: 72, desc: '基础工具和 Skill 附带工具的统一视图，实际调用受 Worker 与子任务双重白名单限制。' },
        { id: 'finalize', type: 'core', title: 'Finalize', subtitle: '结果汇总与交付', x: 938, y: 488, desc: '汇总结构化结果、证据、产物和停止原因，生成最终用户结果。' },
        { id: 'trace', type: 'trace', title: 'Agent Trace', subtitle: '事件 / 计划版本 / 审计', x: 68, y: 474, desc: '持久化运行事件、计划版本、指标和工具策略，支持回放与审计。' },
    ];

    const baseEdges = [
        ['input', 'planner', 'core'],
        ['planner', 'scheduler', 'core'],
        ['scheduler', 'worker_registry', 'core'],
        ['worker_registry', 'workers', 'core'],
        ['skills_box', 'worker_registry', 'capability'],
        ['workers', 'tools_box', 'capability'],
        ['workers', 'scheduler', 'core'],
        ['scheduler', 'planner', 'core'],
        ['scheduler', 'finalize', 'core'],
        ['workers', 'trace', 'trace'],
        ['scheduler', 'trace', 'trace'],
        ['finalize', 'trace', 'trace'],
    ];

    function $(id) {
        return document.getElementById(id);
    }

    function readPositions() {
        try {
            return JSON.parse(localStorage.getItem(POS_KEY) || '{}');
        } catch (_) {
            return {};
        }
    }

    function savePositions() {
        localStorage.setItem(POS_KEY, JSON.stringify(state.positions));
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }

    function normalizeList(value) {
        if (!value) return [];
        if (Array.isArray(value)) return value;
        if (typeof value === 'object') return Object.values(value);
        return [];
    }

    function itemName(item) {
        if (typeof item === 'string') return item;
        return item?.name || item?.id || item?.tool || item?.skill || item?.label || 'unnamed';
    }

    function itemDesc(item) {
        if (!item || typeof item === 'string') return '';
        return item.description || item.summary || item.category || item.source || '';
    }

    function collectTools(skills, directTools) {
        const toolMap = new Map();
        directTools.forEach((tool) => {
            const name = itemName(tool);
            toolMap.set(name, { ...tool, name, source: tool.source || 'base_tools' });
        });
        skills.forEach((skill) => {
            normalizeList(skill.tools).forEach((tool) => {
                const name = itemName(tool);
                const item = typeof tool === 'string' ? { name } : tool;
                toolMap.set(name, { ...item, name, source: itemName(skill) });
            });
        });
        return Array.from(toolMap.values()).sort((a, b) => itemName(a).localeCompare(itemName(b)));
    }

    function applySavedPosition(node) {
        const saved = state.positions[node.id];
        if (!saved) return node;
        return { ...node, x: Number(saved.x) || node.x, y: Number(saved.y) || node.y };
    }

    function runtimeConfig() {
        return state.runtime?.budget || state.runtime?.config || {};
    }

    function buildGraph(payload, runtime) {
        state.runtime = runtime;
        const skills = normalizeList(payload.skills).sort((a, b) => itemName(a).localeCompare(itemName(b)));
        const tools = collectTools(skills, normalizeList(payload.tools || payload.base_tools));
        const budget = runtimeConfig();

        state.nodes = baseNodes.map((node) => {
            const next = { ...node };
            if (next.id === 'planner' && Object.keys(budget).length) {
                next.meta = budget;
                next.subtitle = `${budget.agent_max_steps || '-'} steps / ${formatCompact(budget.agent_max_tokens)} tokens`;
            }
            if (next.id === 'skills_box') {
                next.items = skills;
                next.subtitle = `${skills.length} registered skills`;
                next.meta = { total_skills: skills.length };
            }
            if (next.id === 'tools_box') {
                next.items = tools;
                next.subtitle = `${tools.length} available tools`;
                next.meta = { total_tools: tools.length };
            }
            return applySavedPosition(next);
        });

        state.edges = baseEdges.map(([from, to, type]) => ({ from, to, type }));
        updateCanvasSize();
    }

    function updateCanvasSize() {
        const maxX = Math.max(...state.nodes.map((node) => node.x), 980);
        const maxY = Math.max(...state.nodes.map((node) => node.y), 560);
        const width = maxX + 280;
        const height = maxY + 170;
        els.canvas.style.width = `${width}px`;
        els.canvas.style.height = `${height}px`;
        els.edgeLayer.setAttribute('width', String(width));
        els.edgeLayer.setAttribute('height', String(height));
        els.edgeLayer.setAttribute('viewBox', `0 0 ${width} ${height}`);
    }

    function isVisible(node) {
        return state.filter === 'all' || node.type === state.filter;
    }

    function observedNodes() {
        const events = normalizeList(state.trace?.events);
        return new Set(events.map((event) => normalizeNodeId(event.node)).filter(Boolean));
    }

    function activeNode() {
        if (state.trace?.run?.status !== 'running') return '';
        const events = normalizeList(state.trace.events);
        for (let index = events.length - 1; index >= 0; index -= 1) {
            const id = normalizeNodeId(events[index].node);
            if (id) return id;
        }
        return 'planner';
    }

    function normalizeNodeId(value) {
        const key = String(value || '').trim().toLowerCase();
        return nodeAliases[key] || (state.nodes.some((node) => node.id === key) ? key : '');
    }

    function render() {
        updateCanvasSize();
        renderEdges();
        renderNodes();
        renderDetail();
        renderOverview();
        renderRuns();
        renderPlan();
    }

    function renderEdges() {
        const byId = new Map(state.nodes.map((node) => [node.id, node]));
        const visibleIds = new Set(state.nodes.filter(isVisible).map((node) => node.id));
        const observed = observedNodes();
        const active = activeNode();
        const paths = state.edges
            .filter((edge) => byId.has(edge.from) && byId.has(edge.to))
            .filter((edge) => state.filter === 'all' || visibleIds.has(edge.from) || visibleIds.has(edge.to))
            .map((edge) => {
                const from = byId.get(edge.from);
                const to = byId.get(edge.to);
                const start = anchorFor(from, to);
                const end = anchorFor(to, from, true);
                const dx = Math.abs(end.x - start.x);
                const dy = Math.abs(end.y - start.y);
                const bend = Math.max(42, Math.min(150, (dx + dy) * 0.34));
                const selected = edge.from === state.selectedId || edge.to === state.selectedId;
                const hasObserved = observed.has(edge.from) && observed.has(edge.to);
                const isLive = active && (edge.to === active || (edge.from === active && edge.type !== 'trace'));
                const d = `M ${start.x} ${start.y} C ${start.x + bend} ${start.y}, ${end.x - bend} ${end.y}, ${end.x} ${end.y}`;
                const classes = [
                    'edge',
                    `edge-${edge.type}`,
                    selected ? 'selected' : '',
                    hasObserved ? 'observed' : '',
                    isLive ? 'live' : '',
                ].filter(Boolean).join(' ');
                return `<path class="${classes}" d="${d}" />`;
            }).join('');

        els.edgeLayer.innerHTML = `
            <defs>
                <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
                    <path d="M0,0 L0,6 L8,3 z" class="edge-arrow"></path>
                </marker>
            </defs>
            ${paths}
        `;
    }

    function anchorFor(node, other, isTarget) {
        const nodeWidth = node.items ? 260 : NODE_W;
        const otherWidth = other.items ? 260 : NODE_W;
        const nodeHeight = node.items ? 210 : NODE_H;
        const cx = node.x + nodeWidth / 2;
        const cy = node.y + nodeHeight / 2;
        const ocx = other.x + otherWidth / 2;
        const ocy = other.y + (other.items ? 210 : NODE_H) / 2;
        const horizontal = Math.abs(ocx - cx) >= Math.abs(ocy - cy);
        if (horizontal) {
            return {
                x: node.x + (ocx > cx ? nodeWidth : 0) + (isTarget ? (ocx > cx ? -2 : 2) : 0),
                y: cy,
            };
        }
        return {
            x: cx,
            y: node.y + (ocy > cy ? nodeHeight : 0) + (isTarget ? (ocy > cy ? -2 : 2) : 0),
        };
    }

    function renderNodes() {
        const observed = observedNodes();
        const active = activeNode();
        els.nodeLayer.innerHTML = state.nodes.map((node, index) => {
            const selected = node.id === state.selectedId;
            const muted = !isVisible(node);
            const items = normalizeList(node.items);
            const list = items.length ? `
                <div class="node-items">
                    ${items.slice(0, 18).map((item) => `
                        <span class="node-item">${escapeHtml(itemName(item))}</span>
                    `).join('')}
                    ${items.length > 18 ? `<span class="node-item more">+${items.length - 18}</span>` : ''}
                </div>
            ` : '';
            const classes = [
                'workflow-node', node.type, items.length ? 'group-node' : '', selected ? 'selected' : '',
                muted ? 'muted' : '', observed.has(node.id) ? 'observed' : '', active === node.id ? 'live' : '',
            ].filter(Boolean).join(' ');
            return `
                <button class="${classes}" type="button" data-id="${escapeHtml(node.id)}"
                    style="left:${node.x}px; top:${node.y}px; animation-delay:${Math.min(index * 22, 180)}ms">
                    <span class="drag-handle" aria-hidden="true"></span>
                    <span class="node-type">${escapeHtml(typeLabel[node.type] || node.type)}</span>
                    <strong>${escapeHtml(node.title)}</strong>
                    <small>${escapeHtml(node.subtitle)}</small>
                    ${list}
                </button>
            `;
        }).join('');

        els.nodeLayer.querySelectorAll('.workflow-node').forEach(bindNodePointer);
    }

    function bindNodePointer(button) {
        let drag = null;
        button.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            const node = state.nodes.find((item) => item.id === button.dataset.id);
            if (!node) return;
            drag = {
                node,
                startX: event.clientX,
                startY: event.clientY,
                nodeX: node.x,
                nodeY: node.y,
                moved: false,
            };
            button.setPointerCapture(event.pointerId);
            button.classList.add('dragging');
        });

        button.addEventListener('pointermove', (event) => {
            if (!drag) return;
            const nextX = Math.max(20, drag.nodeX + event.clientX - drag.startX);
            const nextY = Math.max(20, drag.nodeY + event.clientY - drag.startY);
            if (Math.abs(nextX - drag.nodeX) > 2 || Math.abs(nextY - drag.nodeY) > 2) drag.moved = true;
            drag.node.x = nextX;
            drag.node.y = nextY;
            state.positions[drag.node.id] = { x: Math.round(nextX), y: Math.round(nextY) };
            button.style.left = `${nextX}px`;
            button.style.top = `${nextY}px`;
            renderEdges();
        });

        button.addEventListener('pointerup', (event) => {
            if (!drag) return;
            button.releasePointerCapture(event.pointerId);
            button.classList.remove('dragging');
            savePositions();
            if (!drag.moved) {
                state.selectedId = button.dataset.id;
                renderEdges();
                renderNodes();
                renderDetail();
            }
            drag = null;
        });

        button.addEventListener('pointercancel', () => {
            button.classList.remove('dragging');
            drag = null;
        });
    }

    function renderDetail() {
        const node = state.nodes.find((item) => item.id === state.selectedId) || state.nodes[0];
        if (!node) return;
        const observed = observedNodes();
        const active = activeNode();

        els.detailType.textContent = typeLabel[node.type] || node.type;
        els.detailTitle.textContent = node.title;
        els.detailDesc.textContent = node.desc || node.subtitle || '';

        const linked = state.edges
            .filter((edge) => edge.from === node.id || edge.to === node.id)
            .map((edge) => edge.from === node.id ? edge.to : edge.from)
            .map((id) => state.nodes.find((item) => item.id === id))
            .filter(Boolean);

        const runState = active === node.id ? '正在执行' : observed.has(node.id) ? '本次已经过' : '未经过';
        const metaRows = [
            ['节点 ID', node.id],
            ['运行状态', state.trace ? runState : '未选择 Run'],
            ['连接数', linked.length],
        ];
        if (node.meta) {
            Object.keys(node.meta).slice(0, 5).forEach((key) => metaRows.push([humanizeKey(key), formatMeta(node.meta[key])]));
        }

        els.detailMeta.innerHTML = metaRows.map(([key, value]) => `
            <div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>
        `).join('');

        const items = normalizeList(node.items);
        const itemList = items.length ? `
            <div>
                <div class="detail-section-title">同步能力</div>
                <div class="detail-item-list">
                    ${items.map((item) => `
                        <div class="detail-item">
                            <b>${escapeHtml(itemName(item))}</b>
                            <span>${escapeHtml(itemDesc(item) || '暂无描述')}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        ` : '';

        els.detailLinks.innerHTML = `
            <div>
                <div class="detail-section-title">连接节点</div>
                <div class="detail-node-links">
                    ${linked.length ? linked.map((item) => `<button type="button" data-id="${escapeHtml(item.id)}">${escapeHtml(item.title)}</button>`).join('') : '<span>暂无直接连接</span>'}
                </div>
            </div>
            ${itemList}
        `;

        els.detailLinks.querySelectorAll('button').forEach((button) => {
            button.addEventListener('click', () => {
                state.selectedId = button.dataset.id;
                renderEdges();
                renderNodes();
                renderDetail();
                const target = els.nodeLayer.querySelector(`[data-id="${CSS.escape(state.selectedId)}"]`);
                target?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' });
            });
        });
    }

    function renderOverview() {
        const budget = runtimeConfig();
        const finished = state.runs.filter((run) => run.status && run.status !== 'running');
        const completed = finished.filter((run) => run.status === 'completed').length;
        const successRate = finished.length ? `${Math.round(completed / finished.length * 100)}%` : '--';
        els.metricRuns.textContent = String(state.runs.length);
        els.metricSuccess.textContent = successRate;
        els.metricSteps.textContent = String(budget.agent_max_steps ?? '--');
        els.metricParallel.textContent = String(budget.planner_parallelism ?? '--');

        const currentRun = state.trace?.run;
        if (currentRun) {
            const status = statusLabel(currentRun.status);
            els.status.textContent = `${status} · ${shortId(currentRun.run_id)}`;
            els.subtitle.textContent = currentRun.goal || '查看任务如何被拆解、执行、验证和重新规划。';
        }
    }

    function renderRuns() {
        els.runsCount.textContent = String(state.runs.length);
        if (!state.runs.length) {
            els.runsList.innerHTML = '<div class="empty-state">还没有 Agent 运行记录。<br>从聊天页发起一个任务后，这里会自动出现。</div>';
            return;
        }
        els.runsList.innerHTML = state.runs.map((run) => {
            const metrics = run.metrics || {};
            const steps = metrics.step_count ?? metrics.steps ?? 0;
            const elapsed = formatDuration(metrics.elapsed_seconds);
            return `
                <button class="run-item ${run.run_id === state.selectedRunId ? 'active' : ''}" type="button" data-run-id="${escapeHtml(run.run_id)}">
                    <span class="run-status-dot run-status-${escapeHtml(run.status || 'unknown')}"></span>
                    <span class="run-copy">
                        <span class="run-goal">${escapeHtml(run.goal || '未命名任务')}</span>
                        <span class="run-meta">${escapeHtml(statusLabel(run.status))} · ${steps} steps${elapsed ? ` · ${elapsed}` : ''}</span>
                    </span>
                    <time class="run-time">${escapeHtml(formatRelativeTime(run.started_at))}</time>
                </button>
            `;
        }).join('');

        els.runsList.querySelectorAll('.run-item').forEach((button) => {
            button.addEventListener('click', () => selectRun(button.dataset.runId));
        });
    }

    function renderPlan() {
        const plans = normalizeList(state.trace?.plans);
        if (!plans.length) {
            els.planVersions.innerHTML = '';
            els.planSummary.innerHTML = '<div class="plan-goal">选择一个包含结构化计划的运行记录。</div>';
            els.subtaskList.innerHTML = '<div class="empty-state">当前 Run 暂无计划版本。</div>';
            return;
        }

        const selected = plans.find((plan) => Number(plan.version) === Number(state.selectedPlanVersion)) || plans[plans.length - 1];
        state.selectedPlanVersion = Number(selected.version || plans.length);
        els.planVersions.innerHTML = plans.map((plan) => `
            <button class="plan-version-btn ${Number(plan.version) === state.selectedPlanVersion ? 'active' : ''}" type="button" data-version="${escapeHtml(plan.version)}">
                v${escapeHtml(plan.version || 1)}
            </button>
        `).join('');

        const subtasks = normalizeList(selected.subtasks);
        const graphNodes = new Map(normalizeList(selected.state_graph?.nodes).map((node) => [String(node.id), node]));
        const completed = subtasks.filter((task) => task.status === 'completed').length;
        const failed = subtasks.filter((task) => task.status === 'failed').length;
        const ready = normalizeList(selected.ready_subtasks).length;
        els.planSummary.innerHTML = `
            <div class="plan-goal" title="${escapeHtml(selected.goal || '')}">${escapeHtml(selected.goal || '未命名计划')}</div>
            <span class="plan-stat"><b>${subtasks.length}</b>任务</span>
            <span class="plan-stat"><b>${completed}</b>完成</span>
            <span class="plan-stat"><b>${failed || ready}</b>${failed ? '失败' : '就绪'}</span>
        `;

        els.subtaskList.innerHTML = subtasks.length ? subtasks.map((task) => {
            const graphNode = graphNodes.get(String(task.id));
            const status = graphNode?.state || task.status || 'pending';
            const dependencies = normalizeList(task.depends_on);
            const tools = normalizeList(task.allowed_tools);
            return `
                <article class="subtask-item status-${escapeHtml(status)}">
                    <div class="subtask-top">
                        <span class="subtask-id">TASK ${escapeHtml(task.id)}</span>
                        <span class="subtask-status">${escapeHtml(statusLabel(status))}</span>
                    </div>
                    <div class="subtask-title">${escapeHtml(task.desc || task.description || '未命名子任务')}</div>
                    <div class="subtask-meta">
                        <span>${escapeHtml(task.task_type || 'general')}</span>
                        <span class="risk-${escapeHtml(task.risk_level || 'low')}">${escapeHtml(task.risk_level || 'low')} risk</span>
                        <span>${dependencies.length ? `依赖 ${dependencies.join(', ')}` : '无依赖'}</span>
                        ${tools.length ? `<span>${tools.length} tools</span>` : ''}
                    </div>
                </article>
            `;
        }).join('') : '<div class="empty-state">这个计划没有子任务。</div>';

        els.planVersions.querySelectorAll('.plan-version-btn').forEach((button) => {
            button.addEventListener('click', () => {
                state.selectedPlanVersion = Number(button.dataset.version);
                renderPlan();
            });
        });
    }

    function formatMeta(value) {
        if (Array.isArray(value)) return `${value.length} 项`;
        if (value && typeof value === 'object') return JSON.stringify(value);
        if (value === undefined || value === null || value === '') return '-';
        return String(value);
    }

    function humanizeKey(value) {
        const labels = {
            agent_max_steps: '最大步骤',
            agent_max_runtime_seconds: '最大耗时',
            agent_max_tokens: 'Token 预算',
            planner_enabled: '规划器',
            planner_parallelism: '并行度',
            trace_enabled: 'Trace',
            total_skills: 'Skills',
            total_tools: 'Tools',
        };
        return labels[value] || value.replaceAll('_', ' ');
    }

    function statusLabel(value) {
        const labels = {
            completed: '已完成',
            running: '运行中',
            failed: '失败',
            stopped: '已停止',
            pending: '等待中',
            waiting: '等待中',
            ready: '就绪',
            done: '已完成',
            blocked: '被阻塞',
            needs_replan: '需重规划',
            retry: '重试',
            adjust: '调整计划',
            skipped: '已跳过',
        };
        return labels[value] || value || '未知';
    }

    function shortId(value) {
        const text = String(value || '');
        return text.length > 15 ? `${text.slice(0, 8)}…${text.slice(-4)}` : text;
    }

    function formatCompact(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return '--';
        if (number >= 1000) return `${Math.round(number / 100) / 10}k`;
        return String(number);
    }

    function formatDuration(value) {
        const seconds = Number(value);
        if (!Number.isFinite(seconds) || seconds <= 0) return '';
        if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
        return `${seconds.toFixed(1)}s`;
    }

    function formatRelativeTime(value) {
        if (!value) return '';
        const timestamp = new Date(value).getTime();
        if (!Number.isFinite(timestamp)) return '';
        const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
        if (seconds < 60) return '刚刚';
        if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
        return new Date(timestamp).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
    }

    async function fetchJson(url) {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`${url} ${response.status}`);
        return response.json();
    }

    async function selectRun(runId, options = {}) {
        if (!runId) return;
        state.selectedRunId = runId;
        localStorage.setItem('latest_agent_run_id', runId);
        els.terminalLink.href = `terminal.html?run_id=${encodeURIComponent(runId)}`;
        if (!options.silent) renderRuns();
        try {
            const trace = await fetchJson(`${API_BASE}/agent/trace/${encodeURIComponent(runId)}`);
            if (trace.status === 'error') throw new Error(trace.msg || 'Trace 加载失败');
            state.trace = trace;
            const plans = normalizeList(trace.plans);
            state.selectedPlanVersion = plans.length ? Number(plans[plans.length - 1].version || plans.length) : null;
            render();
        } catch (error) {
            els.status.textContent = 'Trace 暂不可用';
            els.subtitle.textContent = error.message;
        }
    }

    async function loadData(options = {}) {
        if (els.refresh.classList.contains('loading')) return;
        els.refresh.classList.add('loading');
        try {
            const [payload, runtime, runsPayload] = await Promise.all([
                fetchJson(`${API_BASE}/tools-and-skills`),
                fetchJson(`${API_BASE}/agent/runtime-config`),
                fetchJson(`${API_BASE}/agent/runs?limit=12`),
            ]);
            state.runs = normalizeList(runsPayload.runs);
            buildGraph(payload, runtime);
            els.systemIndicator.className = 'system-indicator online';
            els.status.textContent = 'Agent Runtime 已连接';

            const queryRunId = new URLSearchParams(window.location.search).get('run_id');
            const cachedRunId = localStorage.getItem('latest_agent_run_id');
            const preferred = state.selectedRunId || queryRunId || cachedRunId || state.runs[0]?.run_id || '';
            const selectedExists = state.runs.some((run) => run.run_id === preferred);
            const nextRunId = selectedExists ? preferred : state.runs[0]?.run_id || preferred;
            if (nextRunId) {
                await selectRun(nextRunId, { silent: true });
            } else {
                state.trace = null;
                render();
            }
        } catch (error) {
            if (!state.nodes.length) buildGraph({ skills: [], tools: [] }, null);
            els.systemIndicator.className = 'system-indicator offline';
            els.status.textContent = '离线结构图';
            els.subtitle.textContent = `Agent Runtime 暂不可用：${error.message}`;
            render();
        } finally {
            els.refresh.classList.remove('loading');
            if (!options.background) scheduleRefresh();
        }
    }

    function scheduleRefresh() {
        if (state.refreshTimer) clearTimeout(state.refreshTimer);
        if (!state.live) return;
        state.refreshTimer = setTimeout(() => {
            if (document.visibilityState === 'visible') loadData({ background: true });
            else scheduleRefresh();
        }, REFRESH_INTERVAL);
    }

    function toggleLive() {
        state.live = !state.live;
        els.live.classList.toggle('active', state.live);
        els.live.setAttribute('aria-pressed', String(state.live));
        if (state.live) loadData();
        else if (state.refreshTimer) clearTimeout(state.refreshTimer);
    }

    function bindEvents() {
        els.refresh.addEventListener('click', () => loadData());
        els.live.addEventListener('click', toggleLive);
        document.querySelectorAll('.filter-btn').forEach((button) => {
            button.addEventListener('click', () => {
                state.filter = button.dataset.filter;
                document.querySelectorAll('.filter-btn').forEach((item) => item.classList.toggle('active', item === button));
                renderEdges();
                renderNodes();
            });
        });
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && state.live) loadData({ background: true });
        });
    }

    function init() {
        els.canvas = $('map-canvas');
        els.edgeLayer = $('edge-layer');
        els.nodeLayer = $('node-layer');
        els.status = $('workflow-status');
        els.subtitle = $('workflow-subtitle');
        els.systemIndicator = $('system-indicator');
        els.refresh = $('refresh-btn');
        els.live = $('live-btn');
        els.terminalLink = $('terminal-link');
        els.detailType = $('detail-type');
        els.detailTitle = $('detail-title');
        els.detailDesc = $('detail-desc');
        els.detailMeta = $('detail-meta');
        els.detailLinks = $('detail-links');
        els.metricRuns = $('metric-runs');
        els.metricSuccess = $('metric-success');
        els.metricSteps = $('metric-steps');
        els.metricParallel = $('metric-parallel');
        els.runsCount = $('runs-count');
        els.runsList = $('runs-list');
        els.planVersions = $('plan-versions');
        els.planSummary = $('plan-summary');
        els.subtaskList = $('subtask-list');
        bindEvents();
        loadData();
    }

    document.addEventListener('DOMContentLoaded', init);
})();
