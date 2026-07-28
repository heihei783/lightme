document.addEventListener('DOMContentLoaded', () => {
    const output = document.getElementById('term-output');
    const statusEl = document.getElementById('term-status');
    const approvalBar = document.getElementById('shell-approval-bar');
    const shellCmdEl = document.getElementById('shell-bar-cmd');
    const tracePanel = document.getElementById('trace-panel');
    const traceToggleBtn = document.getElementById('trace-toggle-btn');
    const traceRefreshBtn = document.getElementById('trace-refresh-btn');
    const traceRunIdEl = document.getElementById('trace-run-id');
    const traceStatusEl = document.getElementById('trace-status');
    const traceStepsEl = document.getElementById('trace-steps');
    const traceTimeEl = document.getElementById('trace-time');
    const traceTokensEl = document.getElementById('trace-tokens');
    const tracePlanCountEl = document.getElementById('trace-plan-count');
    const tracePlansEl = document.getElementById('trace-plans');
    const traceEventsEl = document.getElementById('trace-events');
    const traceFlowEl = document.getElementById('trace-flow');
    const traceDagEl = document.getElementById('trace-dag');
    const traceTimelineEl = document.getElementById('trace-timeline');
    const traceDetailEl = document.getElementById('trace-detail');
    const traceZoomOverlay = document.getElementById('trace-zoom-overlay');
    const traceZoomTitle = document.getElementById('trace-zoom-title');
    const traceZoomContent = document.getElementById('trace-zoom-content');
    const traceZoomClose = document.getElementById('trace-zoom-close');
    let currentApprovalId = null;
    let approvalTimer = null;
    let connectCount = 0;
    let currentRunId = new URLSearchParams(window.location.search).get('run_id') || '';
    let lastTraceRefreshAt = 0;
    let pendingTraceTimer = null;
    let lastTraceEvents = [];
    let traceHidden = localStorage.getItem('terminal_trace_hidden') === 'true';

    function setStatus(text, cls) {
        statusEl.textContent = text;
        statusEl.className = 'term-status ' + (cls || '');
    }

    // ==================== 渲染 ====================
    function timeStr(ts) {
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString('zh-CN', { hour12: false });
    }

    function senderClass(sender) {
        const m = {
            'Planning': 'planning', 'SkillSelect': 'skillselect',
            'Executor': 'executor', 'Reflection': 'reflection',
            'Finalize': 'finalize', 'Tool': 'tool', 'Error': 'error',
            'Shell': 'planning'
        };
        return 'term-sender-' + (m[sender] || 'planning');
    }

    function appendLine(html) {
        const div = document.createElement('div');
        div.className = 'term-line';
        div.innerHTML = html;
        output.appendChild(div);
        output.scrollTop = output.scrollHeight;
    }

    function applyTraceVisibility() {
        tracePanel.classList.toggle('is-hidden', traceHidden);
        traceToggleBtn.textContent = traceHidden ? '显示 Trace' : '隐藏 Trace';
        traceToggleBtn.setAttribute('aria-pressed', String(!traceHidden));
        traceRefreshBtn.style.display = traceHidden ? 'none' : '';
    }

    function toggleTraceVisibility() {
        traceHidden = !traceHidden;
        localStorage.setItem('terminal_trace_hidden', String(traceHidden));
        applyTraceVisibility();
        if (!traceHidden) loadTrace();
    }

    // ==================== Trace 回放 ====================
    function parseServerTime(s) {
        if (!s) return null;
        const d = new Date(String(s).replace(' ', 'T'));
        return Number.isNaN(d.getTime()) ? null : d;
    }

    function elapsedSeconds(run) {
        const start = parseServerTime(run.started_at);
        const end = parseServerTime(run.finished_at) || new Date();
        if (!start) return 0;
        return Math.max(0, Math.round((end - start) / 1000));
    }

    function setTraceEmpty(message) {
        traceRunIdEl.textContent = message || '暂无 Agent 运行';
        traceStatusEl.textContent = '状态 -';
        traceStepsEl.textContent = '步数 -';
        traceTimeEl.textContent = '耗时 -';
        traceTokensEl.textContent = 'Tokens -';
        tracePlanCountEl.textContent = '计划 -';
        tracePlansEl.innerHTML = '<div class="trace-muted">暂无计划版本</div>';
        traceEventsEl.innerHTML = '<div class="trace-muted">暂无结构化事件</div>';
        traceFlowEl.innerHTML = '<div class="trace-muted">暂无执行流</div>';
        traceDagEl.innerHTML = '<div class="trace-muted">暂无动态 DAG</div>';
        traceTimelineEl.innerHTML = '<div class="trace-muted">暂无事件时间线</div>';
        traceDetailEl.textContent = '选择事件查看 payload';
    }

    async function resolveLatestRunId() {
        if (currentRunId) return currentRunId;
        const resp = await fetch(API_BASE + '/agent/runs?limit=1');
        const data = await resp.json();
        if (data.status === 'success' && data.runs && data.runs.length > 0) {
            currentRunId = data.runs[0].run_id;
            localStorage.setItem('latest_agent_run_id', currentRunId);
        }
        return currentRunId;
    }

    async function loadTrace(runIdOverride) {
        try {
            traceRefreshBtn.disabled = true;
            if (runIdOverride) currentRunId = runIdOverride;
            const runId = runIdOverride || await resolveLatestRunId();
            if (!runId) {
                setTraceEmpty('暂无 Agent 运行');
                return;
            }
            const resp = await fetch(`${API_BASE}/agent/trace/${encodeURIComponent(runId)}`);
            const data = await resp.json();
            if (data.status !== 'success') {
                setTraceEmpty(data.msg || 'Trace 加载失败');
                return;
            }
            renderTrace(data);
        } catch (e) {
            setTraceEmpty('Trace 加载失败: ' + String(e));
        } finally {
            traceRefreshBtn.disabled = false;
        }
    }

    function scheduleTraceRefresh(runId, force) {
        if (!runId || traceHidden) return;
        currentRunId = runId;
        localStorage.setItem('latest_agent_run_id', currentRunId);
        const now = Date.now();
        if (force || now - lastTraceRefreshAt > 1400) {
            lastTraceRefreshAt = now;
            loadTrace(runId);
            return;
        }
        if (pendingTraceTimer) return;
        pendingTraceTimer = setTimeout(() => {
            pendingTraceTimer = null;
            lastTraceRefreshAt = Date.now();
            loadTrace(currentRunId);
        }, 1400);
    }

    function renderTrace(data) {
        const run = data.run || {};
        const plans = data.plans || [];
        const events = data.events || [];
        lastTraceEvents = events;
        const metrics = run.metrics || {};
        currentRunId = run.run_id || currentRunId;
        if (currentRunId) localStorage.setItem('latest_agent_run_id', currentRunId);
        traceRunIdEl.textContent = `${run.run_id || '-'} · session ${run.session_id || '-'}`;
        traceStatusEl.textContent = `状态 ${labelStatus(run.status)}`;
        traceStepsEl.textContent = `步数 ${metrics.step_count ?? '-'}`;
        traceTimeEl.textContent = `耗时 ${elapsedSeconds(run)}s`;
        traceTokensEl.textContent = `Tokens ${metrics.token_count ?? '-'}`;
        tracePlanCountEl.textContent = `计划 ${plans.length}`;

        renderTraceFlow(plans);
        renderTraceDag(plans, events);
        renderTraceTimeline(events);

        tracePlansEl.innerHTML = plans.map(plan => {
            const subtasks = plan.subtasks || [];
            const completed = subtasks.filter(st => st.status === 'completed').length;
            const failed = subtasks.filter(st => st.status === 'failed').length;
            const ready = (plan.ready_subtasks || []).join(', ') || '-';
            const validation = (plan.validation_errors || []).join('; ');
            return `
                <div class="trace-card">
                    <div class="trace-card-title">v${esc(String(plan.version || 1))} · ${esc(plan.plan_id || '')}</div>
                    <div class="trace-card-line">目标: ${esc(plan.goal || '')}</div>
                    <div class="trace-card-line">子任务: ${completed}/${subtasks.length} 完成，${failed} 失败，ready: ${esc(ready)}</div>
                    <div class="trace-task-list">${subtasks.slice(0, 4).map(st => `
                        <span class="trace-task-pill trace-task-${escAttr(st.status || 'pending')}">${esc(String(st.id))} · ${esc(st.status || 'pending')}</span>
                    `).join('')}</div>
                    ${validation ? `<div class="trace-card-warn">校验修复: ${esc(validation)}</div>` : ''}
                </div>`;
        }).join('') || '<div class="trace-muted">暂无计划版本</div>';

        const keyEvents = events.filter(ev => [
            'run_started', 'plan_created', 'plan_replanned', 'skill_selected',
            'tool_call_requested', 'loop_detected', 'budget_stop',
            'subtask_reviewed', 'run_finalized'
        ].includes(ev.event_type));
        const visibleKeyEvents = keyEvents.slice(-18);
        traceEventsEl.innerHTML = visibleKeyEvents.map((ev, index) => {
            const payload = ev.payload || {};
            const summary = summarizeEvent(ev.event_type, payload);
            return `
                <button type="button" class="trace-event" data-index="${index}">
                    <span class="trace-event-type">${esc(labelEvent(ev.event_type))}</span>
                    <span class="trace-event-node">${esc(labelNode(ev.node || ''))}</span>
                    <span class="trace-event-text">${esc(summary)}</span>
                </button>`;
        }).join('') || '<div class="trace-muted">暂无结构化事件</div>';

        traceEventsEl.querySelectorAll('.trace-event').forEach(btn => {
            btn.onclick = () => {
                traceEventsEl.querySelectorAll('.trace-event').forEach(item => item.classList.remove('selected'));
                btn.classList.add('selected');
                const ev = visibleKeyEvents[Number(btn.dataset.index)];
                traceDetailEl.textContent = JSON.stringify(ev || {}, null, 2);
            };
        });
    }

    function renderTraceFlow(plans) {
        const latest = plans[plans.length - 1];
        const graph = latest?.state_graph || findLatestStateGraph(lastTraceEvents);
        const subtasks = latest?.subtasks || [];
        const graphNodes = graph?.nodes || [];
        if (!subtasks.length && !graphNodes.length) {
            traceFlowEl.innerHTML = '<div class="trace-muted">暂无计划节点</div>';
            return;
        }

        const total = graph?.summary?.total ?? subtasks.length;
        const completed = graph?.summary?.completed ?? subtasks.filter(st => st.status === 'completed').length;
        const failed = graph?.summary?.failed ?? subtasks.filter(st => st.status === 'failed').length;
        const active = graphNodes.find(node => ['ready', 'needs_replan'].includes(node.state)) ||
            subtasks.find(st => ['pending', 'retry', 'adjust'].includes(st.status || 'pending'));
        const deps = graph?.edges?.length ?? subtasks.reduce((sum, st) => sum + ((st.depends_on || []).length), 0);
        traceFlowEl.innerHTML = `
            <div class="trace-overview">
                <div class="trace-overview-main">
                    <strong>${completed}/${total}</strong>
                    <span>子任务完成</span>
                </div>
                <div class="trace-overview-grid">
                    <span><b>${failed}</b><small>失败</small></span>
                    <span><b>${deps}</b><small>依赖</small></span>
                    <span><b>${esc(active ? String(active.id) : '-')}</b><small>当前</small></span>
                </div>
                <div class="trace-current-task">${active ? esc(active.desc || '') : '没有待执行子任务'}</div>
            </div>
        `;
    }

    function renderTraceDag(plans, events) {
        const latest = plans[plans.length - 1];
        const graph = latest?.state_graph || findLatestStateGraph(events);
        if (!graph || !Array.isArray(graph.nodes) || !graph.nodes.length) {
            traceDagEl.innerHTML = '<div class="trace-muted">暂无动态 DAG 快照</div>';
            return;
        }
        const nodes = graph.nodes;
        const edges = graph.edges || [];
        const maxCols = Math.min(4, Math.max(1, nodes.length));
        traceDagEl.innerHTML = `
            <div class="dag-summary">
                <span>frontier: <b>${esc((graph.frontier || []).join(', ') || '-')}</b></span>
                <span>blocked: <b>${esc(String(graph.summary?.blocked ?? 0))}</b></span>
                <span>replan: <b>${esc(String(graph.summary?.needs_replan ?? 0))}</b></span>
            </div>
            <div class="dag-grid" style="grid-template-columns: repeat(${maxCols}, minmax(120px, 1fr));">
                ${nodes.map(node => `
                    <button type="button" class="dag-node dag-state-${stateClass(node.state || 'waiting')}" data-id="${escAttr(node.id)}">
                        <span>${esc(node.id)}</span>
                        <b>${esc(labelDagState(node.state))}</b>
                        <small>${esc(node.desc || '')}</small>
                    </button>
                `).join('')}
            </div>
            <div class="dag-edges">
                ${edges.slice(0, 8).map(edge => `
                    <span class="dag-edge dag-edge-${escAttr(edge.state || 'waiting')}">${esc(edge.from)} → ${esc(edge.to)}</span>
                `).join('')}
                ${edges.length > 8 ? `<span class="dag-edge">+${edges.length - 8}</span>` : ''}
            </div>
        `;
        traceDagEl.querySelectorAll('.dag-node').forEach(btn => {
            btn.onclick = () => {
                const node = nodes.find(item => escAttr(item.id) === btn.dataset.id);
                traceDagEl.querySelectorAll('.dag-node').forEach(item => item.classList.remove('selected'));
                btn.classList.add('selected');
                traceDetailEl.textContent = JSON.stringify(node || {}, null, 2);
            };
        });
    }

    function findLatestStateGraph(events) {
        for (let i = events.length - 1; i >= 0; i--) {
            const graph = events[i]?.payload?.state_graph;
            if (graph && Array.isArray(graph.nodes)) return graph;
        }
        return null;
    }

    function renderTraceTimeline(events) {
        if (!events.length) {
            traceTimelineEl.innerHTML = '<div class="trace-muted">暂无事件</div>';
            return;
        }
        const phases = [
            { node: 'runtime', label: '启动' },
            { node: 'planning', label: '规划' },
            { node: 'skill_select', label: '技能' },
            { node: 'executor', label: '执行' },
            { node: 'reflection', label: '评审' },
            { node: 'finalize', label: '汇总' },
        ];
        const rows = phases.map((phase, index) => {
            const phaseEvents = events.filter(ev => (ev.node || '') === phase.node);
            const last = phaseEvents[phaseEvents.length - 1];
            const status = phaseEvents.length ? 'done' : 'idle';
            return `
                <button type="button" class="trace-phase trace-phase-${status}" data-node="${escAttr(phase.node)}" data-index="${index}">
                    <span class="trace-phase-dot"></span>
                    <span class="trace-phase-label">${esc(phase.label)}</span>
                    <span class="trace-phase-count">${phaseEvents.length}</span>
                    <small>${last ? esc(summarizeEvent(last.event_type, last.payload || {})) : '等待'}</small>
                </button>
            `;
        }).join('');
        traceTimelineEl.innerHTML = `
            <div class="trace-phases">${rows}</div>
        `;
        traceTimelineEl.querySelectorAll('.trace-phase').forEach(btn => {
            btn.onclick = () => {
                const phase = phases[Number(btn.dataset.index)];
                const phaseEvents = events.filter(ev => (ev.node || '') === phase.node);
                const latest = phaseEvents[phaseEvents.length - 1] || {};
                traceTimelineEl.querySelectorAll('.trace-phase').forEach(item => item.classList.remove('selected'));
                btn.classList.add('selected');
                traceDetailEl.textContent = JSON.stringify({
                    phase: phase.label,
                    events: phaseEvents.length,
                    latest,
                }, null, 2);
            };
        });
    }

    function summarizeEvent(type, payload) {
        if (type === 'run_started') return `预算 steps=${payload.budget?.max_steps}, time=${payload.budget?.max_runtime_seconds}s, tokens=${payload.budget?.max_tokens}`;
        if (type === 'plan_created' || type === 'plan_replanned') return `plan ${payload.plan_id} v${payload.version}, ready=${(payload.ready_subtasks || []).join(',') || '-'}`;
        if (type === 'skill_selected') return `subtask ${payload.subtask_id}: ${payload.skill} (${payload.source})`;
        if (type === 'tool_call_requested') return `subtask ${payload.subtask_id}: ${payload.tool}`;
        if (type === 'subtask_reviewed') return `subtask ${payload.subtask_id}: ${payload.status}, retry=${payload.retry_count}`;
        if (type === 'loop_detected') return `重复工具调用: ${(payload.repeated_signatures || []).length}`;
        if (type === 'budget_stop') return payload.reason || 'budget stop';
        if (type === 'run_finalized') return `完成 ${payload.metrics?.completed_subtasks ?? 0}/${payload.metrics?.total_subtasks ?? 0}`;
        return JSON.stringify(payload).slice(0, 120);
    }

    function labelEvent(type) {
        const labels = {
            run_started: '启动',
            plan_created: '计划',
            plan_replanned: '重规划',
            skill_selected: '技能',
            tool_call_requested: '工具',
            loop_detected: '循环',
            budget_stop: '预算',
            subtask_reviewed: '评审',
            run_finalized: '完成',
        };
        return labels[type] || type;
    }

    function labelNode(node) {
        const labels = {
            runtime: '运行',
            planning: '规划',
            skill_select: '技能',
            executor: '执行',
            reflection: '评审',
            finalize: '汇总',
        };
        return labels[node] || node;
    }

    function labelStatus(status) {
        const labels = {
            running: '运行中',
            completed: '完成',
            failed: '失败',
            stopped: '停止',
        };
        return labels[status] || status || '-';
    }

    function labelDagState(state) {
        const labels = {
            ready: '可执行',
            waiting: '等待',
            blocked: '阻塞',
            done: '完成',
            failed: '失败',
            skipped: '跳过',
            needs_replan: '重规划',
        };
        return labels[state] || state || '-';
    }

    function stateClass(state) {
        return escAttr(String(state || 'waiting').replace(/_/g, '-'));
    }

    function clearOutput() {
        // 保留欢迎信息（第一条），清除其余
        const welcome = output.querySelector('.term-welcome');
        output.innerHTML = '';
        if (welcome) {
            output.appendChild(welcome);
        }
    }

    function handleEvent(event) {
        switch (event.type) {
            case 'replay_start':
                // 重连/首次连接回放历史前先清屏
                clearOutput();
                appendLine('<span class="term-log">—— 历史日志 (' + event.count + ' 条) ——</span>');
                break;

            case 'connected':
                appendLine('<span class="term-log">' + esc(event.message) + '</span>');
                setStatus('● 已连接', 'connected');
                break;

            case 'log':
                appendLine(
                    '<span class="term-time">' + timeStr(event.time) + '</span>' +
                    '<span class="term-sender ' + senderClass(event.sender) + '">' +
                    esc(event.sender) + '</span>' +
                    '<span class="term-log">' + esc(event.message).replace(/\n/g, '<br>') + '</span>'
                );
                break;

            case 'tool':
                appendLine(
                    '<span class="term-time">' + timeStr(event.time) + '</span>' +
                    '<span class="term-sender term-sender-tool">Tool</span>' +
                    '<span class="term-tool">' + esc(event.tool) +
                    '(' + esc(event.args || '') + ')</span>'
                );
                break;

            case 'shell_approval':
                appendLine(
                    '<span class="term-time">' + timeStr(event.time) + '</span>' +
                    '<span class="term-shell">⚠ Shell 审批: ' +
                    esc(event.command).substring(0, 100) + '</span>'
                );
                showApprovalBar(event.approval_id, event.command);
                break;

            case 'error':
                appendLine(
                    '<span class="term-time">' + timeStr(event.time) + '</span>' +
                    '<span class="term-sender term-sender-error">ERROR</span>' +
                    '<span class="term-error">' + esc(event.message) + '</span>'
                );
                break;

            case 'metrics':
                handleMetrics(event);
                break;
        }
    }

    function handleMetrics(event) {
        if (!event.run_id) return;
        const metrics = event.metrics || {};
        currentRunId = event.run_id;
        localStorage.setItem('latest_agent_run_id', currentRunId);
        traceRunIdEl.textContent = `${event.run_id} · session ${event.session_id || '-'}`;
        traceStatusEl.textContent = `状态 ${labelStatus(metrics.status || 'running')}`;
        traceStepsEl.textContent = `步数 ${metrics.step_count ?? '-'}`;
        traceTimeEl.textContent = `耗时 ${metrics.elapsed_seconds ?? '-'}s`;
        traceTokensEl.textContent = `Tokens ${metrics.token_count ?? '-'}`;
        const finished = ['completed', 'failed', 'stopped'].includes(metrics.status);
        scheduleTraceRefresh(event.run_id, finished);
    }

    // ==================== Shell 审批栏 ====================
    function showApprovalBar(id, command) {
        currentApprovalId = id;
        shellCmdEl.textContent = command;
        approvalBar.style.display = 'block';
        if (approvalTimer) clearTimeout(approvalTimer);
        approvalTimer = setTimeout(() => {
            submitApproval('rejected');
        }, 60000);
    }

    function hideApprovalBar() {
        approvalBar.style.display = 'none';
        currentApprovalId = null;
        if (approvalTimer) clearTimeout(approvalTimer);
    }

    async function submitApproval(action) {
        if (!currentApprovalId) return;
        const id = currentApprovalId;
        hideApprovalBar();
        try {
            await fetch(API_BASE + '/shell/approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approval_id: id, action: action })
            });
            appendLine('<span class="term-log">  → Shell 命令已' +
                (action === 'approved' ? '批准' : action === 'skipped' ? '跳过' : '拒绝') +
                '</span>');
        } catch (e) {
            appendLine('<span class="term-error">审批提交失败: ' + esc(String(e)) + '</span>');
        }
    }

    approvalBar.querySelectorAll('button').forEach(btn => {
        btn.onclick = () => submitApproval(btn.dataset.action);
    });
    traceToggleBtn.onclick = toggleTraceVisibility;
    traceRefreshBtn.onclick = () => loadTrace();
    traceZoomClose.onclick = closeTraceZoom;
    traceZoomOverlay.onclick = (event) => {
        if (event.target === traceZoomOverlay) closeTraceZoom();
    };
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeTraceZoom();
    });
    document.querySelectorAll('.trace-section').forEach(section => {
        const title = section.querySelector('.trace-section-title');
        if (title) {
            title.setAttribute('role', 'button');
            title.setAttribute('title', '点击放大查看');
            title.onclick = () => openTraceZoom(title.textContent.trim(), section);
        }
        section.ondblclick = (event) => {
            if (event.target.closest('button')) return;
            openTraceZoom(title ? title.textContent.trim() : 'Trace 区域', section);
        };
    });
    traceDetailEl.onclick = () => openTraceZoom('Payload 详情', traceDetailEl);

    function openTraceZoom(title, sourceEl) {
        traceZoomTitle.textContent = title || '放大查看';
        traceZoomContent.innerHTML = '';
        const clone = sourceEl.cloneNode(true);
        clone.classList.add('trace-zoom-clone');
        clone.removeAttribute('style');
        traceZoomContent.appendChild(clone);
        traceZoomOverlay.style.display = 'flex';
    }

    function closeTraceZoom() {
        traceZoomOverlay.style.display = 'none';
        traceZoomContent.innerHTML = '';
    }

    // ==================== SSE 连接 ====================
    function connect() {
        connectCount++;
        setStatus('● 连接中...', '');
        const es = new EventSource(API_BASE + '/console/stream');

        es.onmessage = (e) => {
            try {
                const event = JSON.parse(e.data);
                handleEvent(event);
            } catch (err) {
                // heartbeat / comment
            }
        };

        es.onerror = () => {
            es.close();
            setStatus('● 断开 (5s 后重连)', 'disconnected');
            setTimeout(connect, 5000);
        };

        es.onopen = () => {
            setStatus('● 已连接', 'connected');
        };
    }

    function esc(s) {
        const div = document.createElement('div');
        div.textContent = s || '';
        return div.innerHTML;
    }

    function escAttr(s) {
        return String(s || '').replace(/[^a-zA-Z0-9_-]/g, '-');
    }

    connect();
    applyTraceVisibility();
    if (!traceHidden) loadTrace();
});
