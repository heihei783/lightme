const API = 'http://127.0.0.1:8000';

const state = {
    config: {},
    currentModelType: 'chat',
    editingPresetIndex: -1,
    personalities: { presets: [], active: '' },
    editingPersonalityIndex: -1,
    ragFiles: [],
    toastTimer: null,
};

const DEFAULT_RUNTIME = {
    agent_max_steps: 40,
    agent_max_runtime_seconds: 180,
    agent_max_tokens: 8000,
    planner_enabled: true,
    planner_parallelism: 2,
    trace_enabled: true,
    eval_mode: false,
};

const MODEL_LABELS = {
    chat: '对话',
    embedding: '嵌入',
    vision: '视觉',
    image_gen: '生图',
    tts: '语音',
};

function $(id) {
    return document.getElementById(id);
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

async function requestJson(path, options = {}) {
    const response = await fetch(API + path, options);
    let data;
    try {
        data = await response.json();
    } catch (_) {
        throw new Error(`服务返回了无法解析的响应 (${response.status})`);
    }
    if (!response.ok || data.status === 'error') {
        throw new Error(data.msg || `请求失败 (${response.status})`);
    }
    return data;
}

function jsonRequest(method, body) {
    return {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    };
}

function showToast(message, type = 'success') {
    const toast = $('save-toast');
    toast.textContent = message;
    toast.classList.toggle('error', type === 'error');
    toast.classList.add('show');
    if (state.toastTimer) clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
}

function setServiceStatus(status, label) {
    $('config-status-dot').className = `status-dot ${status}`;
    $('config-service-status').textContent = label;
}

function setButtonBusy(button, busy, busyLabel = '处理中') {
    if (!button) return;
    if (busy) {
        button.dataset.originalText = button.textContent;
        button.textContent = busyLabel;
        button.disabled = true;
    } else {
        button.textContent = button.dataset.originalText || button.textContent;
        button.disabled = false;
    }
}

async function init() {
    bindStaticEvents();
    bindSectionObserver();
    setServiceStatus('', '正在连接');
    try {
        const [configData, personalityData, ragData] = await Promise.all([
            requestJson('/config'),
            requestJson('/config/prompt/presets'),
            requestJson('/rag/files'),
        ]);
        state.config = configData.config || {};
        state.personalities = {
            presets: personalityData.presets || [],
            active: personalityData.active || '',
        };
        state.ragFiles = ragData.files || [];
        hydrateControls();
        renderTab('chat');
        renderPersonalities();
        renderRagFiles();
        setServiceStatus('online', '已连接');
    } catch (error) {
        state.config = { ...DEFAULT_RUNTIME };
        hydrateControls();
        renderTab('chat');
        renderPersonalities();
        renderRagFiles();
        setServiceStatus('error', '连接失败');
        showToast(error.message, 'error');
    }
}

function bindStaticEvents() {
    $('toggle-rag').addEventListener('change', saveGeneralToggles);
    $('toggle-agent').addEventListener('change', saveGeneralToggles);
    $('image-gen-probability').addEventListener('change', saveImageGenProbability);
    $('save-runtime-btn').addEventListener('click', saveRuntimeConfig);

    document.querySelector('.model-tabs').addEventListener('click', (event) => {
        const button = event.target.closest('.tab-btn');
        if (!button) return;
        switchModelTab(button.dataset.tab);
    });
    $('models').addEventListener('click', handleModelListAction);
    $('personality-list').addEventListener('click', handlePersonalityAction);
    $('rag-file-list').addEventListener('click', handleRagAction);

    $('rag-upload-btn').addEventListener('click', () => $('rag-file-input').click());
    $('rag-file-input').addEventListener('change', uploadRagFile);
    $('add-personality-btn').addEventListener('click', () => openPersonalityModal(-1));

    $('model-modal-save').addEventListener('click', saveCurrentPreset);
    $('model-modal-delete').addEventListener('click', deleteCurrentPreset);
    $('personality-modal-save').addEventListener('click', savePersonality);
    $('personality-modal-delete').addEventListener('click', deletePersonality);

    document.querySelectorAll('[data-close-modal]').forEach((button) => {
        button.addEventListener('click', () => closeModal(button.dataset.closeModal));
    });
    document.querySelectorAll('.modal-overlay').forEach((overlay) => {
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) closeModal(overlay.id);
        });
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        document.querySelectorAll('.modal-overlay.show').forEach((modal) => closeModal(modal.id));
    });
}

