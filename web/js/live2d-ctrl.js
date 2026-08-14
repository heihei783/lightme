// live2d-ctrl.js - Live2D 控制器 for LightMe
// 改编自 live2d/index.js，整合口型同步、鼠标追踪、点击交互、ASR

const Live2DCtrl = (() => {
    const M = API_BASE + '/live2d-models/';
    const roleData = [
        {
            name: "22娘",
            path: M + "22/",
            outfits: [
                "model.default.json",
                "model.2016.xmas.1.json", "model.2016.xmas.2.json",
                "model.2017.cba-normal.json", "model.2017.cba-super.json",
                "model.2017.newyear.json", "model.2017.school.json",
                "model.2017.summer.normal.1.json", "model.2017.summer.normal.2.json",
                "model.2017.summer.super.1.json", "model.2017.summer.super.2.json",
                "model.2017.tomo-bukatsu.high.json", "model.2017.tomo-bukatsu.low.json",
                "model.2017.valley.json", "model.2017.vdays.json",
                "model.2018.bls-summer.json", "model.2018.bls-winter.json",
                "model.2018.lover.json", "model.2018.spring.json"
            ]
        },
        {
            name: "33娘",
            path: M + "33/",
            outfits: [
                "model.default.json",
                "model.2016.xmas.1.json", "model.2016.xmas.2.json",
                "model.2017.cba-normal.json", "model.2017.cba-super.json",
                "model.2017.newyear.json", "model.2017.school.json",
                "model.2017.summer.normal.1.json", "model.2017.summer.normal.2.json",
                "model.2017.summer.super.1.json", "model.2017.summer.super.2.json",
                "model.2017.tomo-bukatsu.high.json", "model.2017.tomo-bukatsu.low.json",
                "model.2017.valley.json", "model.2017.vdays.json",
                "model.2018.bls-summer.json", "model.2018.bls-winter.json",
                "model.2018.lover.json", "model.2018.spring.json"
            ]
        },
        { name: "康娜", path: M + "Kobayaxi/", outfits: ["Kobayaxi.model.json"] },
        { name: "血小板", path: M + "platelet/", outfits: ["model.json"] },
        { name: "纱雾", path: M + "sagiri/", outfits: ["sagiri.model.json"] },
        { name: "小埋", path: M + "xiaomai/", outfits: ["xiaomai.model.json"] }
    ];

    const outfitLabels = {
        "model.default.json": "默认",
        "model.2016.xmas.1.json": "圣诞2016①", "model.2016.xmas.2.json": "圣诞2016②",
        "model.2017.cba-normal.json": "CBA普通", "model.2017.cba-super.json": "CBA超级",
        "model.2017.newyear.json": "新年2017", "model.2017.school.json": "校园",
        "model.2017.summer.normal.1.json": "夏日普通①", "model.2017.summer.normal.2.json": "夏日普通②",
        "model.2017.summer.super.1.json": "夏日超级①", "model.2017.summer.super.2.json": "夏日超级②",
        "model.2017.tomo-bukatsu.high.json": "社团高", "model.2017.tomo-bukatsu.low.json": "社团低",
        "model.2017.valley.json": "情人节2017", "model.2017.vdays.json": "Vdays",
        "model.2018.bls-summer.json": "BLS夏日", "model.2018.bls-winter.json": "BLS冬日",
        "model.2018.lover.json": "情人节2018", "model.2018.spring.json": "春季2018"
    };

    const touchTexts = ["哎呀，别摸我啦！", "讨厌！", "哼~~", "再戳我就生气了哦！", "是在检查身体吗？", "哇！吓我一跳！", "你好呀主人~"];

    let charIdx = 0;
    let outfitIdx = 0;
    let currentModel = null;
    let msgTimer = null;
    let audioContext = null;
    let analyser = null;
    let currentVoiceSource = null;
    let currentVoiceEndResolver = null;
    let lipSyncInterval = null;
    let hookInterval = null;
    let globalLipSyncValue = 0;
    let speechRecognition = null;
    let voiceInputWanted = false;
    let voiceInputRunning = false;
    let voiceInputOptions = null;
    let voiceInputRestartTimer = null;

    function injectLive2DHook() {
        if (hookInterval) clearInterval(hookInterval);
        hookInterval = setInterval(() => {
            if (typeof window.Live2DModelWebGL !== 'undefined') {
                if (!window.Live2DModelWebGL.prototype._hooked) {
                    const originalUpdate = window.Live2DModelWebGL.prototype.update;
                    window.Live2DModelWebGL.prototype.update = function() {
                        currentModel = this;
                        if (originalUpdate) originalUpdate.apply(this, arguments);
                        if (globalLipSyncValue > 0.01) {
                            this.setParamFloat("PARAM_MOUTH_OPEN_Y", globalLipSyncValue);
                            this.setParamFloat("PARAM_MOUTH_OPEN", globalLipSyncValue);
                        }
                    };
                    window.Live2DModelWebGL.prototype._hooked = true;
                    clearInterval(hookInterval);
                }
            }
        }, 100);
    }

    function loadModel() {
        const canvas = document.getElementById('live2d-canvas');
        if (!canvas) return;
        canvas.width = parseInt(canvas.dataset.modelWidth || '600', 10);
        canvas.height = parseInt(canvas.dataset.modelHeight || '1000', 10);

        const role = roleData[charIdx];
        const modelPath = role.path + (role.outfits[outfitIdx] || role.outfits[0]);

        // Compact surfaces can fit each model independently instead of
        // forcing every character into the same oversized crop.
        window.dispatchEvent(new CustomEvent('lightme:character-change', {
            detail: {
                index: charIdx,
                name: role.name,
                outfitIndex: outfitIdx,
                outfitCount: role.outfits.length,
                modelPath,
            },
        }));

        if (window.loadlive2d) {
            currentModel = null;
            globalLipSyncValue = 0;
            injectLive2DHook();
            loadlive2d("live2d-canvas", modelPath);
        }
    }

    function showMsg(text) {
        const box = document.getElementById('live2d-msg-box');
        if (!box) return;
        box.innerHTML = text;
        box.classList.add('show');
        if (msgTimer) clearTimeout(msgTimer);
        msgTimer = setTimeout(() => box.classList.remove('show'), 5000);
    }

    function unlockAudio() {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return Promise.reject(new Error('当前浏览器不支持 Web Audio'));
        if (!audioContext) audioContext = new AudioContext();
        if (audioContext.state === 'suspended') return audioContext.resume();
        return Promise.resolve();
    }

    function clearLipSync() {
        if (lipSyncInterval) clearInterval(lipSyncInterval);
        lipSyncInterval = null;
        globalLipSyncValue = 0;
    }

    function emitVoiceEvent(type, detail = {}) {
        window.dispatchEvent(new CustomEvent(`lightme:${type}`, { detail }));
    }

    function finishVoice(reason, source) {
        if (source && currentVoiceSource && currentVoiceSource !== source) return;
        currentVoiceSource = null;
        clearLipSync();
        const resolveEnded = currentVoiceEndResolver;
        currentVoiceEndResolver = null;
        if (resolveEnded) resolveEnded({ reason, interrupted: reason !== 'ended' });
        emitVoiceEvent('voice-end', { reason, interrupted: reason !== 'ended' });
    }

    function stopVoice(reason = 'interrupted') {
        const source = currentVoiceSource;
        if (!source) return false;
        source.onended = null;
        currentVoiceSource = null;
        try { source.stop(); } catch (e) { /* 已经结束 */ }
        finishVoice(reason);
        return true;
    }

    function isVoicePlaying() {
        return Boolean(currentVoiceSource);
    }

    async function playVoiceWithLipSync(base64Audio) {
        if (!base64Audio) throw new Error('缺少语音数据');

        await unlockAudio();

        const audioData = atob(base64Audio);
        const arrayBuffer = new ArrayBuffer(audioData.length);
        const view = new Uint8Array(arrayBuffer);
        for (let i = 0; i < audioData.length; i++) view[i] = audioData.charCodeAt(i);

        const buffer = await new Promise((resolve, reject) => {
            audioContext.decodeAudioData(arrayBuffer, resolve, () => reject(new Error('音频解码失败')));
        });
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        if (!analyser) {
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            analyser.connect(audioContext.destination);
        }
        source.connect(analyser);

        const previousSource = currentVoiceSource;
        currentVoiceSource = source;
        if (previousSource) {
            currentVoiceSource = previousSource;
            stopVoice('replaced');
            currentVoiceSource = source;
        }

        if (lipSyncInterval) clearInterval(lipSyncInterval);
        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        lipSyncInterval = setInterval(() => {
            analyser.getByteFrequencyData(dataArray);
            let sum = 0;
            const binCount = Math.floor(dataArray.length / 2);
            for (let i = 0; i < binCount; i++) sum += dataArray[i];
            const volume = sum / binCount;
            const targetValue = Math.min(1, Math.max(0, volume > 5 ? volume / 50 : 0));
            globalLipSyncValue = globalLipSyncValue * 0.3 + targetValue * 0.7;
        }, 20);

        let resolveEnded;
        const ended = new Promise((resolve) => { resolveEnded = resolve; });
        currentVoiceEndResolver = resolveEnded;
        source.onended = () => finishVoice('ended', source);
        source.start(0);
        emitVoiceEvent('voice-start', { duration: buffer.duration });

        return {
            duration: buffer.duration,
            ended,
            stop: (reason = 'interrupted') => {
                if (currentVoiceSource === source) stopVoice(reason);
            },
        };
    }

    function initMouseTracking() {
        document.addEventListener('mousemove', (e) => {
            if (!currentModel) return;
            const canvas = document.getElementById('live2d-canvas');
            if (!canvas) return;
            const rect = canvas.getBoundingClientRect();
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            let dx = (e.clientX - cx) / (rect.width / 2);
            let dy = -(e.clientY - cy) / (rect.height / 2);
            dx = Math.max(-1.5, Math.min(1.5, dx));
            dy = Math.max(-1.5, Math.min(1.5, dy));
            try {
                currentModel.setParamFloat("PARAM_ANGLE_X", dx * 30);
                currentModel.setParamFloat("PARAM_ANGLE_Y", dy * 30);
                currentModel.setParamFloat("PARAM_EYE_BALL_X", dx);
                currentModel.setParamFloat("PARAM_EYE_BALL_Y", dy);
                currentModel.setParamFloat("PARAM_BODY_ANGLE_X", dx * 10);
            } catch(e) {}
        });
    }

    function initClickInteraction() {
        const canvas = document.getElementById('live2d-canvas');
        if (!canvas) return;
        let startX = 0, startY = 0;
        canvas.onmousedown = (e) => { startX = e.clientX; startY = e.clientY; };
        canvas.onmouseup = (e) => {
            const dist = Math.sqrt(Math.pow(e.clientX - startX, 2) + Math.pow(e.clientY - startY, 2));
            if (dist < 10 && currentModel) {
                const motions = ["tap_body", "pinch_out", "shake", "flick_head"];
                if (currentModel.startRandomMotion) {
                    currentModel.startRandomMotion(motions[Math.floor(Math.random() * motions.length)], 3);
                }
                if (currentModel.setRandomExpression) currentModel.setRandomExpression();
                showMsg(touchTexts[Math.floor(Math.random() * touchTexts.length)]);
            }
        };
    }

    function emitVoiceInputState(state, detail = {}) {
        const sttBtn = document.getElementById('l2d-voice-btn');
        if (sttBtn) {
            sttBtn.classList.toggle('recording', state === 'listening' || state === 'speech');
            sttBtn.setAttribute('aria-pressed', String(voiceInputWanted));
            sttBtn.title = voiceInputWanted ? '暂停聆听' : '开始语音输入';
        }
        emitVoiceEvent('voice-input-state', { state, ...detail });
        if (voiceInputOptions?.onStateChange) voiceInputOptions.onStateChange(state, detail);
    }

    function ensureSpeechRecognition() {
        if (speechRecognition) return speechRecognition;
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) return null;

        speechRecognition = new SpeechRecognition();
        speechRecognition.lang = 'zh-CN';
        speechRecognition.interimResults = true;
        speechRecognition.continuous = true;
        speechRecognition.maxAlternatives = 1;

        speechRecognition.onstart = () => {
            voiceInputRunning = true;
            emitVoiceInputState('listening');
        };

        speechRecognition.onspeechstart = () => {
            emitVoiceInputState('speech');
            if (voiceInputOptions?.onSpeechStart) voiceInputOptions.onSpeechStart();
        };

        speechRecognition.onspeechend = () => {
            if (voiceInputWanted) emitVoiceInputState('processing');
        };

        speechRecognition.onresult = (event) => {
            let interimText = '';
            const finalParts = [];
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0]?.transcript || '';
                if (event.results[i].isFinal) finalParts.push(transcript);
                else interimText += transcript;
            }
            if (interimText && voiceInputOptions?.onInterim) {
                voiceInputOptions.onInterim(interimText.trim());
            }
            const finalText = finalParts.join('').trim();
            if (finalText && voiceInputOptions?.onFinal) {
                voiceInputOptions.onFinal(finalText);
            }
        };

        speechRecognition.onend = () => {
            voiceInputRunning = false;
            if (!voiceInputWanted) {
                emitVoiceInputState('idle');
                return;
            }
            emitVoiceInputState('reconnecting');
            clearTimeout(voiceInputRestartTimer);
            voiceInputRestartTimer = setTimeout(() => {
                if (voiceInputWanted) startRecognitionEngine();
            }, 260);
        };

        speechRecognition.onerror = (event) => {
            const error = event.error || 'unknown';
            voiceInputRunning = false;
            if (error === 'not-allowed' || error === 'service-not-allowed') {
                voiceInputWanted = false;
                emitVoiceInputState('blocked', { error });
            } else if (error !== 'aborted' && error !== 'no-speech') {
                emitVoiceInputState('error', { error });
            }
            if (voiceInputOptions?.onError) voiceInputOptions.onError(error);
        };

        return speechRecognition;
    }

    function startRecognitionEngine() {
        const recognition = ensureSpeechRecognition();
        if (!recognition) {
            voiceInputWanted = false;
            emitVoiceInputState('unsupported');
            if (voiceInputOptions?.onError) voiceInputOptions.onError('unsupported');
            return false;
        }
        if (voiceInputRunning) return true;
        try {
            recognition.start();
            return true;
        } catch (e) {
            if (e.name !== 'InvalidStateError') {
                emitVoiceInputState('error', { error: e.message || 'start-failed' });
            }
            return false;
        }
    }

    function startVoiceInput(options = {}) {
        voiceInputOptions = { ...options };
        voiceInputWanted = true;
        emitVoiceInputState('starting');
        return startRecognitionEngine();
    }

    function stopVoiceInput({ keepOptions = false } = {}) {
        voiceInputWanted = false;
        clearTimeout(voiceInputRestartTimer);
        voiceInputRestartTimer = null;
        if (speechRecognition && voiceInputRunning) {
            try { speechRecognition.abort(); } catch (e) { /* 已停止 */ }
        }
        voiceInputRunning = false;
        emitVoiceInputState('idle');
        if (!keepOptions) voiceInputOptions = null;
    }

    function isVoiceInputActive() {
        return voiceInputWanted;
    }

    // ASR - 单次语音输入与沉浸式持续聆听共用同一识别器
    function initASR() {
        const sttBtn = document.getElementById('l2d-voice-btn');
        const chatInput = document.getElementById('user-input');
        if (!sttBtn) return;
        const supported = Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
        if (!supported) {
            sttBtn.classList.add('unsupported');
            sttBtn.title = '当前浏览器不支持语音识别';
            return;
        }

        sttBtn.onclick = () => {
            if (voiceInputWanted) {
                stopVoiceInput({ keepOptions: voiceInputOptions?.mode === 'immersive' });
                return;
            }
            if (voiceInputOptions?.mode === 'immersive') {
                voiceInputWanted = true;
                startRecognitionEngine();
                return;
            }
            startVoiceInput({
                mode: 'manual',
                onStateChange: (state) => {
                    if (!chatInput) return;
                    chatInput.placeholder = state === 'speech' ? '正在听你说话…' : '想和 LightMe 说些什么…';
                },
                onFinal: (text) => {
                    if (chatInput) chatInput.value = text;
                    showMsg('听到了: ' + text);
                    stopVoiceInput();
                },
            });
        };
    }

    function nextCharacter() {
        charIdx = (charIdx + 1) % roleData.length;
        outfitIdx = 0;
        loadModel();
        showMsg("你好，我是 " + roleData[charIdx].name);
    }

    function nextOutfit() {
        const role = roleData[charIdx];
        outfitIdx = (outfitIdx + 1) % role.outfits.length;
        loadModel();
        const label = outfitLabels[role.outfits[outfitIdx]] || role.outfits[outfitIdx];
        showMsg(role.name + " - " + label);
    }

    function getCharName() { return roleData[charIdx].name; }

    function init() {
        injectLive2DHook();
        loadModel();
        initMouseTracking();
        initClickInteraction();
        initASR();
        const charBtn = document.getElementById('l2d-char-btn');
        if (charBtn) charBtn.onclick = nextCharacter;
        const outfitBtn = document.getElementById('l2d-outfit-btn');
        if (outfitBtn) outfitBtn.onclick = nextOutfit;
    }

    return {
        init,
        loadModel,
        showMsg,
        unlockAudio,
        playVoiceWithLipSync,
        stopVoice,
        isVoicePlaying,
        startVoiceInput,
        stopVoiceInput,
        isVoiceInputActive,
        nextCharacter,
        nextOutfit,
        getCharName,
    };
})();
