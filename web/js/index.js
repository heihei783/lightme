document.addEventListener('DOMContentLoaded', async () => {
    const sendBtn = document.getElementById('send-btn');
    const newChatBtn = document.getElementById('new-chat-btn');
    const userInput = document.getElementById('user-input');
    const chatWindow = document.getElementById('chat-window');
    const sessionListContainer = document.getElementById('session-list');
    const avatarFileInput = document.getElementById('avatar-file-input');

    let currentSessionId = localStorage.getItem('last_session_id') || '';
    let ttsMuted = localStorage.getItem('tts_muted') === 'true';
    // 当前正在上传的头像类型: 'user' | 'ai'
    let avatarUploadTarget = 'user';
    // 陪伴模式
    let companionTimer = null;
    let companionInterval = 10; // 默认10秒，后续从配置读取
    let companionActive = false;
    let imageGenProbability = 0.08;

    // ==================== 头像系统 ====================
    function getUserAvatarUrl() {
        const filename = localStorage.getItem('avatar_filename');
        return filename ? API_BASE + '/avatar/' + filename : null;
    }

    function getAiAvatarUrl() {
        const filename = localStorage.getItem('ai_avatar_filename');
        return filename ? API_BASE + '/avatar/' + filename : null;
    }

    // 点击用户头像 → 上传用户头像
    function onUserAvatarClick() {
        avatarUploadTarget = 'user';
        avatarFileInput.click();
    }

    // 点击 AI 头像 → 上传 AI 头像
    function onAiAvatarClick() {
        avatarUploadTarget = 'ai';
        avatarFileInput.click();
    }

    // 头像上传处理
    avatarFileInput.onchange = async () => {
        const file = avatarFileInput.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        try {
            const resp = await fetch(API_BASE + '/avatar/upload', {
                method: 'POST',
                body: formData
            });
            const data = await resp.json();
            if (data.status === 'success') {
                const key = avatarUploadTarget === 'ai' ? 'ai_avatar_filename' : 'avatar_filename';
                localStorage.setItem(key, data.filename);
                // 刷新当前聊天窗口中的头像
                refreshAllAvatars();
            } else {
                alert('上传失败: ' + (data.msg || '未知错误'));
            }
        } catch (e) {
            console.error('头像上传失败:', e);
        }
        avatarFileInput.value = '';
    };

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
        await fetchSessions();
        await fetchCompanionInterval();
        if (currentSessionId) {
            await switchSession(currentSessionId);
        }
        if (typeof Live2DCtrl !== 'undefined') {
            Live2DCtrl.init();
        }
        initTTSControls();
    }
    await init();

    // ==================== 陪伴间隔配置 ====================
    async function fetchCompanionInterval() {
        try {
            const resp = await fetch(API_BASE + '/config');
            const data = await resp.json();
            if (data.status === 'success') {
                if (data.config.companion_interval) companionInterval = data.config.companion_interval;
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
        currentSessionId = sid;
        localStorage.setItem('last_session_id', sid);
        await fetchSessions();
        chatWindow.innerHTML = '';
        await loadHistory(sid);
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
        img.title = '点击更换头像';
        img.onclick = onUserAvatarClick;
        return img;
    }

    function buildAiAvatarImg(url) {
        const img = document.createElement('img');
        img.className = 'msg-avatar msg-avatar-ai';
        img.src = url;
        img.title = '点击更换AI头像';
        img.onclick = onAiAvatarClick;
        return img;
    }

    function buildUserAvatarHTML() {
        const url = getUserAvatarUrl();
        if (url) {
            return `<img class="msg-avatar msg-avatar-user" src="${url}" alt="我" title="点击更换头像">`;
        }
        return `<div class="msg-avatar-placeholder avatar-placeholder-user" style="background:#ffe0e6;color:#ff7675;" title="点击上传头像">🐱</div>`;
    }

    function buildAiAvatarHTML() {
        const url = getAiAvatarUrl();
        if (url) {
            return `<img class="msg-avatar msg-avatar-ai" src="${url}" alt="AI" title="点击更换AI头像">`;
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
            `<div class="message ai-msg" style="display:flex;align-items:center;justify-content:center;min-height:28px;">
                <div class="thinking-spinner"></div>
            </div>`;
        bindAvatarClicks(div);
        return div;
    }

    function showThinking() {
        const row = buildThinkingRow();
        chatWindow.appendChild(row);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    // 将思考行原地转为正式消息气泡（保留 spinner 继续转），返回气泡 DOM
    // 调用方拿到第一块文本后自行替换内容
    function claimThinkingRow() {
        const row = document.getElementById('thinking-row');
        if (!row) return null;
        row.id = '';
        row.classList.remove('thinking-row');
        return row.querySelector('.message');
    }

    function hideThinking() {
        const row = document.getElementById('thinking-row');
        if (row) row.remove();
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
    sendBtn.onclick = sendMessage;
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    async function sendMessage() {
        const text = userInput.value.trim();
        const hasImage = !!selectedImageB64;
        if (!text && !hasImage) return;

        if (hasImage) {
            renderUserMessage(text || '看看这张图片', selectedImageB64);
        } else {
            renderUserMessage(text);
        }
        userInput.value = '';
        const imgToSend = selectedImageB64;
        selectedImageB64 = null;
        imagePreviewRow.style.display = 'none';

        showThinking();

        try {
            const response = await fetch(API_BASE + '/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: currentSessionId || 'new',
                    message: text || '请描述这张图片',
                    image: imgToSend || undefined
                })
            });

            const newSid = response.headers.get('X-Session-Id');
            if (newSid && newSid !== currentSessionId) {
                currentSessionId = newSid;
                localStorage.setItem('last_session_id', newSid);
                fetchSessions();
            }

            // 认领思考行（spinner 继续转，直到第一块文本到达才被替换）
            let aiBubble = claimThinkingRow();
            if (!aiBubble) {
                hideThinking();
                const aiRow = document.createElement('div');
                aiRow.className = 'msg-row ai-row';
                aiRow.innerHTML = buildAiAvatarHTML() + '<div class="message ai-msg"></div>';
                bindAvatarClicks(aiRow);
                chatWindow.appendChild(aiRow);
                aiBubble = aiRow.querySelector('.message');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullText = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                fullText += decoder.decode(value);
                aiBubble.textContent = fullText;
                chatWindow.scrollTop = chatWindow.scrollHeight;
            }

            if (!fullText) {
                aiBubble.textContent = '呜呜，断线了喵...';
            }

            if (fullText && !ttsMuted && typeof Live2DCtrl !== 'undefined') {
                speakText(fullText);
            }

            // 8% 概率触发生图
            if (Math.random() < imageGenProbability && text) {
                try {
                    const imgResp = await fetch(API_BASE + '/image-gen', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ prompt: text.slice(0, 300) })
                    });
                    const imgData = await imgResp.json();
                    if (imgData.status === 'success' && imgData.image) {
                        renderGeneratedImage(imgData.image, text.slice(0, 50));
                    }
                } catch (e) { /* 静默失败 */ }
            }

        } catch (error) {
            hideThinking();
            renderAiMessage('呜呜，断线了喵...');
        }
    }

    function renderGeneratedImage(base64data, caption) {
        const row = document.createElement('div');
        row.className = 'msg-row ai-row';
        const img = document.createElement('img');
        img.src = 'data:image/png;base64,' + base64data;
        img.style.cssText = 'max-width:200px;border-radius:12px;display:block;';
        const cap = document.createElement('span');
        cap.style.cssText = 'font-size:11px;color:#999;margin-top:4px;display:block;';
        cap.textContent = '🎨 ' + (caption || 'AI 生成');
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

    // ==================== TTS 语音合成 ====================
    async function speakText(text) {
        try {
            const v = getTTSVoice();
            const resp = await fetch(API_BASE + '/tts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text.slice(0, 200), voice: v.voice, provider: v.provider })
            });
            const data = await resp.json();
            if (data.status === 'success' && data.audio && typeof Live2DCtrl !== 'undefined') {
                Live2DCtrl.playVoiceWithLipSync(data.audio);
                Live2DCtrl.showMsg(text.slice(0, 100));
            }
        } catch (e) {
            console.error('TTS 失败:', e);
        }
    }

    // ==================== 陪伴按钮 ====================
    const companionBtn = document.getElementById('companion-btn');

    // 截屏 → 压缩 → 发给 AI，复用的核心函数
    async function captureAndSend(track) {
        const imageCapture = new ImageCapture(track);
        const bitmap = await imageCapture.grabFrame();

        const canvas = document.createElement('canvas');
        let w = bitmap.width, h = bitmap.height;
        const max = 1024;
        if (w > max || h > max) {
            if (w > h) { h *= max / w; w = max; }
            else { w *= max / h; h = max; }
        }
        canvas.width = w; canvas.height = h;
        canvas.getContext('2d').drawImage(bitmap, 0, 0, w, h);
        const imageB64 = canvas.toDataURL('image/jpeg', 0.6).split(',')[1];

        showThinking();

        const response = await fetch(API_BASE + '/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: currentSessionId || 'new',
                message: '你是一只陪伴主人的猫娘。主人正在看着屏幕。请描述你看到了什么内容，并用可爱关心的语气与主人互动。如果看到代码或文字，可以给出建议或鼓励。',
                image: imageB64
            })
        });

        let aiBubble = claimThinkingRow();
        if (!aiBubble) {
            hideThinking();
            const aiRow = document.createElement('div');
            aiRow.className = 'msg-row ai-row';
            aiRow.innerHTML = buildAiAvatarHTML() + '<div class="message ai-msg"></div>';
            bindAvatarClicks(aiRow);
            chatWindow.appendChild(aiRow);
            aiBubble = aiRow.querySelector('.message');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            fullText += decoder.decode(value);
            aiBubble.textContent = fullText;
            chatWindow.scrollTop = chatWindow.scrollHeight;
        }

        if (fullText && !ttsMuted && typeof Live2DCtrl !== 'undefined') {
            speakText(fullText);
        }
    }

    async function startCompanion() {
        try {
            const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
            const track = stream.getVideoTracks()[0];

            companionActive = true;
            companionBtn.textContent = '陪伴中 ⏸';
            companionBtn.style.background = '#ff7675';
            renderAiMessage(`陪伴模式已开启，每 ${companionInterval} 秒自动截屏分析喵~`);

            // 首次立即截屏
            await captureAndSend(track);

            // 定时循环
            companionTimer = setInterval(async () => {
                if (track.readyState === 'ended') {
                    stopCompanion();
                    return;
                }
                try {
                    await captureAndSend(track);
                } catch (e) {
                    console.error('陪伴截屏失败:', e);
                }
            }, companionInterval * 1000);

            // 用户关闭共享时自动停止
            track.addEventListener('ended', () => stopCompanion());
        } catch (e) {
            console.error('屏幕捕获失败:', e);
            hideThinking();
            renderAiMessage('陪伴模式需要屏幕捕获权限喵~请在浏览器弹窗中允许。');
        }
    }

    function stopCompanion() {
        companionActive = false;
        if (companionTimer) { clearInterval(companionTimer); companionTimer = null; }
        companionBtn.textContent = '陪伴 👀';
        companionBtn.style.background = '';
        if (!document.getElementById('thinking-row')) {
            renderAiMessage('陪伴模式已结束喵~');
        }
    }

    companionBtn.onclick = () => {
        if (companionActive) {
            stopCompanion();
        } else {
            startCompanion();
        }
    };

    // ==================== 新建对话 ====================
    newChatBtn.onclick = () => {
        currentSessionId = '';
        localStorage.removeItem('last_session_id');
        chatWindow.innerHTML = '';
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

    // ==================== 工具函数 ====================
    function escHtml(s) {
        const div = document.createElement('div');
        div.textContent = s || '';
        return div.innerHTML;
    }
});
