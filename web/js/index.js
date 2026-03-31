document.addEventListener('DOMContentLoaded', async () => {
    const sendBtn = document.getElementById('send-btn');
    const newChatBtn = document.getElementById('new-chat-btn');
    const userInput = document.getElementById('user-input');
    const chatWindow = document.getElementById('chat-window');
    const sessionListContainer = document.getElementById('session-list');

    let currentSessionId = localStorage.getItem('last_session_id') || "";

    // --- 初始化 ---
    async function init() {
        await fetchSessions();
        if (currentSessionId) {
            await switchSession(currentSessionId);
        }
    }
    await init();

    // --- 1. 获取会话列表 ---
    async function fetchSessions() {
        try {
            const response = await fetch('http://127.0.0.1:8000/sessions');
            const data = await response.json();
            let sessions = data.sessions;

            if (!sessions || sessions.length === 0) {
                sessions = [{ session_id: "", title: "快来开启新对话吧喵~" }];
            }
            renderSessionList(sessions);
        } catch (error) {
            console.error("加载会话列表失败:", error);
        }
    }

    // --- 2. 渲染左侧列表与删除按钮 ---
    function renderSessionList(sessions) {
        sessionListContainer.innerHTML = '';
        sessions.forEach(session => {
            // 🌟 强兼容：不管后端传 session_id 还是 id，都能接住
            const sid = session.session_id || session.id;
            const div = document.createElement('div');

            div.className = `session-item ${sid === currentSessionId ? 'active' : ''}`;

            if (sid) {
                div.innerHTML = `
                    <span class="title">${session.title}</span>
                    <button class="delete-btn" title="删除这个对话喵">✖</button>
                `;
            } else {
                div.innerHTML = `<span class="title">${session.title}</span>`;
            }

            // 🌟 点击事件分离：点按钮是删除，点其它区域是切换
            div.onclick = (e) => {
                if (e.target.classList.contains('delete-btn')) {
                    e.stopPropagation(); // 阻止事件冒泡，防止触发切换
                    deleteSession(sid);
                } else if (sid) {
                    switchSession(sid);
                }
            };
            sessionListContainer.appendChild(div);
        });
    }

    // --- 3. 切换与强制刷新右侧历史 ---
    async function switchSession(sid) {
        if (!sid) return;

        currentSessionId = sid;
        localStorage.setItem('last_session_id', sid);

        // 重新渲染列表以更新 CSS 的 active 高亮
        await fetchSessions();

        // 🌟 强行清空聊天窗口并重新加载
        chatWindow.innerHTML = '';
        await loadHistory(sid);
    }

    // --- 4. 彻底删除逻辑 ---
    async function deleteSession(sid) {
        if (!confirm("确定要彻底删掉这段回忆喵？不可恢复哦！")) return;

        try {
            const response = await fetch(`http://127.0.0.1:8000/session/${sid}`, {
                method: 'DELETE'
            });
            const data = await response.json();

            if (data.status === "success") {
                // 如果删的是当前正在看的对话，让右侧回到出厂状态
                if (sid === currentSessionId) {
                    currentSessionId = "";
                    localStorage.removeItem('last_session_id');
                    chatWindow.innerHTML = '<div class="message ai-msg">对话已删除，快来开启新对话吧喵~</div>';
                }
                await fetchSessions(); // 刷新左侧列表让它消失
            }
        } catch (error) {
            console.error("删除失败喵:", error);
        }
    }

    // --- 5. 从后端拉取历史 ---
    async function loadHistory(sid) {
        if (!sid) return;
        try {
            const response = await fetch(`http://127.0.0.1:8000/history/${sid}`);
            const data = await response.json();
            if (data.status === "success") {
                chatWindow.innerHTML = ''; // 🌟 再次确保清空防重复
                data.history.forEach(msg => renderMessage(msg.content, msg.role));
            }
        } catch (error) {
            console.error("加载历史失败:", error);
        }
    }

    // --- 6. 渲染消息气泡 ---
    function renderMessage(text, className) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${className}`;
        msgDiv.innerText = text;
        chatWindow.appendChild(msgDiv);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    // --- 7. 发送消息 ---
    sendBtn.onclick = sendMessage;
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    async function sendMessage() {
        const text = userInput.value.trim();
        if (!text) return;

        renderMessage(text, 'user-msg');
        userInput.value = '';

        const aiMsgDiv = document.createElement('div');
        aiMsgDiv.className = 'message ai-msg';
        aiMsgDiv.innerText = '思考中喵...';
        chatWindow.appendChild(aiMsgDiv);
        chatWindow.scrollTop = chatWindow.scrollHeight;

        try {
            const response = await fetch('http://127.0.0.1:8000/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: currentSessionId || "new", message: text })
            });

            // 如果是新对话，抓取后端传来的新 ID
            const newSid = response.headers.get('X-Session-Id');
            if (newSid && newSid !== currentSessionId) {
                currentSessionId = newSid;
                localStorage.setItem('last_session_id', newSid);
                fetchSessions(); // 刷新左侧，显示新标题
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            aiMsgDiv.innerText = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                aiMsgDiv.innerText += decoder.decode(value);
                chatWindow.scrollTop = chatWindow.scrollHeight;
            }
        } catch (error) {
            aiMsgDiv.innerText = '呜呜，断线了喵...';
        }
    }

    // --- 8. 新建对话按钮 ---
    newChatBtn.onclick = () => {
        currentSessionId = "";
        localStorage.removeItem('last_session_id');
        chatWindow.innerHTML = '';
        fetchSessions(); // 刷新以清除左侧高亮
        renderMessage('新对话已开启，请发送消息喵！', 'ai-msg');
    };
});