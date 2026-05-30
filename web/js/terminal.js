document.addEventListener('DOMContentLoaded', () => {
    const output = document.getElementById('term-output');
    const statusEl = document.getElementById('term-status');
    const approvalBar = document.getElementById('shell-approval-bar');
    const shellCmdEl = document.getElementById('shell-bar-cmd');
    let currentApprovalId = null;
    let approvalTimer = null;
    let connectCount = 0;

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
        }
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

    connect();
});
