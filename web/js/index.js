document.addEventListener('DOMContentLoaded', async () => {
    const sendBtn = document.getElementById('send-btn');
    const newChatBtn = document.getElementById('new-chat-btn');
    const userInput = document.getElementById('user-input');
    const chatWindow = document.getElementById('chat-window');
    const sessionListContainer = document.getElementById('session-list');
    const avatarFileInput = document.getElementById('avatar-file-input');
    const avatarModal = document.getElementById('avatar-modal-overlay');
    const avatarModalClose = document.getElementById('avatar-modal-close');
    const avatarModalTitle = document.getElementById('avatar-modal-title');
    const avatarModalKind = document.getElementById('avatar-modal-kind');
    const avatarPreviewStage = document.getElementById('avatar-preview-stage');
    const avatarPreviewImage = document.getElementById('avatar-preview-image');
    const avatarPreviewEmpty = document.getElementById('avatar-preview-empty');
    const avatarQualityBadge = document.getElementById('avatar-quality-badge');
    const avatarPreviewName = document.getElementById('avatar-preview-name');
    const avatarPreviewMeta = document.getElementById('avatar-preview-meta');
    const avatarUploadStatus = document.getElementById('avatar-upload-status');
    const avatarSelectBtn = document.getElementById('avatar-select-btn');
    const avatarApplyBtn = document.getElementById('avatar-apply-btn');
    const taskMeter = document.getElementById('task-meter');
    const taskMeterPhase = document.getElementById('task-meter-phase');
    const taskElapsed = document.getElementById('task-elapsed');
    const taskTokens = document.getElementById('task-tokens');
    const taskSteps = document.getElementById('task-steps');
    const taskProgress = document.getElementById('task-progress');
    const taskPhaseTrack = document.getElementById('task-phase-track');

    let currentSessionId = localStorage.getItem('last_session_id') || '';
    let ttsMuted = localStorage.getItem('tts_muted') === 'true';
    let taskStartedAt = 0;
    let taskTimer = null;
    let activeTaskSessionId = currentSessionId;
    let backendMetricSeen = false;
    let streamedOutputChars = 0;
    let latestTaskMetrics = null;
    let activeAgentProcess = null;
    let agentEnabled = false;
    let avatarProfiles = { user: null, ai: null };
    let pendingAvatarFile = null;
    let pendingAvatarObjectUrl = '';
    // 当前正在上传的头像类型: 'user' | 'ai'
    let avatarUploadTarget = 'user';
    // 沉浸陪伴：屏幕通道只保持授权，不进行定时截屏
    let companionActive = false;
    let companionScreenStream = null;
    let companionScreenTrack = null;
    let companionVoiceTurnRunning = false;
    let companionChatController = null;
    let queuedVoiceUtterance = '';
    let lastAssistantSpeech = '';
    let imageGenProbability = 0.08;

    // ==================== 头像系统 ====================
    // 从后端加载持久化的头像配置（解决 GUI 模式重启丢失问题）
    async function loadAvatarsFromServer() {
        try {
            const resp = await fetch(API_BASE + '/avatar/current');
            const data = await resp.json();
            if (data.status === 'success') {
                if (data.user_avatar) localStorage.setItem('avatar_filename', data.user_avatar);
                if (data.ai_avatar) localStorage.setItem('ai_avatar_filename', data.ai_avatar);
                avatarProfiles = {
                    user: data.avatars?.user || null,
                    ai: data.avatars?.ai || null,
                };
            }
        } catch (e) {
            // 后端不可用时使用 localStorage 缓存
            console.log('头像配置加载失败，使用本地缓存');
        }
    }

    function getUserAvatarUrl() {
        const filename = localStorage.getItem('avatar_filename');
        return getAvatarUrl('user', filename);
    }

    function getAiAvatarUrl() {
        const filename = localStorage.getItem('ai_avatar_filename');
        return getAvatarUrl('ai', filename);
    }

    function getAvatarUrl(type, filename) {
        if (!filename) return null;
        const profile = avatarProfiles[type];
        const path = profile?.filename === filename && profile.url
            ? profile.url
            : `/avatar/${encodeURIComponent(filename)}`;
        return /^https?:\/\//i.test(path) ? path : API_BASE + path;
    }

    function onUserAvatarClick() {
        openAvatarModal('user');
    }

    function onAiAvatarClick() {
        openAvatarModal('ai');
    }

    function openAvatarModal(type) {
        avatarUploadTarget = type;
        clearPendingAvatar();
        const isAi = type === 'ai';
        const profile = avatarProfiles[type];
        const url = isAi ? getAiAvatarUrl() : getUserAvatarUrl();
        avatarModalTitle.textContent = isAi ? 'AI 头像' : '用户头像';
        avatarModalKind.textContent = isAi ? 'ASSISTANT PROFILE' : 'USER PROFILE';
        avatarPreviewName.textContent = isAi ? '当前 AI 头像' : '当前用户头像';
        avatarQualityBadge.textContent = profile?.format === 'webp' ? 'HD WEBP' : 'CURRENT';
        avatarQualityBadge.className = 'avatar-quality-badge';
        avatarUploadStatus.textContent = '';
        avatarUploadStatus.className = '';
        avatarApplyBtn.disabled = true;
        avatarApplyBtn.textContent = '';
        avatarApplyBtn.append(createActionIcon('/images/执行确认.png'));
        avatarApplyBtn.append(document.createTextNode('应用头像'));
        showAvatarPreview(url);
        avatarPreviewMeta.textContent = profile
            ? `${profile.width} × ${profile.height} · ${String(profile.format || '').toUpperCase()} · ${formatFileSize(profile.bytes)}`
            : url ? '当前头像' : '尚未设置头像';
        avatarModal.style.display = 'flex';
        avatarModalClose.focus();
    }

    function closeAvatarModal() {
        avatarModal.style.display = 'none';
        clearPendingAvatar();
        avatarFileInput.value = '';
    }

    function clearPendingAvatar() {
        pendingAvatarFile = null;
        if (pendingAvatarObjectUrl) URL.revokeObjectURL(pendingAvatarObjectUrl);
        pendingAvatarObjectUrl = '';
    }

    function showAvatarPreview(url) {
        avatarPreviewImage.hidden = !url;
        avatarPreviewEmpty.hidden = Boolean(url);
        if (url) avatarPreviewImage.src = url;
        else avatarPreviewImage.removeAttribute('src');
    }

    function createActionIcon(src) {
        const img = document.createElement('img');
        img.src = src;
        img.alt = '';
        return img;
    }

    function formatFileSize(bytes) {
        const value = Number(bytes || 0);
        if (!value) return '-';
        if (value < 1024) return `${value} B`;
        if (value < 1024 * 1024) return `${(value / 1024).toFixed(0)} KB`;
        return `${(value / (1024 * 1024)).toFixed(1)} MB`;
    }

    function readAvatarDimensions(file) {
        return new Promise((resolve, reject) => {
            const url = URL.createObjectURL(file);
            const image = new Image();
            image.onload = () => resolve({ url, width: image.naturalWidth, height: image.naturalHeight });
            image.onerror = () => {
                URL.revokeObjectURL(url);
                reject(new Error('无法读取图片'));
            };
            image.src = url;
        });
    }

    async function prepareAvatarFile(file) {
        clearPendingAvatar();
        avatarApplyBtn.disabled = true;
        avatarUploadStatus.className = '';
        if (!file || !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
            avatarUploadStatus.textContent = '请选择 PNG、JPEG、WebP 或 GIF 图片';
            avatarUploadStatus.className = 'error';
            return;
        }
        if (file.size > 10 * 1024 * 1024) {
            avatarUploadStatus.textContent = '图片不能超过 10MB';
            avatarUploadStatus.className = 'error';
            return;
        }
        try {
            const info = await readAvatarDimensions(file);
            pendingAvatarObjectUrl = info.url;
            showAvatarPreview(info.url);
            const outputEdge = Math.min(1024, info.width, info.height);
            avatarPreviewName.textContent = file.name;
            avatarPreviewMeta.textContent = `${info.width} × ${info.height} · ${formatFileSize(file.size)} → ${outputEdge} × ${outputEdge} WebP`;
            avatarQualityBadge.textContent = outputEdge >= 512 ? 'HD READY' : 'CHECK';
            avatarQualityBadge.className = `avatar-quality-badge ${outputEdge >= 512 ? 'ready' : 'warning'}`;
            if (Math.min(info.width, info.height) < 256) {
                avatarUploadStatus.textContent = '图片短边低于 256px，无法作为高清头像';
                avatarUploadStatus.className = 'error';
                return;
            }
            pendingAvatarFile = file;
            avatarApplyBtn.disabled = false;
            avatarUploadStatus.textContent = '图片已就绪';
            avatarUploadStatus.className = 'success';
        } catch (error) {
            avatarUploadStatus.textContent = error.message || '无法读取图片';
            avatarUploadStatus.className = 'error';
        }
    }

    async function uploadPendingAvatar() {
        if (!pendingAvatarFile) return;
        const formData = new FormData();
        formData.append('file', pendingAvatarFile);
        formData.append('type', avatarUploadTarget);
        avatarApplyBtn.disabled = true;
        avatarSelectBtn.disabled = true;
        avatarApplyBtn.textContent = '处理中…';
        avatarUploadStatus.textContent = '正在生成高清头像';
        avatarUploadStatus.className = '';
        try {
            const resp = await fetch(API_BASE + '/avatar/upload', {
                method: 'POST',
                body: formData
            });
            const data = await resp.json();
            if (!resp.ok || data.status !== 'success') throw new Error(data.detail || data.msg || '上传失败');
            const key = avatarUploadTarget === 'ai' ? 'ai_avatar_filename' : 'avatar_filename';
            localStorage.setItem(key, data.filename);
            avatarProfiles[avatarUploadTarget] = data.avatar || null;
            refreshAllAvatars();
            showAvatarPreview(getAvatarUrl(avatarUploadTarget, data.filename));
            avatarPreviewMeta.textContent = `${data.avatar.width} × ${data.avatar.height} · WEBP · ${formatFileSize(data.avatar.bytes)}`;
            avatarQualityBadge.textContent = 'HD WEBP';
            avatarQualityBadge.className = 'avatar-quality-badge ready';
            avatarUploadStatus.textContent = '头像已更新';
            avatarUploadStatus.className = 'success';
            clearPendingAvatar();
        } catch (e) {
            console.error('头像上传失败:', e);
            avatarUploadStatus.textContent = e.message || '头像上传失败';
            avatarUploadStatus.className = 'error';
            avatarApplyBtn.disabled = false;
        } finally {
            avatarSelectBtn.disabled = false;
            avatarApplyBtn.textContent = '';
            avatarApplyBtn.append(createActionIcon('/images/执行确认.png'));
            avatarApplyBtn.append(document.createTextNode('应用头像'));
        }
        avatarFileInput.value = '';
    }

    avatarSelectBtn.onclick = () => avatarFileInput.click();
    avatarApplyBtn.onclick = uploadPendingAvatar;
    avatarModalClose.onclick = closeAvatarModal;
    avatarModal.onclick = (event) => { if (event.target === avatarModal) closeAvatarModal(); };
    avatarPreviewStage.ondragover = (event) => {
        event.preventDefault();
        avatarPreviewStage.classList.add('dragging');
    };
    avatarPreviewStage.ondragleave = () => avatarPreviewStage.classList.remove('dragging');
    avatarPreviewStage.ondrop = (event) => {
        event.preventDefault();
        avatarPreviewStage.classList.remove('dragging');
        prepareAvatarFile(event.dataTransfer?.files?.[0]);
    };
    avatarFileInput.onchange = () => prepareAvatarFile(avatarFileInput.files[0]);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && avatarModal.style.display === 'flex') closeAvatarModal();
    });

    function refreshAllAvatars() {
        document.querySelectorAll('.msg-avatar-user').forEach(img => {
            const url = getUserAvatarUrl();
            if (url) img.src = url;
        });
        document.querySelectorAll('.avatar-placeholder-user').forEach(el => {
            const url = getUserAvatarUrl();
            if (url) {
                el.replaceWith(buildUserAvatarImg(url));
            }
        });
        document.querySelectorAll('.msg-avatar-ai').forEach(img => {
            const url = getAiAvatarUrl();
            if (url) img.src = url;
        });
        document.querySelectorAll('.avatar-placeholder-ai').forEach(el => {
            const url = getAiAvatarUrl();
            if (url) {
                el.replaceWith(buildAiAvatarImg(url));
            }
        });
    }

    // ==================== 初始化 ====================
    async function init() {
        await loadAvatarsFromServer();  // 从后端加载持久化头像（修复 GUI 重启丢失）
        await fetchRuntimeConfig();
        await fetchSessions();
        await fetchCompanionInterval();
        if (currentSessionId) {
            await switchSession(currentSessionId);
        }
        if (typeof Live2DCtrl !== 'undefined') {
            Live2DCtrl.init();
        }
        initTTSControls();
        resetTaskMeter('空闲');
    }
    // ==================== 首页实时任务指标 ====================
    function setTaskMeterVisible(visible) {
        if (!taskMeter) return;
        taskMeter.style.display = visible ? '' : 'none';
    }

    // ==================== 聊天内 Agent 执行过程 ====================
    const processNodeLabels = {
        runtime: 'Runtime',
        planning: 'Planner',
        scheduler: 'Scheduler',
        research_worker: 'Research Worker',
        browser_worker: 'Browser Worker',
        execution_worker: 'Execution Worker',
        verification_worker: 'Verification Worker',
        general_worker: 'General Worker',
        collaboration: 'Collaboration',
        skill_select: 'Skill Select',
        executor: 'Executor',
        reflection: 'Verifier',
        finalize: 'Finalize',
    };

    function createAgentProcessRow({ expanded = true, restored = false } = {}) {
        const row = document.createElement('div');
        row.className = `agent-process-row${restored ? ' restored' : ''}`;
        row.innerHTML = `
            <section class="agent-process-panel${expanded ? ' expanded' : ''}" aria-label="Agent 执行过程">
                <button class="agent-process-toggle" type="button" aria-expanded="${expanded}">
                    <span class="agent-process-orbit" aria-hidden="true"><i></i></span>
                    <span class="agent-process-title">
                        <strong>正在判断任务路径</strong>
                        <small>等待 Direct / Agent 统一路由</small>
                    </span>
                    <span class="agent-process-elapsed">0.0s</span>
                    <span class="agent-process-chevron" aria-hidden="true">${expanded ? '⌃' : '⌄'}</span>
                </button>
                <div class="agent-process-body">
                    <section class="agent-reasoning-stream" aria-label="思考动态">
                        <header class="agent-reasoning-head">
                            <span>思考动态</span>
                            <small>等待 Runtime</small>
                        </header>
                        <div class="agent-reasoning-list">
                            <div class="agent-reasoning-item active">
                                <span class="agent-reasoning-phase">理解</span>
                                <span class="agent-reasoning-copy">
                                    <b>接收当前请求</b>
                                    <small>正在建立本次执行上下文。</small>
                                </span>
                            </div>
                        </div>
                    </section>
                    <div class="agent-plan-preview is-loading">
                        <div class="process-skeleton"></div>
                        <div class="process-skeleton short"></div>
                    </div>
                    <div class="agent-process-timeline">
                        <div class="process-event active">
                            <span class="process-event-dot"></span>
                            <span class="process-event-copy">
                                <b>请求进入任务路由</b>
                                <small>正在选择执行路径</small>
                            </span>
                        </div>
                    </div>
                    <a class="agent-process-trace-link" href="workflow.html">查看完整执行拓扑</a>
                </div>
            </section>
        `;
        const panel = row.querySelector('.agent-process-panel');
        row.querySelector('.agent-process-toggle').addEventListener('click', () => {
            const expanded = panel.classList.toggle('expanded');
            row.querySelector('.agent-process-toggle').setAttribute('aria-expanded', String(expanded));
            row.querySelector('.agent-process-chevron').textContent = expanded ? '⌃' : '⌄';
        });
        return row;
    }

    function startAgentProcess() {
        stopAgentProcessTracking();
        const row = createAgentProcessRow();
        chatWindow.appendChild(row);
        activeAgentProcess = {
            row,
            runId: '',
            startedAt: performance.now(),
            pollTimer: null,
            inFlight: false,
            finished: false,
            ok: true,
            lastTrace: null,
        };
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    async function restoreLatestAgentProcess(sessionId) {
        if (!sessionId) return;
        try {
            const runsResponse = await fetch(`${API_BASE}/agent/runs?session_id=${encodeURIComponent(sessionId)}&limit=3`);
            const runsData = await runsResponse.json();
            const run = (runsData.runs || []).find((item) => item.status && item.status !== 'running');
            if (!run || sessionId !== currentSessionId) return;
            const traceResponse = await fetch(`${API_BASE}/agent/trace/${encodeURIComponent(run.run_id)}`);
            const trace = await traceResponse.json();
            if (trace.status === 'error' || sessionId !== currentSessionId) return;

            const row = createAgentProcessRow({ expanded: true, restored: true });
            const panel = row.querySelector('.agent-process-panel');
            const metrics = run.metrics || {};
            const completed = Number(metrics.completed_subtasks || 0);
            const total = Number(metrics.total_subtasks || 0);
            panel.classList.toggle('done', run.status === 'completed');
            panel.classList.toggle('partial', run.status !== 'completed' && completed > 0);
            panel.classList.toggle('failed', run.status === 'failed' && completed === 0);
            row.querySelector('.agent-process-title strong').textContent = '已恢复上一轮 Agent 执行';
            row.querySelector('.agent-process-title small').textContent = `执行上下文已保留${total ? ` · 子任务 ${completed}/${total}` : ''}`;
            row.querySelector('.agent-process-elapsed').textContent = `${metrics.step_count || 0} steps`;
            const link = row.querySelector('.agent-process-trace-link');
            link.href = `workflow.html?run_id=${encodeURIComponent(run.run_id)}`;
            link.textContent = `查看完整执行拓扑 · ${shortRunId(run.run_id)}`;
            chatWindow.appendChild(row);
            renderAgentTrace({ row, runId: run.run_id, lastTrace: trace }, trace);
            chatWindow.scrollTop = chatWindow.scrollHeight;
        } catch (error) {
            console.debug('恢复 Agent 执行记录失败:', error);
        }
    }

    function stopAgentProcessTracking() {
        if (activeAgentProcess?.pollTimer) clearTimeout(activeAgentProcess.pollTimer);
        activeAgentProcess = null;
    }

    function attachAgentProcessRun(runId) {
        const process = activeAgentProcess;
        if (!process || !runId) return;
        if (process.runId && process.runId !== runId) return;
        process.runId = runId;
        process.row.querySelector('.agent-process-title strong').textContent = 'Agent Runtime 已启动';
        process.row.querySelector('.agent-process-title small').textContent = 'Planner-Scheduler-Worker 实时过程';
        const encoded = encodeURIComponent(runId);
        const link = process.row.querySelector('.agent-process-trace-link');
        link.href = `workflow.html?run_id=${encoded}`;
        link.textContent = `查看完整执行拓扑 · ${shortRunId(runId)}`;
        queueAgentTracePoll(80);
    }

    function shortRunId(runId) {
        const value = String(runId || '');
        return value.length > 14 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
    }

    function queueAgentTracePoll(delay = 600) {
        const process = activeAgentProcess;
        if (!process?.runId || process.inFlight) return;
        if (process.pollTimer) clearTimeout(process.pollTimer);
        process.pollTimer = setTimeout(() => pollAgentTrace(process), delay);
    }

    async function pollAgentTrace(process) {
        if (!process || process !== activeAgentProcess || !process.runId || process.inFlight) return;
        process.inFlight = true;
        try {
            const response = await fetch(`${API_BASE}/agent/trace/${encodeURIComponent(process.runId)}`);
            if (!response.ok) throw new Error(`Trace ${response.status}`);
            const trace = await response.json();
            if (trace.status === 'error') throw new Error(trace.msg || 'Trace 暂不可用');
            process.lastTrace = trace;
            renderAgentTrace(process, trace);
            if (trace.run?.status && trace.run.status !== 'running') {
                finishAgentProcess(trace.run.status === 'completed', trace.run.metrics || {});
            }
        } catch (error) {
            const subtitle = process.row.querySelector('.agent-process-title small');
            if (subtitle && !process.finished) subtitle.textContent = 'Trace 正在同步，执行不受影响';
        } finally {
            process.inFlight = false;
            if (process === activeAgentProcess && !process.finished) queueAgentTracePoll(850);
        }
    }

    function renderAgentTrace(process, trace) {
        if (!process?.row?.isConnected) return;
        const plans = Array.isArray(trace.plans) ? trace.plans : [];
        const latestPlan = plans[plans.length - 1];
        renderAgentPlan(process, latestPlan);
        renderAgentReasoning(process, trace);

        const events = (Array.isArray(trace.events) ? trace.events : [])
            .map(describeTraceEvent)
            .filter(Boolean)
            .slice(-40);
        const timeline = process.row.querySelector('.agent-process-timeline');
        if (events.length) {
            timeline.innerHTML = events.map((event, index) => `
                <div class="process-event ${event.tone || ''} ${index === events.length - 1 && trace.run?.status === 'running' ? 'active' : ''}">
                    <span class="process-event-dot"></span>
                    <span class="process-event-node">${escHtml(event.node)}</span>
                    <span class="process-event-copy">
                        <b>${escHtml(event.title)}</b>
                        ${event.detail ? `<small>${escHtml(event.detail)}</small>` : ''}
                    </span>
                    <time>${escHtml(formatTraceTime(event.time))}</time>
                </div>
            `).join('');
        }

        const run = trace.run || {};
        const metrics = run.metrics || {};
        if (run.status === 'running') {
            const current = events[events.length - 1];
            updateAgentProcessSummary(current?.title || '正在执行计划', {
                ...metrics,
                status: 'running',
            });
        }
    }

    const reasoningPhaseLabels = {
        understand: '理解',
        recall: '回忆',
        plan: '计划',
        decide: '决策',
        observe: '观察',
        verify: '验证',
        next: '下一步',
        summarize: '汇总',
    };

    function renderAgentReasoning(process, trace) {
        const stream = process.row.querySelector('.agent-reasoning-stream');
        const list = process.row.querySelector('.agent-reasoning-list');
        const status = process.row.querySelector('.agent-reasoning-head small');
        if (!stream || !list || !status) return;
        const updates = (Array.isArray(trace.events) ? trace.events : [])
            .filter((event) => event.event_type === 'reasoning_update' && event.payload?.visibility === 'public_summary')
            .slice(-9);
        if (!updates.length) {
            status.textContent = trace.run?.status === 'running' ? '等待首个摘要' : '旧版运行轨迹';
            return;
        }

        status.textContent = trace.run?.status === 'running' ? `实时 · ${updates.length} 条` : `${updates.length} 条已保存`;
        list.innerHTML = updates.map((event, index) => {
            const payload = event.payload || {};
            const phase = reasoningPhaseLabels[payload.phase] || '思考';
            const eventStatus = String(payload.status || 'running').toLowerCase();
            const safeStatus = ['running', 'success', 'ready', 'warning', 'adjusted', 'error'].includes(eventStatus)
                ? eventStatus
                : 'running';
            const active = index === updates.length - 1 && trace.run?.status === 'running';
            return `
                <div class="agent-reasoning-item status-${safeStatus}${active ? ' active' : ''}">
                    <span class="agent-reasoning-phase">${escHtml(phase)}</span>
                    <span class="agent-reasoning-copy">
                        <b>${escHtml(payload.title || '更新执行判断')}</b>
                        ${payload.summary ? `<small>${escHtml(payload.summary)}</small>` : ''}
                        ${payload.next_action ? `<em><i>下一步</i>${escHtml(payload.next_action)}</em>` : ''}
                    </span>
                    <time>${escHtml(formatTraceTime(event.created_at))}</time>
                </div>
            `;
        }).join('');
        stream.classList.toggle('is-live', trace.run?.status === 'running');
    }

    function renderAgentPlan(process, plan) {
        const container = process.row.querySelector('.agent-plan-preview');
        if (!plan || !Array.isArray(plan.subtasks)) return;
        const graphNodes = new Map((plan.state_graph?.nodes || []).map((node) => [String(node.id), node]));
        const tasks = plan.subtasks.slice(0, 8);
        container.classList.remove('is-loading');
        container.innerHTML = `
            <div class="agent-plan-head">
                <span>PLAN v${escHtml(plan.version || 1)}</span>
                <strong>${plan.subtasks.length} 个子任务</strong>
                <small>质量 ${escHtml(plan.quality?.score ?? '-')}</small>
            </div>
            <div class="agent-plan-goal">${escHtml(plan.goal || '结构化执行计划')}</div>
            <div class="agent-plan-tasks">
                ${tasks.map((task) => {
                    const graphNode = graphNodes.get(String(task.id));
                    const status = normalizeProcessStatus(graphNode?.state || task.status || 'pending');
                    return `
                        <div class="agent-plan-task status-${status}">
                            <span>${escHtml(task.id)}</span>
                            <b>${escHtml(task.desc || task.description || '未命名子任务')}</b>
                            <small>${escHtml(processStatusLabel(status))}</small>
                        </div>
                    `;
                }).join('')}
                ${plan.subtasks.length > tasks.length ? `<div class="agent-plan-more">+${plan.subtasks.length - tasks.length}</div>` : ''}
            </div>
        `;
    }

    function describeTraceEvent(event) {
        const payload = event.payload || {};
        const node = processNodeLabels[event.node]
            || (String(event.node || '').endsWith('_worker') ? String(event.node).replace(/_/g, ' ') : event.node)
            || 'Agent';
        const base = { node, time: event.created_at };
        if (event.event_type === 'run_started') {
            const budget = payload.budget || {};
            return { ...base, title: 'Agent Runtime 已启动', detail: `${budget.max_steps || '-'} steps · ${budget.max_tokens || '-'} tokens` };
        }
        if (event.event_type === 'plan_created' || event.event_type === 'plan_replanned') {
            const total = payload.state_graph?.summary?.total ?? payload.ready_subtasks?.length ?? '-';
            const score = payload.quality?.score;
            const action = event.event_type === 'plan_replanned' ? '局部重规划' : '生成计划';
            return { ...base, title: `${action} v${payload.version || 1}`, detail: `${total} 个子任务${score != null ? ` · 质量 ${score}` : ''}` };
        }
        if (event.event_type === 'session_context_hydrated') {
            const runs = Array.isArray(payload.source_run_ids) ? payload.source_run_ids.length : 0;
            return { ...base, title: '继承上一轮执行上下文', detail: `${runs} 次历史运行 · ${payload.context_length || 0} 字符结构化记忆`, tone: 'success' };
        }
        if (event.event_type === 'run_handoff_saved') {
            return { ...base, title: '保存执行交接记忆', detail: `${payload.subtasks || 0} 个子任务 · ${payload.artifacts || 0} 个产物`, tone: 'success' };
        }
        if (event.event_type === 'scheduler_batch_created') {
            const ids = Array.isArray(payload.subtask_ids) ? payload.subtask_ids.join(', ') : '-';
            return { ...base, title: `创建执行批次 ${payload.epoch || '-'}`, detail: `子任务 ${ids} · ${payload.mode === 'parallel' ? `${payload.parallelism || 1} 路并行` : '串行执行'}` };
        }
        if (event.event_type === 'worker_dispatched') {
            return { ...base, title: `派发子任务 ${payload.subtask_id || '-'}`, detail: `${payload.worker || 'worker'} · ${payload.task_type || 'general'} / ${payload.risk_level || 'low'} risk` };
        }
        if (event.event_type === 'worker_started') {
            return { ...base, title: `开始执行子任务 ${payload.subtask_id || '-'}`, detail: `${payload.worker || 'worker'} 已创建隔离上下文` };
        }
        if (event.event_type === 'worker_tool_policy') {
            const tools = Array.isArray(payload.allowed_tools) ? payload.allowed_tools : [];
            return { ...base, title: '应用 Worker 工具边界', detail: `${tools.length} 个允许工具 · 最多 ${payload.max_tool_calls || 0} 次调用` };
        }
        if (event.event_type === 'worker_tool_call_requested') {
            return { ...base, title: `调用工具 ${payload.tool || 'unknown'}`, detail: formatToolArgs(payload.args), tone: 'tool' };
        }
        if (event.event_type === 'worker_tool_result') {
            return { ...base, title: `${payload.tool || '工具'} 返回${payload.ok ? '成功' : '异常'}`, detail: `子任务 ${payload.subtask_id || '-'} · ${payload.output_length || 0} 字符证据`, tone: payload.ok ? 'success' : 'warning' };
        }
        if (event.event_type === 'worker_tool_policy_violation') {
            return { ...base, title: '阻止 Worker 越权调用', detail: `${(payload.violations || []).length} 项策略违规`, tone: 'error' };
        }
        if (event.event_type === 'worker_loop_detected') {
            return { ...base, title: '检测到重复工具调用', detail: `${payload.tool || '工具'} 已停止重复执行`, tone: 'warning' };
        }
        if (event.event_type === 'worker_tool_budget_trimmed') {
            return { ...base, title: '裁剪超额工具调用', detail: `请求 ${payload.requested || 0} · 接受 ${payload.accepted || 0}`, tone: 'warning' };
        }
        if (event.event_type === 'artifact_created') {
            return { ...base, title: `生成${payload.kind || '产物'}`, detail: safeSnippet(payload.uri, 120), tone: 'success' };
        }
        if (event.event_type === 'worker_completed') {
            const status = normalizeProcessStatus(payload.status);
            const issues = payload.verification?.issues || [];
            return {
                ...base,
                title: `子任务 ${payload.subtask_id || '-'} ${processStatusLabel(status)}`,
                detail: issues.length ? `验收：${issues.slice(0, 2).join('；')}` : `${payload.evidence_count || 0} 条证据 · ${payload.elapsed_seconds || 0}s`,
                tone: status === 'completed' ? 'success' : status === 'failed' ? 'error' : 'warning',
            };
        }
        if (event.event_type === 'scheduler_batch_completed') {
            return { ...base, title: `批次 ${payload.epoch || '-'} 已归并`, detail: `通过 ${(payload.completed || []).length} · 重试 ${(payload.retry || []).length} · 重规划 ${(payload.needs_replan || []).length} · 失败 ${(payload.failed || []).length}` };
        }
        if (event.event_type === 'scheduler_stopped' || event.event_type === 'scheduler_blocked') {
            return { ...base, title: event.event_type === 'scheduler_blocked' ? '调度器无法继续派发' : '调度器已停止', detail: safeSnippet(payload.reason, 120), tone: 'warning' };
        }
        if (event.event_type === 'skill_selected') {
            return { ...base, title: `选择技能 ${payload.skill || 'general_llm'}`, detail: `子任务 ${payload.subtask_id || '-'}` };
        }
        if (event.event_type === 'executor_tool_policy') {
            const tools = Array.isArray(payload.allowed_tools) ? payload.allowed_tools.length : 0;
            const remaining = payload.tool_calls_remaining;
            return { ...base, title: '应用子任务工具策略', detail: `${payload.task_type || 'general'} / ${payload.risk_level || 'low'} risk · ${tools} tools · ${remaining == null ? `上限 ${payload.max_tool_calls || '-'}` : `剩余 ${remaining}/${payload.max_tool_calls || '-'}`}` };
        }
        if (event.event_type === 'tool_call_requested') {
            return { ...base, title: `调用工具 ${payload.tool || 'unknown'}`, detail: formatToolArgs(payload.args), tone: 'tool' };
        }
        if (event.event_type === 'model_observation') {
            return { ...base, title: '已获得阶段性结果', detail: `子任务 ${payload.subtask_id || '-'}，准备进入验收` };
        }
        if (event.event_type === 'tool_budget_exhausted') {
            return { ...base, title: '工具调用已满足预算', detail: '基于已有工具结果生成阶段性结论', tone: 'warning' };
        }
        if (event.event_type === 'tool_budget_trimmed') {
            return { ...base, title: '裁剪超额工具调用', detail: `请求 ${payload.requested || 0} 个 · 执行 ${payload.accepted || 0} 个`, tone: 'warning' };
        }
        if (event.event_type === 'subtask_reviewed') {
            const status = normalizeProcessStatus(payload.status);
            const issues = payload.verifier?.issues || [];
            return {
                ...base,
                title: `子任务 ${payload.subtask_id || '-'} ${processStatusLabel(status)}`,
                detail: issues.length ? `验收项：${issues.slice(0, 2).join('；')}` : `验证通过 · 重试 ${payload.retry_count || 0} 次`,
                tone: status === 'completed' ? 'success' : status === 'failed' ? 'error' : 'warning',
            };
        }
        if (event.event_type === 'tool_policy_violation') {
            return { ...base, title: '阻止未授权工具调用', detail: (payload.violations || []).join(', '), tone: 'error' };
        }
        if (event.event_type === 'loop_detected') {
            return { ...base, title: '检测到重复工具调用', detail: '循环保护已停止继续调用', tone: 'warning' };
        }
        if (event.event_type === 'budget_stop') {
            return { ...base, title: '达到 Runtime 预算', detail: payload.reason || '执行已停止', tone: 'warning' };
        }
        if (event.event_type === 'run_finalized') {
            const metrics = payload.metrics || {};
            const incomplete = Number(metrics.total_subtasks || 0) > Number(metrics.completed_subtasks || 0);
            const stopped = Boolean(metrics.stop_reason) || incomplete;
            return {
                ...base,
                title: stopped ? '执行已停止，汇总已有结果' : '完成结果汇总',
                detail: metrics.stop_reason ? safeSnippet(metrics.stop_reason, 120) : `${metrics.step_count || 0} steps · ${metrics.tool_calls || 0} 次工具调用`,
                tone: stopped ? 'warning' : 'success',
            };
        }
        if (event.event_type === 'node_start') {
            if (event.node === 'planning') return { ...base, title: '理解目标并拆解任务', detail: '正在建立依赖、预算和验收条件' };
            if (event.node === 'collaboration') return { ...base, title: '评估协作执行策略', detail: `Plan v${payload.version || 1}` };
            if (event.node === 'skill_select') return { ...base, title: `为子任务 ${payload.subtask_id || '-'} 选择能力`, detail: safeSnippet(payload.desc) };
            if (event.node === 'executor') {
                const budget = payload.tool_calls_remaining == null ? '' : ` · 工具预算剩余 ${payload.tool_calls_remaining}`;
                return { ...base, title: `执行子任务 ${payload.subtask_id || '-'}`, detail: `${safeSnippet(payload.subtask)}${budget}` };
            }
            if (event.node === 'reflection') return { ...base, title: `验证子任务 ${payload.subtask_id || '-'}`, detail: safeSnippet(payload.desc) };
            if (event.node === 'finalize') {
                const incomplete = Number(payload.total || 0) > Number(payload.completed || 0);
                return {
                    ...base,
                    title: incomplete ? '汇总已有执行结果' : '汇总可交付结果',
                    detail: payload.stop_reason ? safeSnippet(payload.stop_reason, 120) : `${payload.completed || 0}/${payload.total || 0} 个子任务完成`,
                    tone: incomplete ? 'warning' : '',
                };
            }
        }
        return null;
    }

    function safeSnippet(value, max = 100) {
        const text = String(value || '').replace(/\s+/g, ' ').trim();
        return text.length > max ? `${text.slice(0, max)}…` : text;
    }

    function formatToolArgs(args) {
        if (!args || typeof args !== 'object') return '已提交工具参数';
        const safeKeys = ['query', 'path', 'file_path', 'url', 'command', 'name', 'pattern'];
        const parts = safeKeys
            .filter((key) => args[key] != null)
            .slice(0, 2)
            .map((key) => `${key}: ${safeSnippet(args[key], 80)}`);
        return parts.length ? parts.join(' · ') : '已提交工具参数';
    }

    function normalizeProcessStatus(value) {
        const status = String(value || 'pending').toLowerCase();
        if (['done', 'completed'].includes(status)) return 'completed';
        if (['ready', 'running'].includes(status)) return status;
        if (['failed', 'blocked'].includes(status)) return status;
        if (['retry', 'retryable', 'adjust', 'needs_replan', 'replan_required'].includes(status)) return 'retry';
        if (status === 'skipped') return 'skipped';
        return 'pending';
    }

    function processStatusLabel(status) {
        return {
            completed: '已完成',
            ready: '就绪',
            running: '执行中',
            failed: '失败',
            blocked: '阻塞',
            retry: '重试 / 重规划',
            skipped: '已跳过',
            pending: '等待中',
        }[status] || status;
    }

    function formatTraceTime(value) {
        if (!value) return '';
        const date = new Date(String(value).replace(' ', 'T'));
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    }

    function updateAgentProcessSummary(phase, metrics = {}) {
        const process = activeAgentProcess;
        if (!process?.row?.isConnected) return;
        const title = process.row.querySelector('.agent-process-title strong');
        const subtitle = process.row.querySelector('.agent-process-title small');
        if (phase) title.textContent = phase;
        const step = metrics.step_count ?? latestTaskMetrics?.step_count ?? 0;
        const completed = metrics.completed_subtasks ?? latestTaskMetrics?.completed_subtasks ?? 0;
        const total = metrics.total_subtasks ?? latestTaskMetrics?.total_subtasks ?? 0;
        subtitle.textContent = `${step} steps${total ? ` · 子任务 ${completed}/${total}` : ''}`;
    }

    function updateAgentProcessElapsed() {
        const process = activeAgentProcess;
        if (!process?.row?.isConnected || process.finished) return;
        const elapsed = (performance.now() - process.startedAt) / 1000;
        process.row.querySelector('.agent-process-elapsed').textContent = formatElapsed(elapsed);
    }

    function finishAgentProcess(ok, metrics = {}) {
        const process = activeAgentProcess;
        if (!process?.row?.isConnected) return;
        const wasFinished = process.finished;
        process.finished = true;
        const traceRun = process.lastTrace?.run || {};
        const traceMetrics = traceRun.metrics || {};
        const finalMetrics = { ...metrics, ...traceMetrics };
        const total = Number(finalMetrics.total_subtasks || 0);
        const completed = Number(finalMetrics.completed_subtasks || 0);
        const stopped = ['stopped', 'failed', 'cancelled'].includes(String(traceRun.status || '').toLowerCase());
        const partial = completed > 0 && completed < total;
        const incomplete = total > 0 && completed < total;
        const successful = Boolean(ok) && !stopped && !finalMetrics.stop_reason && !incomplete;
        process.ok = successful;
        if (process.pollTimer) clearTimeout(process.pollTimer);
        const panel = process.row.querySelector('.agent-process-panel');
        panel.classList.remove('running');
        panel.classList.toggle('done', successful);
        panel.classList.toggle('partial', partial || (!successful && incomplete));
        panel.classList.toggle('failed', !successful && !partial && !incomplete);
        const elapsed = (performance.now() - process.startedAt) / 1000;
        const steps = finalMetrics.step_count ?? latestTaskMetrics?.step_count ?? 0;
        const hasAgentRun = Boolean(process.runId);
        const title = process.row.querySelector('.agent-process-title strong');
        if (!hasAgentRun && successful) title.textContent = '快速响应已完成';
        else if (successful) title.textContent = '执行过程已完成';
        else if (partial) title.textContent = '执行过程部分完成';
        else title.textContent = '执行过程已停止';
        const reason = finalMetrics.stop_reason ? ` · ${safeSnippet(finalMetrics.stop_reason, 80)}` : '';
        process.row.querySelector('.agent-process-title small').textContent = `${steps} steps · ${formatElapsed(elapsed)}${reason}`;
        process.row.querySelector('.agent-process-elapsed').textContent = formatElapsed(elapsed);
        if (!wasFinished && process.runId) pollAgentTrace(process);
        panel.classList.remove('expanded');
        process.row.querySelector('.agent-process-toggle').setAttribute('aria-expanded', 'false');
        process.row.querySelector('.agent-process-chevron').textContent = '⌄';
    }

    async function fetchRuntimeConfig() {
        const wasAgentEnabled = agentEnabled;
        try {
            const resp = await fetch(API_BASE + '/config');
            const data = await resp.json();
            if (data.status === 'success') {
                agentEnabled = !!(data.config.agent_open || data.config.rag_open);
            }
        } catch (e) {
            agentEnabled = false;
        }
        if (wasAgentEnabled && !agentEnabled && taskTimer) {
            clearInterval(taskTimer);
            taskTimer = null;
            taskStartedAt = 0;
        }
        setTaskMeterVisible(agentEnabled);
    }

    function formatElapsed(seconds) {
        const value = Number(seconds || 0);
        return value >= 60 ? `${Math.floor(value / 60)}m ${Math.round(value % 60)}s` : `${value.toFixed(1)}s`;
    }

    function estimateTokens(text) {
        return Math.max(0, Math.ceil(String(text || '').length / 4));
    }

    function currentElapsedSeconds() {
        return taskStartedAt ? (performance.now() - taskStartedAt) / 1000 : 0;
    }

    function setActivePhase(node) {
        taskPhaseTrack.querySelectorAll('span').forEach(item => {
            item.classList.toggle('active', item.dataset.node === node);
            item.classList.toggle('done', false);
        });
        const normalizedNode = String(node || '').endsWith('_worker') ? 'workers' : node;
        const order = ['planning', 'scheduler', 'workers', 'finalize'];
        const activeIndex = order.indexOf(normalizedNode);
        if (activeIndex >= 0) {
            taskPhaseTrack.querySelectorAll('span').forEach(item => {
                item.classList.toggle('done', order.indexOf(item.dataset.node) < activeIndex);
            });
        }
    }

    function startTaskMeter(phase) {
        if (!agentEnabled) return;
        taskStartedAt = performance.now();
        activeTaskSessionId = currentSessionId || 'new';
        backendMetricSeen = false;
        streamedOutputChars = 0;
        latestTaskMetrics = { token_count: 0, step_count: 0, completed_subtasks: 0, total_subtasks: 0 };
        taskMeter.classList.remove('idle', 'done', 'error');
        taskMeter.classList.add('running');
        taskMeterPhase.textContent = phase || '处理中';
        taskElapsed.textContent = '0.0s';
        taskTokens.textContent = '0';
        taskSteps.textContent = '0';
        taskProgress.textContent = '0/0';
        setActivePhase('');
        if (taskTimer) clearInterval(taskTimer);
        taskTimer = setInterval(() => {
            if (!taskStartedAt) return;
            const elapsed = currentElapsedSeconds();
            taskElapsed.textContent = formatElapsed(elapsed);
            updateAgentProcessElapsed();
        }, 100);
    }

    function updateTaskMeter(metrics, node) {
        if (!agentEnabled) return;
        if (!metrics) return;
        latestTaskMetrics = { ...(latestTaskMetrics || {}), ...metrics };
        if (backendMetricSeen && metrics.elapsed_seconds != null && taskStartedAt) {
            taskStartedAt = performance.now() - Number(metrics.elapsed_seconds || 0) * 1000;
        }
        taskMeter.classList.remove('idle', 'done', 'error');
        taskMeter.classList.add(metrics.status === 'completed' ? 'done' : metrics.status === 'failed' ? 'error' : 'running');
        taskMeterPhase.textContent = metrics.phase || metrics.status || '处理中';
        const elapsed = currentElapsedSeconds() || Number(metrics.elapsed_seconds || 0);
        taskElapsed.textContent = formatElapsed(elapsed);
        taskTokens.textContent = String(metrics.token_count ?? 0);
        taskSteps.textContent = String(metrics.step_count ?? 0);
        const completed = metrics.completed_subtasks ?? 0;
        const total = metrics.total_subtasks ?? 0;
        taskProgress.textContent = `${completed}/${total}`;
        if (node) setActivePhase(node);
        updateAgentProcessSummary(metrics.phase || metrics.status || '正在执行', metrics);
        if (metrics.status === 'completed' || metrics.status === 'failed' || metrics.status === 'stopped') {
            if (taskTimer) clearInterval(taskTimer);
            taskTimer = null;
            finishAgentProcess(metrics.status === 'completed', metrics);
        }
    }

    function updateStreamingEstimate(text) {
        if (!agentEnabled) return;
        if (backendMetricSeen) return;
        streamedOutputChars = String(text || '').length;
        const tokens = estimateTokens(text);
        latestTaskMetrics = { ...(latestTaskMetrics || {}), token_count: tokens, step_count: 1, completed_subtasks: 1, total_subtasks: 1 };
        const elapsed = currentElapsedSeconds();
        taskTokens.textContent = String(tokens);
        taskSteps.textContent = '1';
        taskProgress.textContent = '1/1';
        taskMeterPhase.textContent = '流式回复中';
    }

    function finishTaskMeter(ok) {
        if (!agentEnabled) return;
        if (taskTimer) clearInterval(taskTimer);
        taskTimer = null;
        if (!backendMetricSeen) {
            taskMeter.classList.remove('running', 'idle');
            taskMeter.classList.add(ok ? 'done' : 'error');
            taskMeterPhase.textContent = ok ? '回复完成' : '请求失败';
            const elapsed = currentElapsedSeconds();
            const tokens = Math.ceil(streamedOutputChars / 4);
            taskElapsed.textContent = formatElapsed(elapsed);
            taskTokens.textContent = String(tokens);
            taskSteps.textContent = ok ? '1' : '0';
            taskProgress.textContent = ok ? '1/1' : '0/1';
        }
        finishAgentProcess(ok, latestTaskMetrics || {});
    }

    function resetTaskMeter(phase) {
        if (taskTimer) clearInterval(taskTimer);
        stopAgentProcessTracking();
        taskTimer = null;
        taskStartedAt = 0;
        backendMetricSeen = false;
        streamedOutputChars = 0;
        latestTaskMetrics = null;
        setTaskMeterVisible(agentEnabled);
        if (!agentEnabled) return;
        taskMeter.className = 'task-meter idle';
        taskMeterPhase.textContent = phase || '空闲';
        taskElapsed.textContent = '0.0s';
        taskTokens.textContent = '0';
        taskSteps.textContent = '0';
        taskProgress.textContent = '0/0';
        setActivePhase('');
    }

    function handleConsoleMetric(event) {
        if (!agentEnabled) return;
        if (!event || event.type !== 'metrics') return;
        const eventSession = event.session_id || '';
        const matchesCurrent = currentSessionId && eventSession === currentSessionId;
        const matchesActive = activeTaskSessionId && eventSession === activeTaskSessionId;
        if (!matchesCurrent && !matchesActive) return;
        if (!taskStartedAt && (event.metrics || {}).status !== 'running') return;
        backendMetricSeen = true;
        if (!taskStartedAt) {
            taskStartedAt = performance.now() - Number((event.metrics || {}).elapsed_seconds || 0) * 1000;
        }
        if (event.run_id) {
            localStorage.setItem('latest_agent_run_id', event.run_id);
            attachAgentProcessRun(event.run_id);
        }
        updateTaskMeter(event.metrics || {}, event.node || '');
        queueAgentTracePoll(120);
    }

    function connectAgentMetricsSSE() {
        const evtSource = new EventSource(API_BASE + '/console/stream');
        evtSource.onmessage = (event) => {
            try {
                handleConsoleMetric(JSON.parse(event.data));
            } catch (e) {
                // heartbeat / non-metrics events
            }
        };
        evtSource.onerror = () => {
            evtSource.close();
            setTimeout(connectAgentMetricsSSE, 5000);
        };
    }

    // ==================== 陪伴间隔配置 ====================
    async function fetchCompanionInterval() {
        try {
            const resp = await fetch(API_BASE + '/config');
            const data = await resp.json();
            if (data.status === 'success') {
                agentEnabled = !!(data.config.agent_open || data.config.rag_open);
                setTaskMeterVisible(agentEnabled);
                if (data.config.image_gen_probability != null) imageGenProbability = data.config.image_gen_probability;
            }
        } catch (e) { /* 保持默认值 */ }
    }

    // ==================== TTS 控制 ====================
    async function initTTSControls() {
        const voiceSelect = document.getElementById('tts-voice-select');
        const muteBtn = document.getElementById('tts-mute-btn');

        // 从后端拉取音色列表
        let voices = [];
        try {
            const resp = await fetch(API_BASE + '/tts/voices');
            const data = await resp.json();
            if (data.status === 'success') voices = data.voices;
        } catch (e) {
            console.error('获取音色列表失败:', e);
        }

        // 构建分组选项
        if (voiceSelect) {
            voiceSelect.innerHTML = '';
            const edgeGroup = document.createElement('optgroup');
            edgeGroup.label = '─ EdgeTTS ─';
            const fishGroup = document.createElement('optgroup');
            fishGroup.label = '─ FishAudio ─';

            voices.forEach(v => {
                const opt = document.createElement('option');
                opt.value = JSON.stringify({ voice: v.voice, provider: v.provider });
                opt.textContent = v.name;
                if (v.provider === 'fish_audio') {
                    fishGroup.appendChild(opt);
                } else {
                    edgeGroup.appendChild(opt);
                }
            });
            voiceSelect.appendChild(edgeGroup);
            if (fishGroup.children.length > 0) voiceSelect.appendChild(fishGroup);

            // 恢复上次选择的音色
            const savedVoice = localStorage.getItem('tts_voice');
            if (savedVoice) {
                for (const opt of voiceSelect.options) {
                    if (opt.value === savedVoice) { voiceSelect.value = savedVoice; break; }
                }
            }
            voiceSelect.onchange = () => {
                localStorage.setItem('tts_voice', voiceSelect.value);
            };
        }

        if (muteBtn) {
            if (ttsMuted) muteBtn.classList.add('muted');
            muteBtn.textContent = ttsMuted ? '🔇' : '🔊';
            muteBtn.onclick = () => {
                ttsMuted = !ttsMuted;
                localStorage.setItem('tts_muted', ttsMuted);
                muteBtn.textContent = ttsMuted ? '🔇' : '🔊';
                muteBtn.classList.toggle('muted', ttsMuted);
                if (ttsMuted && typeof Live2DCtrl !== 'undefined') {
                    Live2DCtrl.stopVoice?.('muted');
                }
                if (!ttsMuted && typeof Live2DCtrl !== 'undefined') {
                    Live2DCtrl.unlockAudio().catch((error) => console.warn('音频初始化失败:', error));
                }
            };
        }
    }

    function getTTSVoice() {
        const saved = localStorage.getItem('tts_voice');
        if (saved) {
            try { return JSON.parse(saved); } catch (e) { /* fall through */ }
        }
        return { voice: 'zh-CN-XiaoyiNeural', provider: 'edge_tts' };
    }

    // ==================== 会话列表 ====================
    async function fetchSessions() {
        try {
            const response = await fetch(API_BASE + '/sessions');
            const data = await response.json();
            let sessions = data.sessions;
            if (!sessions || sessions.length === 0) {
                sessions = [{ session_id: '', title: '快来开启新对话吧喵~' }];
            }
            renderSessionList(sessions);
        } catch (error) {
            console.error('加载会话列表失败:', error);
        }
    }

    function renderSessionList(sessions) {
        sessionListContainer.innerHTML = '';
        sessions.forEach(session => {
            const sid = session.session_id || session.id;
            const div = document.createElement('div');
            div.className = `session-item ${sid === currentSessionId ? 'active' : ''}`;
            if (sid) {
                div.innerHTML = `<span class="title">${escHtml(session.title)}</span><button class="delete-btn" title="删除这个对话喵">✖</button>`;
            } else {
                div.innerHTML = `<span class="title">${escHtml(session.title)}</span>`;
            }
            div.onclick = (e) => {
                if (e.target.classList.contains('delete-btn')) {
                    e.stopPropagation();
                    deleteSession(sid, e.target);
                } else if (sid) {
                    switchSession(sid);
                }
            };
            sessionListContainer.appendChild(div);
        });
    }

    async function switchSession(sid) {
        if (!sid) return;
        stopAgentProcessTracking();
        currentSessionId = sid;
        localStorage.setItem('last_session_id', sid);
        activeTaskSessionId = sid;
        resetTaskMeter('空闲');
        await fetchSessions();
        chatWindow.innerHTML = '';
        await loadHistory(sid);
        await restoreLatestAgentProcess(sid);
    }

    async function deleteSession(sid, btnEl) {
        if (!confirm('确定要彻底删掉这段回忆喵？不可恢复哦！')) return;
        if (btnEl) {
            btnEl.textContent = '';
            btnEl.classList.add('spinning');
        }
        try {
            const response = await fetch(`${API_BASE}/session/${sid}`, { method: 'DELETE' });
            const data = await response.json();
            if (data.status === 'success') {
                if (sid === currentSessionId) {
                    currentSessionId = '';
                    localStorage.removeItem('last_session_id');
                    chatWindow.innerHTML = buildAiMsgRow('对话已删除，快来开启新对话吧喵~');
                }
                await fetchSessions();
            }
        } catch (error) {
            console.error('删除失败喵:', error);
        }
    }

    async function loadHistory(sid) {
        if (!sid) return;
        try {
            const response = await fetch(`${API_BASE}/history/${sid}`);
            const data = await response.json();
            if (data.status === 'success') {
                chatWindow.innerHTML = '';
                data.history.forEach(msg => {
                    if (msg.role === 'user-msg') {
                        renderUserMessage(msg.content);
                    } else if (msg.role === 'ai-img') {
                        renderHistoryImage(msg.image, msg.content);
                    } else {
                        renderAiMessage(msg.content);
                    }
                });
            }
        } catch (error) {
            console.error('加载历史失败:', error);
        }
    }

    // ==================== 头像 HTML 构建 ====================
    function buildUserAvatarImg(url) {
        const img = document.createElement('img');
        img.className = 'msg-avatar msg-avatar-user';
        img.src = url;
        img.alt = '我';
        img.decoding = 'async';
        img.draggable = false;
        img.title = '点击更换头像';
        img.onclick = onUserAvatarClick;
        return img;
    }

    function buildAiAvatarImg(url) {
        const img = document.createElement('img');
        img.className = 'msg-avatar msg-avatar-ai';
        img.src = url;
        img.alt = 'AI';
        img.decoding = 'async';
        img.draggable = false;
        img.title = '点击更换AI头像';
        img.onclick = onAiAvatarClick;
        return img;
    }

    function buildUserAvatarHTML() {
        const url = getUserAvatarUrl();
        if (url) {
            return `<img class="msg-avatar msg-avatar-user" src="${escHtml(url)}" alt="我" title="查看或更换头像" width="40" height="40" decoding="async" draggable="false">`;
        }
        return `<div class="msg-avatar-placeholder avatar-placeholder-user" style="background:#ffe0e6;color:#ff7675;" title="点击上传头像">🐱</div>`;
    }

    function buildAiAvatarHTML() {
        const url = getAiAvatarUrl();
        if (url) {
            return `<img class="msg-avatar msg-avatar-ai" src="${escHtml(url)}" alt="AI" title="查看或更换AI头像" width="40" height="40" decoding="async" draggable="false">`;
        }
        return `<div class="msg-avatar-placeholder avatar-placeholder-ai" style="background:#e0f0ff;color:#4285f4;" title="点击上传AI头像">🤖</div>`;
    }

    // 给已渲染的 avatar 元素绑定点击事件
    function bindAvatarClicks(row) {
        const userAvatar = row.querySelector('.msg-avatar-user, .avatar-placeholder-user');
        if (userAvatar) userAvatar.onclick = onUserAvatarClick;
        const aiAvatar = row.querySelector('.msg-avatar-ai, .avatar-placeholder-ai');
        if (aiAvatar) aiAvatar.onclick = onAiAvatarClick;
    }

    // ==================== 消息渲染 ====================
    function buildUserMsgRow(text, imageB64) {
        const div = document.createElement('div');
        div.className = 'msg-row user-row';
        let inner = buildUserAvatarHTML();
        inner += `<div class="message user-msg">`;
        if (imageB64) {
            inner += `<img src="data:image/jpeg;base64,${imageB64}" alt="上传图片">`;
        }
        inner += `${escHtml(text)}</div>`;
        div.innerHTML = inner;
        bindAvatarClicks(div);
        return div;
    }

    function buildAiMsgRow(text) {
        const div = document.createElement('div');
        div.className = 'msg-row ai-row';
        div.innerHTML = buildAiAvatarHTML() + `<div class="message ai-msg">${escHtml(text)}</div>`;
        bindAvatarClicks(div);
        return div;
    }

    function renderUserMessage(text, imageB64) {
        const row = buildUserMsgRow(text, imageB64);
        chatWindow.appendChild(row);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    function renderAiMessage(text) {
        const row = buildAiMsgRow(text);
        chatWindow.appendChild(row);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    // 思考中气泡 — 带"正在思考"文字 + 跳动的点
    function buildThinkingRow() {
        const div = document.createElement('div');
        div.className = 'msg-row ai-row thinking-row';
        div.id = 'thinking-row';
        div.innerHTML = buildAiAvatarHTML() +
            `<div class="message ai-msg" style="display:flex;align-items:center;min-height:28px;">
                <div class="thinking-spinner" aria-hidden="true"></div>
                <span class="thinking-label">正在思考</span>
            </div>`;
        bindAvatarClicks(div);
        return div;
    }

    function showThinking() {
        const row = buildThinkingRow();
        chatWindow.appendChild(row);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    function setThinkingStatus(text) {
        const row = document.getElementById('thinking-row');
        const label = row ? row.querySelector('.thinking-label') : null;
        if (label) label.textContent = text;
    }

    // 将思考行原地转为正式消息气泡，返回气泡 DOM。
    function claimThinkingRow() {
        const row = document.getElementById('thinking-row');
        if (!row) return null;
        row.id = '';
        row.classList.remove('thinking-row');
        const bubble = row.querySelector('.message');
        if (bubble) bubble.removeAttribute('style');
        return bubble;
    }

    function hideThinking() {
        const row = document.getElementById('thinking-row');
        if (row) row.remove();
    }

    function claimOrCreateAiBubble() {
        let aiBubble = claimThinkingRow();
        if (aiBubble) return aiBubble;

        hideThinking();
        const aiRow = document.createElement('div');
        aiRow.className = 'msg-row ai-row';
        aiRow.innerHTML = buildAiAvatarHTML() + '<div class="message ai-msg"></div>';
        bindAvatarClicks(aiRow);
        chatWindow.appendChild(aiRow);
        return aiRow.querySelector('.message');
    }

    function createStreamingTextPresenter(aiBubble, { startPaused = false } = {}) {
        const targetCharacters = [];
        let renderedCount = 0;
        let paused = startPaused;
        let inputDone = false;
        let timer = null;
        let timing = null;
        let resolved = false;
        let resolveDrained;
        const drained = new Promise((resolve) => { resolveDrained = resolve; });

        aiBubble.textContent = '';
        aiBubble.classList.add('is-streaming');
        aiBubble.setAttribute('aria-live', 'polite');
        aiBubble.setAttribute('aria-busy', 'true');

        function completeIfReady() {
            if (!inputDone || renderedCount < targetCharacters.length || resolved) return false;
            resolved = true;
            aiBubble.classList.remove('is-streaming');
            aiBubble.setAttribute('aria-busy', 'false');
            resolveDrained();
            return true;
        }

        function schedule() {
            if (paused || timer || resolved) return;
            timer = setTimeout(tick, 20);
        }

        function tick() {
            timer = null;
            if (paused || resolved) return;
            const backlog = targetCharacters.length - renderedCount;
            if (backlog <= 0) {
                completeIfReady();
                return;
            }

            let batchSize;
            if (timing && renderedCount < timing.characterCount) {
                const elapsed = performance.now() - timing.startedAt;
                const progress = Math.min(1, elapsed / timing.durationMs);
                const expectedCount = Math.max(1, Math.floor(progress * timing.characterCount));
                if (expectedCount <= renderedCount) {
                    schedule();
                    return;
                }
                batchSize = expectedCount - renderedCount;
            } else {
                batchSize = Math.min(8, Math.max(1, Math.ceil(backlog / 48)));
            }

            renderedCount = Math.min(targetCharacters.length, renderedCount + batchSize);
            const visibleText = targetCharacters.slice(0, renderedCount).join('');
            aiBubble.textContent = visibleText;
            updateStreamingEstimate(visibleText);
            chatWindow.scrollTop = chatWindow.scrollHeight;
            if (!completeIfReady()) schedule();
        }

        return {
            append(text) {
                if (text) targetCharacters.push(...Array.from(text));
                schedule();
            },
            release({ durationMs = 0, timedCharacters = 0 } = {}) {
                paused = false;
                if (durationMs > 0 && timedCharacters > 0) {
                    timing = {
                        startedAt: performance.now(),
                        durationMs: Math.max(300, durationMs),
                        characterCount: Math.min(targetCharacters.length, timedCharacters),
                    };
                }
                schedule();
            },
            flush() {
                paused = false;
                timing = null;
                if (timer) clearTimeout(timer);
                timer = null;
                renderedCount = targetCharacters.length;
                const visibleText = targetCharacters.join('');
                aiBubble.textContent = visibleText;
                updateStreamingEstimate(visibleText);
                chatWindow.scrollTop = chatWindow.scrollHeight;
                completeIfReady();
            },
            finish() {
                inputDone = true;
                if (!completeIfReady()) schedule();
                return drained;
            },
        };
    }

    // ==================== 图片上传 ====================
    let selectedImageB64 = null;
    const uploadImgBtn = document.getElementById('upload-img-btn');
    const imageInput = document.getElementById('image-input');
    const imagePreviewRow = document.getElementById('image-preview-row');
    const imagePreview = document.getElementById('image-preview');
    const removeImgBtn = document.getElementById('remove-img-btn');

    uploadImgBtn.onclick = () => imageInput.click();

    imageInput.onchange = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        if (file.size > 10 * 1024 * 1024) { alert('图片太大，请小于10MB'); return; }
        const reader = new FileReader();
        reader.onload = (ev) => {
            const img = new Image();
            img.onload = () => {
                const canvas = document.createElement('canvas');
                let w = img.width, h = img.height;
                const max = 1024;
                if (w > max || h > max) {
                    if (w > h) { h *= max / w; w = max; }
                    else { w *= max / h; h = max; }
                }
                canvas.width = w; canvas.height = h;
                canvas.getContext('2d').drawImage(img, 0, 0, w, h);
                selectedImageB64 = canvas.toDataURL('image/jpeg', 0.7).split(',')[1];
                imagePreview.src = URL.createObjectURL(file);
                imagePreviewRow.style.display = 'flex';
            };
            img.src = ev.target.result;
        };
        reader.readAsDataURL(file);
    };

    removeImgBtn.onclick = () => {
        selectedImageB64 = null;
        imagePreviewRow.style.display = 'none';
        imageInput.value = '';
    };

    // ==================== 发送消息 ====================
    sendBtn.onclick = () => sendMessage();
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    async function prepareSpeech(text, externalSignal = null) {
        const spokenText = Array.from(text).slice(0, 200).join('');
        const voice = getTTSVoice();
        const controller = new AbortController();
        const abortFromOutside = () => controller.abort();
        if (externalSignal) {
            if (externalSignal.aborted) controller.abort();
            else externalSignal.addEventListener('abort', abortFromOutside, { once: true });
        }
        const timeout = setTimeout(() => controller.abort(), 45000);
        try {
            const response = await fetch(API_BASE + '/tts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: spokenText,
                    voice: voice.voice,
                    provider: voice.provider,
                }),
                signal: controller.signal,
            });
            if (!response.ok) throw new Error(`TTS 请求失败: HTTP ${response.status}`);
            const data = await response.json();
            if (data.status !== 'success' || !data.audio) {
                throw new Error(data.msg || 'TTS 未返回音频');
            }
            return { audio: data.audio, spokenText };
        } finally {
            clearTimeout(timeout);
            externalSignal?.removeEventListener('abort', abortFromOutside);
        }
    }

    async function consumeStreamingAnswer(response, { ttsEnabled = false, signal = null } = {}) {
        if (!response.ok) throw new Error(`聊天请求失败: HTTP ${response.status}`);
        if (!response.body) throw new Error('浏览器未提供流式响应体');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let presenter = null;

        function ensurePresenter(startPaused = false) {
            if (!presenter) {
                presenter = createStreamingTextPresenter(claimOrCreateAiBubble(), { startPaused });
            }
            return presenter;
        }

        function acceptChunk(chunk) {
            if (!chunk) return;
            fullText += chunk;
            if (ttsEnabled) {
                updateStreamingEstimate(fullText);
            } else {
                ensurePresenter().append(chunk);
            }
        }

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            acceptChunk(decoder.decode(value, { stream: true }));
        }
        acceptChunk(decoder.decode());

        if (!fullText) {
            const aiBubble = claimOrCreateAiBubble();
            aiBubble.textContent = '连接已结束，但没有收到回答。';
            return '';
        }

        if (ttsEnabled) {
            setThinkingStatus('正在生成语音');
            let speech = null;
            let playback = null;
            try {
                speech = await prepareSpeech(fullText, signal);
                lastAssistantSpeech = speech.spokenText;
                setThinkingStatus('语音已就绪，正在播放');
                playback = await Live2DCtrl.playVoiceWithLipSync(speech.audio);
                Live2DCtrl.showMsg(speech.spokenText.slice(0, 100));
            } catch (error) {
                console.error('TTS 失败，已降级为文字流:', error);
                setThinkingStatus('语音生成失败，切换文字输出');
            }

            const gatedPresenter = ensurePresenter(true);
            gatedPresenter.append(fullText);
            gatedPresenter.release({
                durationMs: playback && playback.duration ? playback.duration * 1000 : 0,
                timedCharacters: speech ? Array.from(speech.spokenText).length : 0,
            });
            if (playback?.ended) {
                playback.ended.then(({ interrupted }) => {
                    if (interrupted) gatedPresenter.flush();
                });
            }
        }

        await ensurePresenter().finish();
        return fullText;
    }

    async function sendMessage({
        textOverride = null,
        imageOverride = null,
        source = 'typed',
        signal = null,
    } = {}) {
        const fromVoice = source === 'voice';
        const text = typeof textOverride === 'string' ? textOverride.trim() : userInput.value.trim();
        const imageForMessage = imageOverride || (fromVoice ? null : selectedImageB64);
        const hasImage = !!imageForMessage;
        if (!text && !hasImage) return;
        const ttsEnabledForMessage = !ttsMuted && typeof Live2DCtrl !== 'undefined';
        if (ttsEnabledForMessage) {
            Live2DCtrl.unlockAudio().catch((error) => console.warn('音频初始化失败:', error));
        }

        if (hasImage) {
            renderUserMessage(text || '看看这张图片', imageForMessage);
        } else {
            renderUserMessage(text);
        }
        if (!fromVoice) {
            userInput.value = '';
            selectedImageB64 = null;
            imagePreviewRow.style.display = 'none';
        }
        const imgToSend = imageForMessage;

        startTaskMeter('请求已发送');
        if (agentEnabled) startAgentProcess();
        showThinking();

        try {
            const response = await fetch(API_BASE + '/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: currentSessionId || 'new',
                    message: text || '请描述这张图片',
                    image: imgToSend || undefined
                }),
                signal,
            });

            const newSid = response.headers.get('X-Session-Id');
            if (newSid && newSid !== currentSessionId) {
                currentSessionId = newSid;
                localStorage.setItem('last_session_id', newSid);
                activeTaskSessionId = newSid;
                fetchSessions();
            }

            const fullText = await consumeStreamingAnswer(response, {
                ttsEnabled: ttsEnabledForMessage,
                signal,
            });
            if (signal?.aborted && fromVoice) {
                resetTaskMeter('已打断');
                return '';
            }
            finishTaskMeter(!!fullText);

            // 随机概率触发 AI 虚拟生活场景生图
            if (!signal?.aborted && Math.random() < imageGenProbability && currentSessionId) {
                try {
                    console.log('[ImageGen] 触发，概率:', imageGenProbability, 'session:', currentSessionId);
                    const imgResp = await fetch(API_BASE + '/image-gen', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ session_id: currentSessionId })
                    });
                    const imgData = await imgResp.json();
                    console.log('[ImageGen] 响应:', imgData.status, imgData.scene || imgData.msg);
                    if (imgData.status === 'success' && imgData.image) {
                        renderGeneratedImage(imgData.image, imgData.scene || 'AI 的生活瞬间');
                    }
                } catch (e) {
                    console.error('[ImageGen] 请求失败:', e);
                }
            }

            return fullText;

        } catch (error) {
            hideThinking();
            if (error?.name === 'AbortError' && fromVoice) {
                resetTaskMeter('已打断');
                return '';
            }
            renderAiMessage('呜呜，断线了喵...');
            finishTaskMeter(false);
            return '';
        }
    }

    function renderGeneratedImage(imageSrc, caption) {
        const row = document.createElement('div');
        row.className = 'msg-row ai-row';
        const img = document.createElement('img');
        // 支持原始 base64、data: URL 或路径
        if (imageSrc.startsWith('data:')) {
            img.src = imageSrc;
        } else if (imageSrc.startsWith('/')) {
            img.src = API_BASE + imageSrc;
        } else {
            img.src = 'data:image/png;base64,' + imageSrc;
        }
        img.style.cssText = 'max-width:200px;border-radius:12px;display:block;';
        const cap = document.createElement('span');
        cap.style.cssText = 'font-size:11px;color:#999;margin-top:4px;display:block;';
        cap.textContent = '🎨 ' + (caption || 'AI 的生活瞬间');
        const bubble = document.createElement('div');
        bubble.className = 'message ai-msg';
        bubble.appendChild(img);
        bubble.appendChild(cap);
        row.innerHTML = buildAiAvatarHTML();
        row.appendChild(bubble);
        bindAvatarClicks(row);
        chatWindow.appendChild(row);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    function renderHistoryImage(imagePath, caption) {
        renderGeneratedImage(imagePath, caption);
    }

    // ==================== 沉浸陪伴：持续聆听 + 语音打断 + AI 按需看屏幕 ====================
    const companionBtn = document.getElementById('companion-btn');
    const desktopPetBtn = document.getElementById('desktop-pet-btn');
    const voiceHud = document.getElementById('immersive-voice-hud');
    const voiceHudStatus = document.getElementById('voice-hud-status');
    const voiceHudTranscript = document.getElementById('voice-hud-transcript');
    const screenAccessState = document.getElementById('screen-access-state');
    let lastBargeInAt = 0;

    function setImmersiveState(state, status, detail = '') {
        if (voiceHud) voiceHud.dataset.state = state;
        if (voiceHudStatus && status) voiceHudStatus.textContent = status;
        if (voiceHudTranscript && detail) voiceHudTranscript.textContent = detail;
    }

    function setScreenAccessState(state) {
        if (!screenAccessState) return;
        screenAccessState.classList.toggle('ready', state === 'ready');
        screenAccessState.classList.toggle('reading', state === 'reading');
        const labels = {
            offline: '屏幕未连接',
            ready: '按需读取',
            reading: '正在读取一帧',
        };
        screenAccessState.textContent = labels[state] || labels.offline;
    }

    function updateCompanionButton(active) {
        if (!companionBtn) return;
        companionBtn.classList.toggle('active', active);
        companionBtn.setAttribute('aria-pressed', String(active));
        const title = companionBtn.querySelector('strong');
        const description = companionBtn.querySelector('small');
        if (title) title.textContent = active ? '结束沉浸陪伴' : '沉浸陪伴';
        if (description) description.textContent = active ? '持续聆听中 · 可随时打断' : '随时说话 · 按需看屏幕';
    }

    function normalizeSpeechForEcho(text) {
        return String(text || '')
            .toLowerCase()
            .replace(/[\s，。！？、,.!?；;：:“”"'（）()\-—…]/g, '');
    }

    function isLikelySpeakerEcho(text) {
        if (!lastAssistantSpeech || Date.now() - lastBargeInAt > 6000) return false;
        const spoken = normalizeSpeechForEcho(lastAssistantSpeech);
        const heard = normalizeSpeechForEcho(text);
        return heard.length >= 6 && (spoken.includes(heard) || heard.includes(spoken.slice(0, Math.min(heard.length, 24))));
    }

    function fallbackNeedsScreen(text) {
        const compact = String(text || '').replace(/\s/g, '');
        if (/不要截图|别截图|不要看屏幕|别看屏幕|停止共享|关闭共享/.test(compact)) return false;
        return /屏幕|页面|画面|窗口|这个报错|这个错误|这段代码|这个按钮|帮我看看这个|看一下这个|我现在在看|我在做什么|浏览器里|软件里/.test(compact);
    }

    async function decideWhetherToReadScreen(text) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 14000);
        try {
            const response = await fetch(API_BASE + '/companion/intent', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text }),
                signal: controller.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            return data.status === 'success' ? Boolean(data.needs_screen) : fallbackNeedsScreen(text);
        } catch (error) {
            console.warn('屏幕意图判断失败，使用本地保守判断:', error);
            return fallbackNeedsScreen(text);
        } finally {
            clearTimeout(timeout);
        }
    }

    async function captureCurrentScreenFrame(track) {
        if (!track || track.readyState === 'ended') throw new Error('屏幕通道已结束');

        let source;
        let sourceWidth;
        let sourceHeight;
        if (typeof ImageCapture !== 'undefined') {
            const imageCapture = new ImageCapture(track);
            source = await imageCapture.grabFrame();
            sourceWidth = source.width;
            sourceHeight = source.height;
        } else {
            const video = document.createElement('video');
            video.muted = true;
            video.playsInline = true;
            video.srcObject = companionScreenStream;
            await video.play();
            if (!video.videoWidth || !video.videoHeight) {
                await new Promise((resolve) => video.addEventListener('loadedmetadata', resolve, { once: true }));
            }
            source = video;
            sourceWidth = video.videoWidth;
            sourceHeight = video.videoHeight;
        }

        const max = 1280;
        let width = sourceWidth;
        let height = sourceHeight;
        if (width > max || height > max) {
            if (width > height) { height *= max / width; width = max; }
            else { width *= max / height; height = max; }
        }

        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(width));
        canvas.height = Math.max(1, Math.round(height));
        canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
        if (typeof source.close === 'function') source.close();
        return canvas.toDataURL('image/jpeg', 0.68).split(',')[1];
    }

    async function processVoiceUtterance(text) {
        const utterance = String(text || '').trim();
        if (!utterance || !companionActive) return;
        if (isLikelySpeakerEcho(utterance)) {
            setImmersiveState('listening', '正在聆听', '已过滤扬声器回声，继续说就好');
            return;
        }

        if (companionVoiceTurnRunning) {
            queuedVoiceUtterance = queuedVoiceUtterance
                ? `${queuedVoiceUtterance} ${utterance}`
                : utterance;
            setImmersiveState('speech', '我听到了', '上一句话结束后马上回应你');
            return;
        }

        companionVoiceTurnRunning = true;
        let currentUtterance = utterance;
        try {
            while (currentUtterance && companionActive) {
                queuedVoiceUtterance = '';
                setImmersiveState('thinking', '正在理解你的意思', '判断这句话是否需要看屏幕');
                const needsScreen = await decideWhetherToReadScreen(currentUtterance);
                if (!companionActive) break;

                let screenFrame = null;
                if (needsScreen && companionScreenTrack?.readyState === 'live') {
                    setImmersiveState('capturing', '正在看你指的内容', '只读取当前这一帧画面');
                    setScreenAccessState('reading');
                    try {
                        screenFrame = await captureCurrentScreenFrame(companionScreenTrack);
                    } catch (error) {
                        console.error('按需截屏失败:', error);
                    } finally {
                        setScreenAccessState(companionActive ? 'ready' : 'offline');
                    }
                }

                setImmersiveState('thinking', 'LightMe 正在想', screenFrame ? '已经看到了你指的屏幕内容' : '这句话不需要读取屏幕');
                const turnController = new AbortController();
                companionChatController = turnController;
                try {
                    await sendMessage({
                        textOverride: currentUtterance,
                        imageOverride: screenFrame,
                        source: 'voice',
                        signal: turnController.signal,
                    });
                } finally {
                    if (companionChatController === turnController) companionChatController = null;
                }
                currentUtterance = queuedVoiceUtterance.trim();
            }
        } finally {
            companionVoiceTurnRunning = false;
            if (companionActive) {
                setImmersiveState('listening', '正在聆听', '可以继续说，也可以随时打断我');
            }
        }
    }

    function startImmersiveVoiceInput() {
        return Live2DCtrl.startVoiceInput({
            mode: 'immersive',
            onSpeechStart: () => {
                if (Live2DCtrl.isVoicePlaying?.()) {
                    lastBargeInAt = Date.now();
                    Live2DCtrl.stopVoice('barge-in');
                }
                if (companionChatController) {
                    companionChatController.abort();
                    companionChatController = null;
                }
                setImmersiveState('speech', '我在听', '直接说就好');
            },
            onInterim: (text) => {
                setImmersiveState('speech', '我在听', text);
            },
            onFinal: (text) => {
                Live2DCtrl.showMsg(`听到了：${text}`);
                processVoiceUtterance(text);
            },
            onStateChange: (state) => {
                if (!companionActive || companionVoiceTurnRunning) return;
                if (state === 'listening' || state === 'reconnecting') {
                    setImmersiveState('listening', '正在聆听', '可以随时说话，也可以打断我');
                } else if (state === 'idle') {
                    setImmersiveState('idle', '聆听已暂停', '点击角色下方的麦克风继续');
                } else if (state === 'blocked') {
                    setImmersiveState('idle', '麦克风权限未开启', '请允许麦克风后重新开启陪伴');
                }
            },
            onError: (error) => {
                if (!['unsupported', 'not-allowed', 'service-not-allowed'].includes(error)) return;
                renderAiMessage('沉浸陪伴需要麦克风权限；请允许麦克风后再开启。');
                stopCompanion({ announce: false });
            },
        });
    }

    async function startCompanion() {
        if (!window.SpeechRecognition && !window.webkitSpeechRecognition) {
            renderAiMessage('当前浏览器暂不支持持续语音识别，请使用最新版 Chrome 或 Edge。');
            return;
        }
        try {
            const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
            const track = stream.getVideoTracks()[0];
            companionScreenStream = stream;
            companionScreenTrack = track;
            companionActive = true;
            updateCompanionButton(true);
            setScreenAccessState('ready');
            setImmersiveState('listening', '正在开启麦克风', '屏幕只会在对话需要时读取一帧');

            if (!startImmersiveVoiceInput()) {
                throw new Error('无法启动语音识别');
            }
            Live2DCtrl.unlockAudio().catch((error) => console.warn('音频初始化失败:', error));
            renderAiMessage('沉浸陪伴已开启。你可以随时说话，也可以在我说话时直接打断；屏幕只会在你的问题确实需要时读取一帧。');

            track.addEventListener('ended', () => {
                if (companionActive) stopCompanion({ reason: 'screen-ended' });
            });
        } catch (error) {
            console.error('沉浸陪伴启动失败:', error);
            hideThinking();
            stopCompanion({ announce: false });
            renderAiMessage('开启沉浸陪伴需要屏幕共享和麦克风权限，请在浏览器提示中允许。');
        }
    }

    function stopCompanion({ announce = true, reason = 'manual' } = {}) {
        const wasActive = companionActive;
        companionActive = false;
        companionVoiceTurnRunning = false;
        queuedVoiceUtterance = '';
        if (companionChatController) {
            companionChatController.abort();
            companionChatController = null;
        }
        Live2DCtrl.stopVoiceInput?.();
        Live2DCtrl.stopVoice?.('companion-stopped');
        if (companionScreenStream) {
            companionScreenStream.getTracks().forEach((track) => {
                if (track.readyState !== 'ended') track.stop();
            });
        }
        companionScreenStream = null;
        companionScreenTrack = null;
        updateCompanionButton(false);
        setScreenAccessState('offline');
        setImmersiveState('idle', '等待开启沉浸陪伴', '可以随时说话，也可以打断我');
        if (announce && wasActive && !document.getElementById('thinking-row')) {
            renderAiMessage(reason === 'screen-ended' ? '屏幕共享已结束，沉浸陪伴也暂停了。' : '沉浸陪伴已结束，想继续聊时再叫我。');
        }
    }

    window.addEventListener('lightme:voice-start', () => {
        if (companionActive) setImmersiveState('speaking', 'LightMe 正在回应', '你可以直接说话打断');
    });

    window.addEventListener('lightme:voice-end', (event) => {
        if (!companionActive) return;
        const interrupted = Boolean(event.detail?.interrupted);
        setImmersiveState('listening', interrupted ? '已停下，听你说' : '正在聆听', '可以继续说话');
    });

    companionBtn.onclick = () => {
        if (companionActive) stopCompanion();
        else startCompanion();
    };

    if (desktopPetBtn) {
        desktopPetBtn.onclick = async () => {
            desktopPetBtn.classList.add('loading');
            const oldText = desktopPetBtn.textContent;
            desktopPetBtn.textContent = '启动中';
            try {
                const resp = await fetch(API_BASE + '/desktop-pet/open', { method: 'POST' });
                const data = await resp.json();
                if (data.status === 'success') {
                    renderAiMessage(data.msg || '桌面宠物已启动');
                } else {
                    alert(data.msg || '桌面宠物启动失败');
                }
            } catch (e) {
                alert('桌面宠物启动失败，请确认当前是桌面 GUI 环境');
            } finally {
                desktopPetBtn.textContent = oldText;
                desktopPetBtn.classList.remove('loading');
            }
        };
    }

    // ==================== 新建对话 ====================
    newChatBtn.onclick = () => {
        currentSessionId = '';
        activeTaskSessionId = '';
        localStorage.removeItem('last_session_id');
        chatWindow.innerHTML = '';
        resetTaskMeter('空闲');
        fetchSessions();
        renderAiMessage('新对话已开启，请发送消息喵！');
    };

    // ==================== 工具 & 技能弹窗 ====================
    const toolsBtn = document.getElementById('tools-btn');
    const toolsModal = document.getElementById('tools-modal-overlay');
    const toolsModalClose = document.getElementById('tools-modal-close');
    const skillsPane = document.getElementById('skills-pane');
    const toolsPane = document.getElementById('tools-pane');
    const modalSummary = document.getElementById('modal-summary');

    toolsBtn.onclick = () => {
        toolsModal.style.display = 'flex';
        fetchToolsAndSkills();
    };
    toolsModalClose.onclick = () => { toolsModal.style.display = 'none'; };
    toolsModal.onclick = (e) => { if (e.target === toolsModal) toolsModal.style.display = 'none'; };

    // 标签页切换
    document.querySelectorAll('.modal-tab').forEach(tab => {
        tab.onclick = () => {
            document.querySelectorAll('.modal-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            const paneId = tab.dataset.tab;
            skillsPane.style.display = paneId === 'skills-pane' ? 'block' : 'none';
            toolsPane.style.display = paneId === 'tools-pane' ? 'block' : 'none';
        };
    });

    const catClassMap = {
        search: 'cat-search', execute: 'cat-execute',
        create: 'cat-create', analyze: 'cat-analyze'
    };

    async function fetchToolsAndSkills() {
        try {
            const resp = await fetch(API_BASE + '/tools-and-skills');
            const data = await resp.json();
            if (data.status !== 'success') return;

            // 摘要
            modalSummary.textContent =
                `共 ${data.total_skills} 个技能，${data.total_tools} 个工具可用`;

            // 渲染技能列表
            let skillsHTML = '';
            data.skills.forEach(s => {
                const kwTags = (s.keywords || []).slice(0, 8).map(k =>
                    `<span class="skill-kw-tag">${escHtml(k)}</span>`
                ).join('');
                const catClass = catClassMap[s.category] || 'cat-general';
                const toolsNote = s.tools.length
                    ? `<div class="skill-tools-tag">🔧 ${s.tools.map(t => t.name).join(', ')}</div>`
                    : '';
                skillsHTML += `
                    <div class="skill-card">
                        <div class="skill-card-header">
                            <span class="skill-name">${escHtml(s.name)}</span>
                            <span class="skill-category ${catClass}">${escHtml(s.category)}</span>
                        </div>
                        <div class="skill-desc">${escHtml(s.description)}</div>
                        <div class="skill-keywords">${kwTags}</div>
                        ${toolsNote}
                    </div>`;
            });
            skillsPane.innerHTML = skillsHTML || '<p style="color:#999;">没有已注册的技能</p>';

            // 渲染工具列表
            let toolsHTML = '';

            // 基础工具
            toolsHTML += '<div class="base-tool-section"><div class="section-label">基础工具</div>';
            data.base_tools.forEach(t => {
                toolsHTML += `
                    <div class="tool-item">
                        <div class="tool-icon">🔧</div>
                        <div class="tool-info">
                            <div class="tool-name">${escHtml(t.name)}</div>
                            <div class="tool-desc">${escHtml(t.description)}</div>
                        </div>
                    </div>`;
            });
            toolsHTML += '</div>';

            // 技能工具
            data.skills.filter(s => s.tools.length > 0).forEach(s => {
                toolsHTML += `<div class="skill-tool-section"><div class="section-label">${escHtml(s.name)} 专属工具</div>`;
                s.tools.forEach(t => {
                    toolsHTML += `
                        <div class="tool-item">
                            <div class="tool-icon">🧩</div>
                            <div class="tool-info">
                                <div class="tool-name">${escHtml(t.name)}</div>
                                <div class="tool-desc">${escHtml(t.description)}</div>
                            </div>
                        </div>`;
                });
                toolsHTML += '</div>';
            });

            toolsPane.innerHTML = toolsHTML || '<p style="color:#999;">没有可用的工具</p>';

        } catch (e) {
            console.error('获取工具和技能列表失败:', e);
            modalSummary.textContent = '加载失败，请检查后端连接';
        }
    }

    // ==================== Shell 命令审批 ====================
    const shellOverlay = document.getElementById('shell-approval-overlay');
    const shellCmdText = document.getElementById('shell-command-text');
    const shellHint = document.getElementById('shell-approval-hint');
    let shellPollTimer = null;

    // 审批按钮点击
    shellOverlay.querySelectorAll('.shell-approval-actions button').forEach(btn => {
        btn.onclick = async () => {
            const action = btn.dataset.action;
            const approvalId = shellOverlay.dataset.approvalId;
            btn.disabled = true;
            try {
                await fetch(API_BASE + '/shell/approve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ approval_id: approvalId, action: action })
                });
            } catch (e) {
                console.error('审批提交失败:', e);
            }
            shellOverlay.style.display = 'none';
            shellOverlay.dataset.approvalId = '';
        };
    });

    // 关闭按钮 (等同于拒绝)
    document.getElementById('shell-approval-close').onclick = () => {
        const approvalId = shellOverlay.dataset.approvalId;
        if (approvalId) {
            fetch(API_BASE + '/shell/approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ approval_id: approvalId, action: 'rejected' })
            }).catch(() => {});
        }
        shellOverlay.style.display = 'none';
        shellOverlay.dataset.approvalId = '';
    };

    // 点击遮罩关闭
    shellOverlay.onclick = (e) => {
        if (e.target === shellOverlay) {
            document.getElementById('shell-approval-close').click();
        }
    };

    // SSE 连接
    function connectShellSSE() {
        const evtSource = new EventSource(API_BASE + '/shell/approval-stream');

        evtSource.onmessage = (event) => {
            try {
                const pending = JSON.parse(event.data);
                handlePendingApprovals(pending);
            } catch (e) {
                // heartbeat
            }
        };

        evtSource.onerror = () => {
            evtSource.close();
            // 5 秒后重连
            setTimeout(connectShellSSE, 5000);
        };
    }

    // 轮询回退 (SSE 不可用时)
    async function pollShellPending() {
        try {
            const resp = await fetch(API_BASE + '/shell/pending');
            const data = await resp.json();
            if (data.status === 'success' && data.pending.length > 0) {
                handlePendingApprovals(data.pending);
            }
        } catch (e) { /* ignore */ }
        shellPollTimer = setTimeout(pollShellPending, 1000);
    }

    function handlePendingApprovals(pending) {
        if (!pending || pending.length === 0) return;
        // 取第一个待审批命令
        const item = pending[0];
        if (shellOverlay.style.display === 'flex' && shellOverlay.dataset.approvalId === item.id) {
            return; // 已经在显示同样的审批
        }
        shellOverlay.dataset.approvalId = item.id;
        shellCmdText.textContent = item.command;
        shellHint.textContent = '请在 60 秒内确认，超时将自动拒绝';
        shellOverlay.style.display = 'flex';
        // 重新启用所有按钮
        shellOverlay.querySelectorAll('.shell-approval-actions button').forEach(b => b.disabled = false);
    }

    // 所有渲染器与常量完成初始化后，再恢复会话并连接实时事件。
    await init();
    window.addEventListener('focus', fetchRuntimeConfig);
    window.addEventListener('pageshow', fetchRuntimeConfig);
    connectShellSSE();
    connectAgentMetricsSSE();
    // 轮询作为回退
    shellPollTimer = setTimeout(pollShellPending, 1500);

    // ==================== 工具函数 ====================
    function escHtml(s) {
        const div = document.createElement('div');
        div.textContent = s || '';
        return div.innerHTML;
    }

    // 终端页面跳转: pywebview 内跳转，浏览器开新窗口
    window.openTerminal = function () {
        if (typeof window.pywebview !== 'undefined') {
            window.location.href = 'terminal.html';
        } else {
            window.open('terminal.html', '_blank');
        }
    };
});
