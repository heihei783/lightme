document.addEventListener('DOMContentLoaded', () => {
    const charBtn = document.getElementById('pet-char-btn');
    const outfitBtn = document.getElementById('pet-outfit-btn');
    const closeBtn = document.getElementById('pet-close-btn');
    const chatBtn = document.getElementById('pet-chat-btn');
    const voiceBtn = document.getElementById('pet-voice-btn');
    const chatPanel = document.getElementById('pet-chat-panel');
    const chatInput = document.getElementById('pet-chat-input');
    const sendBtn = document.getElementById('pet-send-btn');
    const msgBox = document.getElementById('live2d-msg-box');
    const status = document.getElementById('pet-status');

    let sessionId = localStorage.getItem('lightme_pet_session_id') || 'new';
    let requestController = null;
    let messageTimer = null;
    let voiceActive = false;
    let voiceTurnRunning = false;
    let queuedVoiceUtterance = '';
    let screenStream = null;
    let screenTrack = null;
    let qtScreenAccessActive = false;
    let lastAssistantSpeech = '';
    let lastAssistantSpeechAt = 0;
    let qtDesktopApi = null;
    let qtVoiceOptions = null;
    let qtVoiceSignalsBound = false;

    function bindQtVoiceSignals() {
        if (!qtDesktopApi || qtVoiceSignalsBound) return;
        qtVoiceSignalsBound = true;
        qtDesktopApi.speech_started?.connect(() => qtVoiceOptions?.onSpeechStart?.());
        qtDesktopApi.speech_interim?.connect((text) => qtVoiceOptions?.onInterim?.(text));
        qtDesktopApi.speech_final?.connect((text) => qtVoiceOptions?.onFinal?.(text));
        qtDesktopApi.speech_state?.connect((state) => qtVoiceOptions?.onStateChange?.(state));
        qtDesktopApi.speech_error?.connect((error) => qtVoiceOptions?.onError?.(error || 'native-error'));
    }

    function connectQtBridge() {
        if (!window.qt?.webChannelTransport) return;
        const connect = () => {
            if (typeof QWebChannel === 'undefined') return;
            new QWebChannel(window.qt.webChannelTransport, (channel) => {
                qtDesktopApi = channel.objects.desktopBridge || null;
                bindQtVoiceSignals();
                if (voiceBtn) voiceBtn.disabled = false;
            });
        };
        if (typeof QWebChannel !== 'undefined') {
            connect();
            return;
        }
        const script = document.createElement('script');
        script.src = 'qrc:///qtwebchannel/qwebchannel.js';
        script.onload = connect;
        document.head.appendChild(script);
    }

    connectQtBridge();
    if (window.qt?.webChannelTransport && voiceBtn) voiceBtn.disabled = true;

    function applyCharacterLayout(character) {
        const name = String(character?.name || '22娘');
        document.body.dataset.character = name;
        if (charBtn) charBtn.title = `切换角色（当前：${name}）`;
        if (outfitBtn) {
            const outfitCount = Number(character?.outfitCount || 1);
            outfitBtn.disabled = outfitCount <= 1;
            outfitBtn.title = outfitCount > 1
                ? `切换服装（${Number(character?.outfitIndex || 0) + 1}/${outfitCount}）`
                : `${name} 暂无其他服装`;
        }
    }

    window.addEventListener('lightme:character-change', (event) => {
        applyCharacterLayout(event.detail);
    });
    applyCharacterLayout({ name: '22娘' });

    if (typeof Live2DCtrl !== 'undefined') {
        Live2DCtrl.init();
        Live2DCtrl.showMsg('桌面宠物已启动');
    }

    function nativeApi() {
        return qtDesktopApi || window.pywebview?.api || null;
    }

    function callQt(methodName, ...args) {
        return new Promise((resolve, reject) => {
            const method = qtDesktopApi?.[methodName];
            if (typeof method !== 'function') {
                reject(new Error(`Qt bridge method unavailable: ${methodName}`));
                return;
            }
            try {
                method(...args, (result) => resolve(result));
            } catch (error) {
                reject(error);
            }
        });
    }

    function startNativeDrag(event) {
        if (event?.button !== undefined && event.button !== 0) return;
        event?.preventDefault();
        const api = nativeApi();
        if (api?.start_drag) {
            const result = api.start_drag();
            if (result?.catch) result.catch(() => {});
        }
    }

    // Any non-control area—including the whole character—acts as a native
    // drag surface. Buttons and the text input remain independently clickable.
    document.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        if (event.target.closest('button, input, form, .pet-toolbar, .pet-action-dock, .pet-chat-panel')) return;
        startNativeDrag(event);
    }, true);

    function setStatus(text) {
        if (status) status.textContent = text || '随时陪着你';
    }

    function showMessage(text, timeout = 7000) {
        if (!msgBox) return;
        msgBox.textContent = String(text || '').trim();
        msgBox.classList.toggle('show', Boolean(msgBox.textContent));
        clearTimeout(messageTimer);
        if (msgBox.textContent && timeout > 0) {
            messageTimer = setTimeout(() => msgBox.classList.remove('show'), timeout);
        }
    }

    function setChatOpen(open) {
        chatPanel.classList.toggle('open', open);
        chatPanel.setAttribute('aria-hidden', String(!open));
        chatBtn.classList.toggle('active', open);
        chatBtn.setAttribute('aria-expanded', String(open));
        if (open) setTimeout(() => chatInput.focus(), 60);
    }

    function normalizeSpeech(text) {
        return String(text || '')
            .toLowerCase()
            .replace(/[\s，。！？、,.!?；;：:“”"'（）()\-—…]/g, '');
    }

    function isLikelyEcho(text) {
        if (Date.now() - lastAssistantSpeechAt > 9000) return false;
        const heard = normalizeSpeech(text);
        const spoken = normalizeSpeech(lastAssistantSpeech);
        if (!heard || !spoken || heard.length < 5) return false;
        return spoken.includes(heard) || heard.includes(spoken.slice(0, Math.min(spoken.length, 24)));
    }

    function updateVoiceButton(state = 'idle') {
        const listening = voiceActive && ['starting', 'listening', 'speech', 'reconnecting'].includes(state);
        voiceBtn.classList.toggle('active', voiceActive);
        voiceBtn.classList.toggle('listening', listening);
        voiceBtn.classList.toggle('capturing', voiceActive && state === 'capturing');
        voiceBtn.setAttribute('aria-pressed', String(voiceActive));
        voiceBtn.title = voiceActive ? '结束陪伴模式' : '开启陪伴模式（持续语音与按需看屏幕）';
    }

    async function playReply(text) {
        const spokenText = Array.from(String(text || '')).slice(0, 220).join('');
        if (!spokenText || !voiceActive) return;
        try {
            setStatus('正在准备语音…');
            const response = await fetch(API_BASE + '/tts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: spokenText }),
            });
            const data = await response.json();
            if (!response.ok || data.status !== 'success' || !data.audio) {
                throw new Error(data.msg || '语音生成失败');
            }
            if (!voiceActive) return;
            lastAssistantSpeech = spokenText;
            lastAssistantSpeechAt = Date.now();
            const playback = await Live2DCtrl.playVoiceWithLipSync(data.audio);
            setStatus('LightMe 正在回应，可直接打断');
            if (playback?.ended) await playback.ended;
        } catch (error) {
            console.warn('桌宠语音播放失败:', error);
        } finally {
            if (voiceActive) setStatus('正在聆听，随时可以说话');
        }
    }

    async function readStreamingText(response) {
        if (!response.ok) throw new Error(`聊天请求失败: HTTP ${response.status}`);
        if (!response.body) return response.text();
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let text = '';
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            text += decoder.decode(value, { stream: true });
            if (text) showMessage(text.slice(-160), 0);
        }
        text += decoder.decode();
        return text.trim();
    }

    async function sendMessage(rawText, { image = null } = {}) {
        const text = String(rawText || '').trim();
        if (!text) return '';
        requestController?.abort();
        const controller = new AbortController();
        requestController = controller;
        sendBtn.disabled = true;
        chatInput.value = '';
        setChatOpen(false);
        showMessage(`你：${text}`, 2200);
        setStatus('LightMe 正在想…');
        try {
            const payload = { session_id: sessionId, message: text };
            if (image) payload.image = image;
            const response = await fetch(API_BASE + '/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal: controller.signal,
            });
            const newSessionId = response.headers.get('X-Session-Id');
            if (newSessionId) {
                sessionId = newSessionId;
                localStorage.setItem('lightme_pet_session_id', newSessionId);
            }
            const reply = await readStreamingText(response);
            if (!reply) throw new Error('没有收到回答');
            showMessage(reply, 9000);
            await playReply(reply);
            return reply;
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('桌宠对话失败:', error);
                showMessage('暂时没有连接上，稍后再和我说一次吧。');
            }
            return '';
        } finally {
            if (requestController === controller) requestController = null;
            sendBtn.disabled = false;
            setStatus(voiceActive ? '正在聆听，随时可以说话' : '随时陪着你');
        }
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
            console.warn('屏幕意图判断失败，使用本地判断:', error);
            return fallbackNeedsScreen(text);
        } finally {
            clearTimeout(timeout);
        }
    }

    async function captureCurrentScreenFrame(track) {
        if (qtScreenAccessActive && qtDesktopApi?.capture_screen) {
            const image = await callQt('capture_screen');
            if (!image) throw new Error('Qt 原生屏幕截图失败');
            return image;
        }
        if (!track || track.readyState === 'ended') throw new Error('屏幕通道已结束');
        let source;
        let sourceWidth;
        let sourceHeight;
        let fallbackVideo = null;

        if (typeof ImageCapture !== 'undefined') {
            source = await new ImageCapture(track).grabFrame();
            sourceWidth = source.width;
            sourceHeight = source.height;
        } else {
            fallbackVideo = document.createElement('video');
            fallbackVideo.muted = true;
            fallbackVideo.playsInline = true;
            fallbackVideo.srcObject = screenStream;
            await fallbackVideo.play();
            if (!fallbackVideo.videoWidth || !fallbackVideo.videoHeight) {
                await new Promise((resolve) => fallbackVideo.addEventListener('loadedmetadata', resolve, { once: true }));
            }
            source = fallbackVideo;
            sourceWidth = fallbackVideo.videoWidth;
            sourceHeight = fallbackVideo.videoHeight;
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
        if (fallbackVideo) {
            fallbackVideo.pause();
            fallbackVideo.srcObject = null;
        }
        return canvas.toDataURL('image/jpeg', 0.68).split(',')[1];
    }

    async function processVoiceUtterance(text) {
        const utterance = String(text || '').trim();
        if (!utterance || !voiceActive) return;
        if (isLikelyEcho(utterance)) {
            setStatus('已过滤扬声器回声，继续说就好');
            return;
        }
        if (voiceTurnRunning) {
            queuedVoiceUtterance = queuedVoiceUtterance
                ? `${queuedVoiceUtterance} ${utterance}`
                : utterance;
            setStatus('我听到了，马上回应');
            return;
        }

        voiceTurnRunning = true;
        let currentUtterance = utterance;
        try {
            while (currentUtterance && voiceActive) {
                queuedVoiceUtterance = '';
                updateVoiceButton('thinking');
                setStatus('正在判断是否需要看屏幕…');
                const needsScreen = await decideWhetherToReadScreen(currentUtterance);
                if (!voiceActive) break;

                let image = null;
                if (needsScreen && (qtScreenAccessActive || screenTrack?.readyState === 'live')) {
                    updateVoiceButton('capturing');
                    setStatus('正在读取当前屏幕的一帧…');
                    try {
                        image = await captureCurrentScreenFrame(screenTrack);
                    } catch (error) {
                        console.warn('桌宠按需截屏失败:', error);
                    }
                }

                updateVoiceButton('thinking');
                setStatus(image ? '已经看到了，正在想…' : 'LightMe 正在想…');
                await sendMessage(currentUtterance, { image });
                currentUtterance = queuedVoiceUtterance.trim();
            }
        } finally {
            voiceTurnRunning = false;
            if (voiceActive) {
                updateVoiceButton('listening');
                setStatus('正在聆听，随时可以说话');
            }
        }
    }

    function stopVoiceCompanion({ message = '陪伴模式已暂停' } = {}) {
        const wasActive = voiceActive;
        voiceActive = false;
        voiceTurnRunning = false;
        queuedVoiceUtterance = '';
        requestController?.abort();
        if (qtDesktopApi?.stop_voice_input) qtDesktopApi.stop_voice_input();
        else Live2DCtrl.stopVoiceInput?.();
        qtVoiceOptions = null;
        Live2DCtrl.stopVoice?.('voice-companion-stopped');
        screenStream?.getTracks().forEach((track) => {
            if (track.readyState !== 'ended') track.stop();
        });
        screenStream = null;
        screenTrack = null;
        if (qtScreenAccessActive && qtDesktopApi?.stop_screen_access) {
            qtDesktopApi.stop_screen_access();
        }
        qtScreenAccessActive = false;
        updateVoiceButton('idle');
        if (wasActive) setStatus(message);
    }

    async function startVoiceCompanion() {
        const usesQtVoice = Boolean(qtDesktopApi?.start_voice_input);
        if (!usesQtVoice && !window.SpeechRecognition && !window.webkitSpeechRecognition) {
            showMessage('当前桌面内核不支持持续语音识别，请更新 Edge WebView2。');
            return;
        }
        if (usesQtVoice && qtDesktopApi?.ensure_speech_permission) {
            const permissionReady = await callQt('ensure_speech_permission');
            if (!permissionReady) {
                showMessage('请在 Windows 设置中打开“在线语音识别”，然后关闭并重新启动桌宠。', 10000);
                setStatus('等待开启系统语音权限');
                return;
            }
        }
        const usesQtCapture = Boolean(qtDesktopApi?.request_screen_access);
        if (!usesQtCapture && !navigator.mediaDevices?.getDisplayMedia) {
            showMessage('当前桌面内核不支持屏幕共享，无法开启完整陪伴模式。');
            return;
        }

        voiceBtn.disabled = true;
        setStatus('请选择要共享的屏幕或窗口…');
        try {
            let track = null;
            if (usesQtCapture) {
                const allowed = await callQt('request_screen_access');
                if (!allowed) throw new Error('未选择共享屏幕');
                qtScreenAccessActive = true;
            } else {
                const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
                track = stream.getVideoTracks()[0];
                if (!track) throw new Error('没有可用的屏幕画面');
                screenStream = stream;
                screenTrack = track;
            }
            voiceActive = true;
            updateVoiceButton('starting');
            setStatus('屏幕已连接，正在开启麦克风…');
            Live2DCtrl.unlockAudio?.().catch(() => {});

            const voiceOptions = {
                mode: 'immersive',
                onSpeechStart: () => {
                    if (Live2DCtrl.isVoicePlaying?.()) Live2DCtrl.stopVoice('barge-in');
                    requestController?.abort();
                    updateVoiceButton('speech');
                    setStatus('我在听');
                },
                onInterim: (text) => setStatus(text || '我在听'),
                onFinal: (text) => {
                    showMessage(`听到了：${text}`, 1800);
                    processVoiceUtterance(text);
                },
                onStateChange: (state) => {
                    if (!voiceActive || voiceTurnRunning) return;
                    updateVoiceButton(state);
                    if (state === 'listening' || state === 'reconnecting') {
                        setStatus('正在聆听，随时可以说话');
                    } else if (state === 'processing') {
                        setStatus('正在理解你的话…');
                    }
                },
                onError: (error) => {
                    if (error === 'speech-privacy-not-accepted') {
                        showMessage('请在刚打开的 Windows 设置中开启“在线语音识别”，然后重新点击“语音”。', 10000);
                        stopVoiceCompanion({ message: '等待开启系统语音权限' });
                    } else if (error === 'microphone-access-denied') {
                        showMessage('请允许桌面应用访问麦克风，然后重新点击“语音”。', 10000);
                        stopVoiceCompanion({ message: '等待开启麦克风权限' });
                    } else if (error === 'not-allowed' || error === 'service-not-allowed' || /denied|权限|access/i.test(error)) {
                        showMessage('陪伴模式需要麦克风权限，请允许后重新开启。');
                        stopVoiceCompanion({ message: '麦克风权限未开启' });
                    } else if (error === 'unsupported') {
                        showMessage('当前桌面内核不支持语音识别。');
                        stopVoiceCompanion();
                    } else {
                        console.error('原生语音识别失败:', error);
                        showMessage(`语音识别启动失败：${error}`);
                        stopVoiceCompanion({ message: '语音识别未启动' });
                    }
                },
            };
            let started;
            if (usesQtVoice) {
                qtVoiceOptions = voiceOptions;
                started = await callQt('start_voice_input');
            } else {
                started = Live2DCtrl.startVoiceInput(voiceOptions);
            }
            if (!started) throw new Error('语音识别启动失败');

            if (track) {
                track.addEventListener('ended', () => {
                    if (voiceActive) {
                        stopVoiceCompanion({ message: '屏幕共享已结束，陪伴模式已暂停' });
                        showMessage('屏幕共享已结束；再次点击“语音”即可重新开启陪伴。');
                    }
                });
            }
            updateVoiceButton('listening');
            setStatus('正在聆听 · 需要时才读取一帧屏幕');
            showMessage('陪伴模式已开启：可以一直说话、随时打断；只有需要时才会读取当前屏幕的一帧。', 8500);
        } catch (error) {
            console.error('桌宠陪伴模式启动失败:', error);
            stopVoiceCompanion({ message: '陪伴模式未开启' });
            showMessage('开启陪伴模式需要屏幕共享和麦克风权限，请在系统提示中允许。');
        } finally {
            voiceBtn.disabled = false;
        }
    }

    if (charBtn && typeof Live2DCtrl !== 'undefined') {
        charBtn.onclick = () => Live2DCtrl.nextCharacter();
    }
    if (outfitBtn && typeof Live2DCtrl !== 'undefined') {
        outfitBtn.onclick = () => Live2DCtrl.nextOutfit();
    }
    chatBtn.onclick = () => setChatOpen(!chatPanel.classList.contains('open'));
    chatPanel.addEventListener('submit', (event) => {
        event.preventDefault();
        sendMessage(chatInput.value);
    });
    voiceBtn.onclick = () => {
        if (voiceActive) stopVoiceCompanion();
        else startVoiceCompanion();
    };

    if (closeBtn) {
        closeBtn.onclick = () => {
            stopVoiceCompanion({ message: '' });
            const api = nativeApi();
            if (api?.close_window) {
                const result = api.close_window();
                if (result?.catch) result.catch(() => window.close());
            } else window.close();
        };
    }

    window.addEventListener('beforeunload', () => {
        voiceActive = false;
        if (qtDesktopApi?.stop_voice_input) qtDesktopApi.stop_voice_input();
        else Live2DCtrl.stopVoiceInput?.();
        qtVoiceOptions = null;
        Live2DCtrl.stopVoice?.('window-closing');
        screenStream?.getTracks().forEach((track) => track.stop());
        requestController?.abort();
    });
});
