const API = 'http://127.0.0.1:8000';
let fullConfig = {};
let currentModelType = 'chat';
let editingPresetIdx = -1;
let personalityData = { presets: [], active: '' };
let editingPersonalityIdx = -1;

async function init() {
    const resp = await fetch(API + '/config');
    const data = await resp.json();
    if (data.status === 'success') fullConfig = data.config;
    document.getElementById('toggle-rag').checked = fullConfig.rag_open || false;
    document.getElementById('toggle-agent').checked = fullConfig.agent_open || false;
    document.getElementById('companion-interval').value = fullConfig.companion_interval || 10;
    document.getElementById('image-gen-probability').value = fullConfig.image_gen_probability ?? 0.08;
    await loadPersonalities();
    await loadRagFiles();
    renderTab('chat');
    bindTabs();
}

async function loadPersonalities() {
    try {
        const resp = await fetch(API + '/config/prompt/presets');
        const data = await resp.json();
        if (data.status === 'success') personalityData = { presets: data.presets, active: data.active };
    } catch (e) { console.error(e); }
    renderPersonalities();
}

function bindTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.onclick = () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentModelType = btn.dataset.tab;
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById('tab-' + currentModelType).classList.add('active');
            renderTab(currentModelType);
        };
    });
}

function getPresetKey(type) {
    const map = { chat: 'CHAT_MODEL_PRESETS', embedding: 'EMBEDDING_MODEL_PRESETS', vision: 'VISION_MODEL_PRESETS', image_gen: 'IMAGE_GEN_MODEL_PRESETS' };
    return map[type] || '';
}

function getActiveKeys(type) {
    const map = {
        chat: { name: 'CHAT_MODEL_NAME', key: 'CHAT_MODEL_API_KEY', url: 'CHAT_MODEL_URL', provider: 'CHAT_MODEL_PROVIDER' },
        embedding: { name: 'EMBEDDING_MODEL_NAME', key: 'EMBEDDING_MODEL_API_KEY', url: 'EMBEDDING_MODEL_URL' },
        vision: { name: 'VISION_MODEL_NAME', key: 'VISION_MODEL_API_KEY', url: 'VISION_MODEL_URL' },
        image_gen: { name: 'IMAGE_GEN_MODEL_NAME', key: 'IMAGE_GEN_MODEL_API_KEY', url: 'IMAGE_GEN_MODEL_URL' }
    };
    return map[type] || {};
}

function getActiveName(type) {
    const keys = getActiveKeys(type);
    return fullConfig[keys.name] || '';
}

function renderTab(type) {
    const container = document.getElementById('tab-' + type);
    if (!container) return;
    const presets = fullConfig[getPresetKey(type)] || [];
    const activeName = getActiveName(type);
    let html = '';
    presets.forEach((p, i) => {
        const isActive = p.model_name === activeName;
        html += `<div class="preset-item">
            <span class="name">${esc(p.name || p.model_name)}${isActive ? '<span class="active-badge">当前</span>' : ''}</span>
            <span class="info"><span>模型: ${esc(p.model_name)}</span><span>URL: ${esc(p.base_url || '-')}</span></span>
            <button class="btn btn-switch" onclick="switchPreset('${type}', ${i})">切换</button>
            <button class="btn btn-edit" onclick="openModelModal('${type}', ${i})">编辑</button>
            <button class="btn btn-del" onclick="deletePreset('${type}', ${i})">删除</button>
        </div>`;
    });
    html += `<button class="btn-add" onclick="openModelModal('${type}', -1)">+ 添加${type==='chat'?'对话':type==='embedding'?'嵌入':type==='vision'?'视觉':'生图'}模型预设</button>`;
    container.innerHTML = html;
}

async function switchPreset(type, idx) {
    const presets = fullConfig[getPresetKey(type)] || [];
    const preset = presets[idx];
    if (!preset) return;
    await fetch(API + '/config/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, preset })
    });
    const resp = await fetch(API + '/config');
    const data = await resp.json();
    if (data.status === 'success') fullConfig = data.config;
    renderTab(type);
}