function bindSectionObserver() {
    if (!('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver((entries) => {
        const visible = entries
            .filter((entry) => entry.isIntersecting)
            .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (!visible) return;
        document.querySelectorAll('.nav-item').forEach((item) => {
            item.classList.toggle('active', item.dataset.section === visible.target.id);
        });
    }, { rootMargin: '-18% 0px -62% 0px', threshold: [0, 0.25, 0.5] });
    document.querySelectorAll('[data-observe-section]').forEach((section) => observer.observe(section));
}

function hydrateControls() {
    const config = { ...DEFAULT_RUNTIME, ...state.config };
    $('toggle-rag').checked = Boolean(config.rag_open);
    $('toggle-agent').checked = Boolean(config.agent_open);
    $('image-gen-probability').value = config.image_gen_probability ?? 0.08;
    $('agent-max-steps').value = config.agent_max_steps;
    $('agent-max-runtime').value = config.agent_max_runtime_seconds;
    $('agent-max-tokens').value = config.agent_max_tokens;
    $('planner-parallelism').value = config.planner_parallelism;
    $('planner-enabled').checked = Boolean(config.planner_enabled);
    $('trace-enabled').checked = Boolean(config.trace_enabled);
    $('eval-mode').checked = Boolean(config.eval_mode);
    renderRuntimeState();
}

function renderRuntimeState() {
    const enabled = $('toggle-agent').checked && $('planner-enabled').checked;
    const container = $('runtime-state');
    container.classList.toggle('enabled', enabled);
    container.querySelector('b').textContent = enabled ? 'Planner 已启用' : 'Planner 未启用';
}

async function updateConfig(updates, message) {
    await requestJson('/config/update', jsonRequest('POST', { updates }));
    Object.assign(state.config, updates);
    showToast(message);
}

