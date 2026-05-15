// live2d-ctrl.js - Live2D 控制器 for LightMe
// 改编自 live2d/index.js，整合口型同步、鼠标追踪、点击交互、ASR

const Live2DCtrl = (() => {
    const M = API_BASE + '/live2d-models/';
    const roleData = [
        {
            name: "22娘",
            path: M + "22/",
            outfits: ["model.default.json", "model.2016.xmas.1.json", "model.2017.cba-normal.json", "model.2017.summer.normal.1.json", "model.2018.spring.json"]
        },
        {
            name: "33娘",
            path: M + "33/",
            outfits: ["model.default.json", "model.2016.xmas.1.json", "model.2017.cba-normal.json", "model.2017.summer.normal.1.json", "model.2018.spring.json"]
        },
        { name: "康娜", path: M + "Kobayaxi/", outfits: ["Kobayaxi.model.json"] },
        { name: "血小板", path: M + "platelet/", outfits: ["model.json"] },
        { name: "纱雾", path: M + "sagiri/", outfits: ["sagiri.model.json"] },
        { name: "小埋", path: M + "xiaomai/", outfits: ["xiaomai.model.json"] }
    ];

    const touchTexts = ["哎呀，别摸我啦！", "讨厌！", "哼~~", "再戳我就生气了哦！", "是在检查身体吗？", "哇！吓我一跳！", "你好呀主人~"];

    let charIdx = 0;
    let outfitIdx = 0;
    let currentModel = null;
    let msgTimer = null;
    let audioContext = null;
    let analyser = null;
    let lipSyncInterval = null;
    let hookInterval = null;
    let globalLipSyncValue = 0;

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
        canvas.width = 600;
        canvas.height = 1000;

        const role = roleData[charIdx];
        const modelPath = role.path + (role.outfits[outfitIdx] || role.outfits[0]);

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

    function playVoiceWithLipSync(base64Audio) {
        if (!base64Audio) return;
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!audioContext) audioContext = new AudioContext();
            if (audioContext.state === 'suspended') audioContext.resume();

            const audioData = atob(base64Audio);
            const arrayBuffer = new ArrayBuffer(audioData.length);
            const view = new Uint8Array(arrayBuffer);
            for (let i = 0; i < audioData.length; i++) view[i] = audioData.charCodeAt(i);

            audioContext.decodeAudioData(arrayBuffer, (buffer) => {
                const source = audioContext.createBufferSource();
                source.buffer = buffer;
                if (!analyser) analyser = audioContext.createAnalyser();
                analyser.fftSize = 256;
                source.connect(analyser);
                analyser.connect(audioContext.destination);
                source.start(0);

                if (lipSyncInterval) clearInterval(lipSyncInterval);
                const dataArray = new Uint8Array(analyser.frequencyBinCount);
                lipSyncInterval = setInterval(() => {
                    analyser.getByteFrequencyData(dataArray);
                    let sum = 0;
                    const binCount = Math.floor(dataArray.length / 2);
                    for (let i = 0; i < binCount; i++) sum += dataArray[i];
                    let volume = sum / binCount;
                    let targetValue = volume > 5 ? volume / 50 : 0;
                    targetValue = Math.min(1.0, Math.max(0, targetValue));
                    globalLipSyncValue = globalLipSyncValue * 0.3 + targetValue * 0.7;
                }, 20);

                source.onended = () => {
                    clearInterval(lipSyncInterval);
                    globalLipSyncValue = 0;
                };
            }, () => console.error("音频解码失败"));
        } catch (e) {
            console.error("语音播放错误:", e);
        }
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

    // ASR - 浏览器语音识别
    function initASR() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) return;

        const recognition = new SpeechRecognition();
        recognition.lang = 'zh-CN';
        recognition.interimResults = false;
        let isListening = false;

        const sttBtn = document.getElementById('l2d-voice-btn');
        const chatInput = document.getElementById('user-input');
        if (!sttBtn) return;

        sttBtn.onclick = () => {
            if (!isListening) { try { recognition.start(); } catch(e) {} }
            else { recognition.stop(); }
        };

        recognition.onstart = () => {
            isListening = true;
            sttBtn.classList.add('recording');
            if (chatInput) chatInput.placeholder = "正在听你说话...";
        };

        recognition.onresult = (event) => {
            const text = event.results[0][0].transcript;
            if (chatInput) chatInput.value = text;
            showMsg("听到了: " + text);
        };

        recognition.onend = () => {
            isListening = false;
            sttBtn.classList.remove('recording');
            if (chatInput) chatInput.placeholder = "输入消息...";
        };

        recognition.onerror = () => {
            isListening = false;
            sttBtn.classList.remove('recording');
        };
    }

    function nextCharacter() {
        charIdx = (charIdx + 1) % roleData.length;
        outfitIdx = 0;
        loadModel();
        showMsg("你好，我是 " + roleData[charIdx].name);
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
    }

    return { init, loadModel, showMsg, playVoiceWithLipSync, nextCharacter, getCharName };
})();