function openModelModal(type, idx) {
    editingPresetIdx = idx;
    currentModelType = type;
    document.getElementById('model-modal').classList.add('show');
    document.getElementById('model-modal-title').textContent = idx >= 0 ? '编辑预设' : '添加预设';
    document.getElementById('modal-delete').style.display = idx >= 0 ? '' : 'none';
    if (idx >= 0) {
        const p = fullConfig[getPresetKey(type)][idx];
        document.getElementById('modal-name').value = p.name || '';
        document.getElementById('modal-model-name').value = p.model_name || '';
        document.getElementById('modal-api-key').value = p.api_key || '';
        document.getElementById('modal-base-url').value = p.base_url || '';
        document.getElementById('modal-provider').value = p.provider || '';
    } else {
        document.getElementById('modal-name').value = '';
        document.getElementById('modal-model-name').value = '';
        document.getElementById('modal-api-key').value = '';
        document.getElementById('modal-base-url').value = '';
        document.getElementById('modal-provider').value = '';
    }
}

async function saveCurrentPreset() {
    const type = currentModelType;
    const presets = [...(fullConfig[getPresetKey(type)] || [])];
    const newPreset = {
        name: document.getElementById('modal-name').value.trim(),
        model_name: document.getElementById('modal-model-name').value.trim(),
        api_key: document.getElementById('modal-api-key').value.trim(),
        base_url: document.getElementById('modal-base-url').value.trim(),
        provider: document.getElementById('modal-provider').value.trim()
    };
    if (editingPresetIdx >= 0) presets[editingPresetIdx] = newPreset;
    else presets.push(newPreset);
    await fetch(API + '/config/presets/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, presets })
    });
    fullConfig[getPresetKey(type)] = presets;
    closeModal('model-modal');
    renderTab(type);
}

async function deleteCurrentPreset() {
    if (!confirm('确定删除此预设？')) return;
    const type = currentModelType;
    const presets = [...(fullConfig[getPresetKey(type)] || [])];
    presets.splice(editingPresetIdx, 1);
    await fetch(API + '/config/presets/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, presets })
    });
    fullConfig[getPresetKey(type)] = presets;
    closeModal('model-modal');
    renderTab(type);
}

async function deletePreset(type, idx) {
    if (!confirm('确定删除此预设？')) return;
    const presets = [...(fullConfig[getPresetKey(type)] || [])];
    presets.splice(idx, 1);
    await fetch(API + '/config/presets/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, presets })
    });
    fullConfig[getPresetKey(type)] = presets;
    renderTab(type);
}

async function saveToggles() {
    await fetch(API + '/config/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            updates: {
                rag_open: document.getElementById('toggle-rag').checked,
                agent_open: document.getElementById('toggle-agent').checked
            }
        })
    });
}

async function saveCompanionInterval() {
    const val = parseInt(document.getElementById('companion-interval').value) || 10;
    await fetch(API + '/config/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: { companion_interval: val } })
    });
}

async function saveImageGenProbability() {
    const val = parseFloat(document.getElementById('image-gen-probability').value);
    if (isNaN(val) || val < 0 || val > 1) return;
    await fetch(API + '/config/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: { image_gen_probability: val } })
    });
}

function renderPersonalities() {
    const container = document.getElementById('personality-list');
    let html = '';
    personalityData.presets.forEach((p, i) => {
        const isActive = p.name === personalityData.active;
        html += `<div class="preset-item">
            <span class="name">${esc(p.name)}${isActive ? '<span class="active-badge">当前</span>' : ''}</span>
            <span class="info">${esc((p.content || '').slice(0, 80))}...</span>
            <button class="btn btn-switch" onclick="switchPersonality('${esc(p.name)}')">切换</button>
            <button class="btn btn-edit" onclick="openPersonalityModal(${i})">编辑</button>
            <button class="btn btn-del" onclick="deletePersonalityAt(${i})">删除</button>
        </div>`;
    });
    container.innerHTML = html;
}