async function saveGeneralToggles() {
    renderRuntimeState();
    try {
        await updateConfig({
            rag_open: $('toggle-rag').checked,
            agent_open: $('toggle-agent').checked,
        }, '基础能力设置已保存');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function saveImageGenProbability() {
    const value = readNumber('image-gen-probability', 0, 1, false);
    if (value === null) return;
    try {
        await updateConfig({ image_gen_probability: value }, '生图概率已保存');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function readNumber(id, min, max, integer = true) {
    const input = $(id);
    const value = integer ? Number.parseInt(input.value, 10) : Number.parseFloat(input.value);
    if (!Number.isFinite(value) || value < min || value > max) {
        showToast(`${input.closest('label, .setting-row')?.querySelector('span, strong')?.textContent || '数值'}超出允许范围`, 'error');
        input.focus();
        return null;
    }
    return value;
}

async function saveRuntimeConfig() {
    const button = $('save-runtime-btn');
    const maxSteps = readNumber('agent-max-steps', 1, 500);
    const maxRuntime = readNumber('agent-max-runtime', 5, 3600);
    const maxTokens = readNumber('agent-max-tokens', 1000, 1000000);
    const parallelism = readNumber('planner-parallelism', 1, 16);
    if ([maxSteps, maxRuntime, maxTokens, parallelism].some((value) => value === null)) return;

    const updates = {
        agent_max_steps: maxSteps,
        agent_max_runtime_seconds: maxRuntime,
        agent_max_tokens: maxTokens,
        planner_enabled: $('planner-enabled').checked,
        planner_parallelism: parallelism,
        trace_enabled: $('trace-enabled').checked,
        eval_mode: $('eval-mode').checked,
    };
    setButtonBusy(button, true, '保存中');
    try {
        await updateConfig(updates, 'Agent Runtime 策略已保存');
        renderRuntimeState();
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        setButtonBusy(button, false);
    }
}

function getPresetKey(type) {
    return {
        chat: 'CHAT_MODEL_PRESETS',
        embedding: 'EMBEDDING_MODEL_PRESETS',
        vision: 'VISION_MODEL_PRESETS',
        image_gen: 'IMAGE_GEN_MODEL_PRESETS',
        tts: 'TTS_MODEL_PRESETS',
    }[type] || '';
}

function getActiveKeys(type) {
    return {
        chat: { name: 'CHAT_MODEL_NAME' },
        embedding: { name: 'EMBEDDING_MODEL_NAME' },
        vision: { name: 'VISION_MODEL_NAME' },
        image_gen: { name: 'IMAGE_GEN_MODEL_NAME' },
        tts: { name: 'TTS_MODEL_NAME' },
    }[type] || {};
}

function getActiveName(type) {
    return state.config[getActiveKeys(type).name] || '';
}

function getActiveProvider(type) {
    return type === 'tts' ? (state.config.TTS_MODEL_PROVIDER || 'edge_tts') : '';
}

function switchModelTab(type) {
    if (!MODEL_LABELS[type]) return;
    state.currentModelType = type;
    document.querySelectorAll('.tab-btn').forEach((button) => {
        const active = button.dataset.tab === type;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', String(active));
    });
    document.querySelectorAll('.tab-content').forEach((content) => {
        content.classList.toggle('active', content.id === `tab-${type}`);
    });
    renderTab(type);
}

function renderTab(type) {
    const container = $(`tab-${type}`);
    if (!container) return;
    const presets = state.config[getPresetKey(type)] || [];
    const activeName = getActiveName(type);
    const activeProvider = getActiveProvider(type);
    const modelLabel = type === 'tts' ? 'Voice / Reference' : 'Model';
    const endpointLabel = type === 'tts' ? 'TTS Endpoint' : 'Endpoint';
    const rows = presets.map((preset, index) => {
        const isActive = preset.model_name === activeName && (type !== 'tts' || (preset.provider || 'fish_audio') === activeProvider);
        return `
            <div class="preset-item">
                <div class="preset-main">
                    <span class="name">${escapeHtml(preset.name || preset.model_name || '未命名预设')}${isActive ? '<span class="active-badge">CURRENT</span>' : ''}</span>
                    <span class="info"><span>${escapeHtml(preset.provider || type)}</span></span>
                </div>
                <span class="info">
                    <span>${modelLabel} · ${escapeHtml(preset.model_name || '-')}</span>
                    <span>${endpointLabel} · ${escapeHtml(preset.base_url || '-')}</span>
                </span>
                <span class="preset-actions">
                    ${isActive ? '' : `<button class="btn btn-switch" type="button" data-model-action="switch" data-index="${index}" data-type="${type}">切换</button>`}
                    <button class="btn" type="button" data-model-action="edit" data-index="${index}" data-type="${type}">编辑</button>
                    <button class="btn btn-del" type="button" data-model-action="delete" data-index="${index}" data-type="${type}">删除</button>
                </span>
            </div>
        `;
    }).join('');
    if (type === 'tts') {
        const edgeActive = activeProvider === 'edge_tts';
        const edgeRow = `
            <div class="preset-item builtin-preset">
                <div class="preset-main">
                    <span class="name">EdgeTTS 内置服务${edgeActive ? '<span class="active-badge">CURRENT</span>' : '<span class="builtin-badge">内置</span>'}</span>
                    <span class="info"><span>edge_tts</span></span>
                </div>
                <span class="info">
                    <span>音色 · 首页内置 8 个可选音色</span>
                    <span>无需 API Key 或 Endpoint</span>
                </span>
                <span class="preset-actions">
                    ${edgeActive ? '' : '<button class="btn btn-switch" type="button" data-model-action="switch-edge" data-type="tts">使用 EdgeTTS</button>'}
                </span>
            </div>
        `;
        container.innerHTML = `${edgeRow}${rows || '<div class="resource-empty">当前还没有 FishAudio 音色。</div>'}
            <button class="btn-add" type="button" data-model-action="add" data-index="-1" data-type="tts">添加 FishAudio 音色</button>`;
        return;
    }
    container.innerHTML = `${rows || '<div class="resource-empty">当前类型还没有模型预设。</div>'}
        <button class="btn-add" type="button" data-model-action="add" data-index="-1" data-type="${type}">添加${MODEL_LABELS[type]}模型预设</button>`;
}

function handleModelListAction(event) {
    const button = event.target.closest('[data-model-action]');
    if (!button) return;
    const type = button.dataset.type;
    const index = Number(button.dataset.index);
    const action = button.dataset.modelAction;
    if (action === 'switch') switchPreset(type, index, button);
    if (action === 'switch-edge') switchEdgeTTS(button);
    if (action === 'edit' || action === 'add') openModelModal(type, index);
    if (action === 'delete') deletePreset(type, index, button);
}

async function switchEdgeTTS(button) {
    setButtonBusy(button, true, '切换中');
    const preset = {
        name: 'EdgeTTS 内置服务',
        model_name: 'zh-CN-XiaoyiNeural',
        provider: 'edge_tts',
    };
    try {
        await requestJson('/config/switch', jsonRequest('POST', { type: 'tts', preset }));
        const data = await requestJson('/config');
        state.config = data.config || state.config;
        localStorage.setItem('tts_voice', JSON.stringify({
            voice: preset.model_name,
            provider: preset.provider,
        }));
        renderTab('tts');
        showToast('已切换到 EdgeTTS 内置服务');
    } catch (error) {
        setButtonBusy(button, false);
        showToast(error.message, 'error');
    }
}

async function switchPreset(type, index, button) {
    const preset = (state.config[getPresetKey(type)] || [])[index];
    if (!preset) return;
    setButtonBusy(button, true, '切换中');
    try {
        await requestJson('/config/switch', jsonRequest('POST', { type, preset }));
        const data = await requestJson('/config');
        state.config = data.config || state.config;
        if (type === 'tts') {
            localStorage.setItem('tts_voice', JSON.stringify({
                voice: preset.model_name,
                provider: preset.provider || 'edge_tts',
            }));
        }
        renderTab(type);
        showToast(`已切换到 ${preset.name || preset.model_name}`);
    } catch (error) {
        setButtonBusy(button, false);
        showToast(error.message, 'error');
    }
}

function openModelModal(type, index) {
    state.currentModelType = type;
    state.editingPresetIndex = index;
    const preset = index >= 0 ? (state.config[getPresetKey(type)] || [])[index] || {} : {};
    const presetTypeLabel = type === 'tts' ? '语音预设' : '模型预设';
    $('model-modal-title').textContent = index >= 0 ? `编辑${presetTypeLabel}` : `添加${presetTypeLabel}`;
    $('model-modal-delete').hidden = index < 0;
    $('model-modal-delete').style.display = index < 0 ? 'none' : '';
    $('modal-name').value = preset.name || '';
    $('modal-name').placeholder = type === 'tts' ? '例如 小艺 EdgeTTS' : '例如 DeepSeek V4';
    $('modal-model-name-label').textContent = type === 'tts' ? '音色 / Reference ID' : '模型名称';
    $('modal-base-url-label').textContent = type === 'tts' ? 'TTS Endpoint' : 'Base URL';
    $('modal-model-name').placeholder = type === 'tts' ? '例如 zh-CN-XiaoyiNeural 或 Fish Reference ID' : '例如 deepseek-v4-flash';
    $('modal-provider').placeholder = type === 'tts' ? 'edge_tts 或 fish_audio' : '例如 deepseek';
    $('modal-model-name').value = preset.model_name || '';
    $('modal-api-key').value = preset.api_key || '';
    $('modal-base-url').value = preset.base_url || '';
    $('modal-provider').value = preset.provider || '';
    if (type === 'tts' && index < 0) {
        $('modal-provider').value = 'fish_audio';
        $('modal-base-url').value = state.config.TTS_MODEL_URL || 'https://api.rubia.top/v1/tts';
    }
    openModal('model-modal', 'modal-name');
}

async function saveCurrentPreset() {
    const type = state.currentModelType;
    const name = $('modal-name').value.trim();
    const modelName = $('modal-model-name').value.trim();
    if (!name || !modelName) {
        showToast(type === 'tts' ? '预设名称和音色 ID 不能为空' : '预设名称和模型名称不能为空', 'error');
        (!name ? $('modal-name') : $('modal-model-name')).focus();
        return;
    }
    const presets = [...(state.config[getPresetKey(type)] || [])];
    const preset = {
        name,
        model_name: modelName,
        api_key: $('modal-api-key').value.trim(),
        base_url: $('modal-base-url').value.trim(),
        provider: $('modal-provider').value.trim(),
    };
    if (state.editingPresetIndex >= 0) presets[state.editingPresetIndex] = preset;
    else presets.push(preset);

    const button = $('model-modal-save');
    setButtonBusy(button, true, '保存中');
    try {
        await requestJson('/config/presets/save', jsonRequest('POST', { type, presets }));
        state.config[getPresetKey(type)] = presets;
        closeModal('model-modal');
        renderTab(type);
        showToast('模型预设已保存');
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        setButtonBusy(button, false);
    }
}

async function deleteCurrentPreset() {
    await deletePreset(state.currentModelType, state.editingPresetIndex, $('model-modal-delete'), true);
}

async function deletePreset(type, index, button, closeAfter = false) {
    const presets = [...(state.config[getPresetKey(type)] || [])];
    const preset = presets[index];
    if (!preset || !window.confirm(`确定删除模型预设“${preset.name || preset.model_name}”？`)) return;
    setButtonBusy(button, true, '删除中');
    presets.splice(index, 1);
    try {
        await requestJson('/config/presets/save', jsonRequest('POST', { type, presets }));
        state.config[getPresetKey(type)] = presets;
        if (closeAfter) closeModal('model-modal');
        renderTab(type);
        showToast('模型预设已删除');
    } catch (error) {
        setButtonBusy(button, false);
        showToast(error.message, 'error');
    }
}

function renderPersonalities() {
    const presets = state.personalities.presets || [];
    $('personality-list').innerHTML = presets.length ? presets.map((preset, index) => {
        const isActive = preset.name === state.personalities.active;
        return `
            <div class="preset-item">
                <div class="preset-main">
                    <span class="name">${escapeHtml(preset.name || '未命名人格')}${isActive ? '<span class="active-badge">CURRENT</span>' : ''}</span>
                    <span class="info"><span>Prompt preset</span></span>
                </div>
                <span class="personality-summary">${escapeHtml(preset.content || '暂无提示词内容')}</span>
                <span class="preset-actions">
                    ${isActive ? '' : `<button class="btn btn-switch" type="button" data-personality-action="switch" data-index="${index}">切换</button>`}
                    <button class="btn" type="button" data-personality-action="edit" data-index="${index}">编辑</button>
                    <button class="btn btn-del" type="button" data-personality-action="delete" data-index="${index}">删除</button>
                </span>
            </div>
        `;
    }).join('') : '<div class="resource-empty">当前没有人格预设。</div>';
}

function handlePersonalityAction(event) {
    const button = event.target.closest('[data-personality-action]');
    if (!button) return;
    const index = Number(button.dataset.index);
    const action = button.dataset.personalityAction;
    if (action === 'switch') switchPersonality(index, button);
    if (action === 'edit') openPersonalityModal(index);
    if (action === 'delete') deletePersonalityAt(index, button);
}

async function switchPersonality(index, button) {
    const preset = state.personalities.presets[index];
    if (!preset) return;
    setButtonBusy(button, true, '切换中');
    try {
        await requestJson('/config/prompt/switch', jsonRequest('POST', { name: preset.name }));
        state.personalities.active = preset.name;
        renderPersonalities();
        showToast(`已切换人格：${preset.name}`);
    } catch (error) {
        setButtonBusy(button, false);
        showToast(error.message, 'error');
    }
}

function openPersonalityModal(index) {
    state.editingPersonalityIndex = index;
    const preset = index >= 0 ? state.personalities.presets[index] || {} : {};
    $('personality-modal-title').textContent = index >= 0 ? '编辑人格预设' : '添加人格预设';
    $('personality-modal-delete').hidden = index < 0;
    $('personality-modal-delete').style.display = index < 0 ? 'none' : '';
    $('personality-name').value = preset.name || '';
    $('personality-content').value = preset.content || '';
    openModal('personality-modal', 'personality-name');
}

async function savePersonality() {
    const name = $('personality-name').value.trim();
    const content = $('personality-content').value.trim();
    if (!name || !content) {
        showToast('人格名称和提示词内容不能为空', 'error');
        (!name ? $('personality-name') : $('personality-content')).focus();
        return;
    }
    const presets = [...state.personalities.presets];
    const previousName = state.editingPersonalityIndex >= 0 ? presets[state.editingPersonalityIndex]?.name : '';
    if (state.editingPersonalityIndex >= 0) presets[state.editingPersonalityIndex] = { name, content };
    else presets.push({ name, content });
    const active = state.personalities.active === previousName ? name : (state.personalities.active || name);
    const button = $('personality-modal-save');
    setButtonBusy(button, true, '保存中');
    try {
        await requestJson('/config/prompt/presets/save', jsonRequest('POST', { presets, active }));
        state.personalities = { presets, active };
        closeModal('personality-modal');
        renderPersonalities();
        showToast('人格预设已保存');
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        setButtonBusy(button, false);
    }
}

async function deletePersonality() {
    await deletePersonalityAt(state.editingPersonalityIndex, $('personality-modal-delete'), true);
}

async function deletePersonalityAt(index, button, closeAfter = false) {
    const presets = [...state.personalities.presets];
    const preset = presets[index];
    if (!preset || !window.confirm(`确定删除人格预设“${preset.name}”？`)) return;
    setButtonBusy(button, true, '删除中');
    presets.splice(index, 1);
    const active = state.personalities.active === preset.name ? '' : state.personalities.active;
    try {
        await requestJson('/config/prompt/presets/save', jsonRequest('POST', { presets, active }));
        state.personalities = { presets, active };
        if (closeAfter) closeModal('personality-modal');
        renderPersonalities();
        showToast('人格预设已删除');
    } catch (error) {
        setButtonBusy(button, false);
        showToast(error.message, 'error');
    }
}

function formatFileSize(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    return `${(value / 1024).toFixed(1)} KB`;
}

function fileExtension(name) {
    const pieces = String(name || '').split('.');
    return pieces.length > 1 ? pieces.pop().toUpperCase() : 'FILE';
}

function renderRagFiles() {
    $('rag-file-list').innerHTML = state.ragFiles.length ? state.ragFiles.map((file, index) => `
        <div class="preset-item file-item">
            <span class="file-name">
                <span class="file-type-badge">${escapeHtml(fileExtension(file.name))}</span>
                <strong>${escapeHtml(file.name)}</strong>
            </span>
            <span class="file-size">${escapeHtml(formatFileSize(file.size))}</span>
            <button class="btn btn-del" type="button" data-rag-action="delete" data-index="${index}">删除</button>
        </div>
    `).join('') : '<div class="resource-empty">知识库中还没有文件。</div>';
}

function handleRagAction(event) {
    const button = event.target.closest('[data-rag-action="delete"]');
    if (!button) return;
    deleteRagFile(Number(button.dataset.index), button);
}

async function uploadRagFile() {
    const input = $('rag-file-input');
    const file = input.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const button = $('rag-upload-btn');
    setButtonBusy(button, true, '上传中');
    try {
        await requestJson('/rag/upload', { method: 'POST', body: formData });
        const data = await requestJson('/rag/files');
        state.ragFiles = data.files || [];
        renderRagFiles();
        showToast(`已上传 ${file.name}`);
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        input.value = '';
        setButtonBusy(button, false);
    }
}

async function deleteRagFile(index, button) {
    const file = state.ragFiles[index];
    if (!file || !window.confirm(`确定从知识库中删除“${file.name}”？`)) return;
    setButtonBusy(button, true, '删除中');
    try {
        await requestJson(`/rag/file/${encodeURIComponent(file.name)}`, { method: 'DELETE' });
        state.ragFiles.splice(index, 1);
        renderRagFiles();
        showToast('知识库文件已删除');
    } catch (error) {
        setButtonBusy(button, false);
        showToast(error.message, 'error');
    }
}

function openModal(id, focusId) {
    const modal = $(id);
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    window.requestAnimationFrame(() => $(focusId)?.focus());
}

function closeModal(id) {
    const modal = $(id);
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
}

init();