async function switchPersonality(name) {
    await fetch(API + '/config/prompt/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    personalityData.active = name;
    renderPersonalities();
}

function openPersonalityModal(idx) {
    editingPersonalityIdx = idx;
    document.getElementById('personality-modal').classList.add('show');
    document.getElementById('personality-modal-title').textContent = idx >= 0 ? '编辑人格预设' : '添加人格预设';
    document.getElementById('personality-delete').style.display = idx >= 0 ? '' : 'none';
    if (idx >= 0) {
        const p = personalityData.presets[idx];
        document.getElementById('personality-name').value = p.name || '';
        document.getElementById('personality-content').value = p.content || '';
    } else {
        document.getElementById('personality-name').value = '';
        document.getElementById('personality-content').value = '';
    }
}

async function savePersonality() {
    const name = document.getElementById('personality-name').value.trim();
    const content = document.getElementById('personality-content').value.trim();
    if (!name || !content) { alert('名称和内容不能为空'); return; }
    const presets = [...personalityData.presets];
    const newPreset = { name, content };
    if (editingPersonalityIdx >= 0) presets[editingPersonalityIdx] = newPreset;
    else presets.push(newPreset);
    const active = personalityData.active || name;
    await fetch(API + '/config/prompt/presets/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ presets, active })
    });
    personalityData.presets = presets;
    personalityData.active = active;
    closeModal('personality-modal');
    renderPersonalities();
}

async function deletePersonality() {
    if (!confirm('确定删除此人格预设？')) return;
    const presets = [...personalityData.presets];
    presets.splice(editingPersonalityIdx, 1);
    const active = personalityData.active === personalityData.presets[editingPersonalityIdx]?.name ? '' : personalityData.active;
    await fetch(API + '/config/prompt/presets/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ presets, active })
    });
    personalityData.presets = presets;
    personalityData.active = active;
    closeModal('personality-modal');
    renderPersonalities();
}

async function deletePersonalityAt(idx) {
    if (!confirm('确定删除此人格预设？')) return;
    const presets = [...personalityData.presets];
    const deletingName = presets[idx]?.name;
    presets.splice(idx, 1);
    const active = personalityData.active === deletingName ? '' : personalityData.active;
    await fetch(API + '/config/prompt/presets/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ presets, active })
    });
    personalityData.presets = presets;
    personalityData.active = active;
    renderPersonalities();
}

// ==================== 知识库文件管理 ====================

async function loadRagFiles() {
    try {
        const resp = await fetch(API + '/rag/files');
        const data = await resp.json();
        if (data.status === 'success') {
            renderRagFiles(data.files);
        }
    } catch (e) { console.error('加载知识库列表失败:', e); }
}

function renderRagFiles(files) {
    const container = document.getElementById('rag-file-list');
    if (!files || files.length === 0) {
        container.innerHTML = '<p style="color:#999;font-size:13px;">暂无文件，上传 txt / md / pdf / docx 到知识库</p>';
        return;
    }
    let html = '';
    files.forEach(f => {
        const sizeKb = (f.size / 1024).toFixed(1);
        html += `<div class="preset-item">
            <span class="name">${esc(f.name)}</span>
            <span class="info">${sizeKb} KB</span>
            <button class="btn btn-del" onclick="deleteRagFile('${esc(f.name)}')">删除</button>
        </div>`;
    });
    container.innerHTML = html;
}

async function uploadRagFile() {
    const input = document.getElementById('rag-file-input');
    const file = input.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    try {
        const resp = await fetch(API + '/rag/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.status === 'success') {
            await loadRagFiles();
        } else {
            alert('上传失败: ' + (data.msg || '未知错误'));
        }
    } catch (e) { console.error('上传文件失败:', e); }
    input.value = '';
}

async function deleteRagFile(name) {
    if (!confirm(`确定从知识库中删除 "${name}" ？`)) return;
    try {
        const resp = await fetch(API + '/rag/file/' + encodeURIComponent(name), { method: 'DELETE' });
        const data = await resp.json();
        if (data.status === 'success') {
            await loadRagFiles();
        } else {
            alert('删除失败: ' + (data.msg || '未知错误'));
        }
    } catch (e) { console.error('删除文件失败:', e); }
}

function closeModal(id) {
    document.getElementById(id).classList.remove('show');
}

function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

window.onclick = (e) => {
    if (e.target.classList.contains('modal-overlay')) e.target.classList.remove('show');
};

init();
