"""
LightMe FastAPI 后端服务
=======================
提供聊天、会话管理、模型配置、头像上传、TTS、图片生成等全部 API。

路由总览:
  GET    /sessions              - 获取所有会话列表
  GET    /history/{session_id}  - 获取会话历史消息
  POST   /chat                  - 发送消息 (支持可选图片, 流式响应)
  DELETE /session/{session_id}  - 删除会话
  POST   /avatar/upload         - 上传头像 (保存到本地 data/avatars/)
  POST   /image-gen             - AI 图片生成
  POST   /tts                   - 文字转语音 (返回 base64 mp3)
  GET    /config                - 获取完整 YAML 配置
  POST   /config/switch         - 一键切换模型 (chat/embedding/vision/image_gen)
  POST   /config/presets/save   - 保存某个类型的预设列表
  POST   /config/update         - 更新任意配置字段
  GET    /config/prompt/presets - 获取人格预设列表
  POST   /config/prompt/presets/save - 保存人格预设
  POST   /config/prompt/switch  - 切换当前激活的人格
"""

import asyncio
import json
import os
import uuid

import uvicorn
import yaml
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from utils.path_tool import get_abs_path
from starlette.responses import StreamingResponse
from utils import db_handler as db
from utils.path_tool import get_abs_path
from app.llm.llm_chain import chat_loop

app = FastAPI()

# ---------------------------------------------------------------------------
# CORS 中间件 — 允许前端跨域访问
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id"],
)

# ---------------------------------------------------------------------------
# 静态文件挂载
# ---------------------------------------------------------------------------
app.mount("/web", StaticFiles(directory="web/"), name="web")

# 头像存储目录（mount 在 /avatar/upload 路由之后注册，避免被 StaticFiles 拦截 POST）
AVATAR_DIR = get_abs_path("data/avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

# Live2D 模型目录 — 只读引用外部项目（不复制，节约磁盘）
LIVE2D_MODEL_DIR = get_abs_path("web/model")
if os.path.isdir(LIVE2D_MODEL_DIR):
    app.mount("/live2d-models", StaticFiles(directory=LIVE2D_MODEL_DIR), name="live2d-models")

# ============================= 会话管理 =============================

@app.get("/sessions")
async def get_sessions():
    """获取所有会话列表，用于左侧栏渲染"""
    return {"status": "success", "sessions": db.get_all_chats()}


@app.get("/history/{session_id}")
async def get_chat_history(session_id: str):
    """获取指定会话的历史消息"""
    history = db.get_history_obj(session_id)
    messages = []
    for msg in history.messages:
        role = "user-msg" if msg.type == "human" else "ai-msg"
        messages.append({"role": role, "content": msg.content})
    return {"status": "success", "history": messages}


@app.delete("/session/{session_id}")
async def delete_session_api(session_id: str):
    """删除指定会话及其历史"""
    db.delete_chat_list(session_id)
    db.clear_session(session_id)
    return {"status": "success"}


# ============================= 聊天接口 =============================

@app.post("/chat")
async def chat(request: Request):
    """
    核心聊天端点 — 支持文本和图片两种模式
    Body: { message, session_id?, image? }
    - image 为可选 base64 字符串，传入时走视觉模型识图
    - 响应为 streaming text/plain
    """
    data = await request.json()
    message = data.get("message")
    sid = data.get("session_id")
    image_b64 = data.get("image")  # 可选: base64 编码的图片

    if not sid or sid == "new":
        sid = db.create_new_chat(message)

    async def response_stream():
        for chunk in chat_loop(sid, message, image_b64):
            yield chunk

    return StreamingResponse(
        response_stream(),
        media_type="text/plain",
        headers={"X-Session-Id": sid},
    )


# ============================= 头像上传 =============================

@app.post("/avatar/upload")
async def upload_avatar(file: UploadFile = File(...)):
    """上传用户头像，保存到 data/avatars/ 目录，返回访问 URL"""
    ext = os.path.splitext(file.filename or ".png")[1] or ".png"
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return {"status": "error", "msg": "不支持的图片格式"}
    filename = f"avatar_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(AVATAR_DIR, filename)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return {"status": "error", "msg": "图片大小不能超过 5MB"}
    with open(filepath, "wb") as f:
        f.write(content)
    return {"status": "success", "filename": filename, "url": f"/avatar/{filename}"}

# 头像静态文件挂载 — 必须在 /avatar/upload 路由之后，否则 POST 会被 StaticFiles 拦截
app.mount("/avatar", StaticFiles(directory=AVATAR_DIR), name="avatar")

# ============================= 图片生成 =============================

@app.post("/image-gen")
async def image_gen(request: Request):
    """调用生图模型，根据 prompt 生成图片，返回 base64"""
    from app.llm.image_gen import generate_image

    data = await request.json()
    prompt = data.get("prompt", "")
    if not prompt:
        return {"status": "error", "msg": "缺少 prompt 参数"}
    img_b64 = generate_image(prompt)
    if img_b64:
        return {"status": "success", "image": img_b64}
    return {"status": "error", "msg": "图片生成失败，请检查生图模型配置"}


# ============================= TTS 语音合成 =============================

@app.get("/tts/voices")
async def tts_voices():
    """获取所有可用 TTS 音色列表"""
    from app.llm.tts import get_voice_list
    return {"status": "success", "voices": get_voice_list()}


@app.post("/tts")
async def tts_endpoint(request: Request):
    """文字转语音，返回 base64 编码的 mp3"""
    from app.llm.tts import tts_to_b64

    data = await request.json()
    text = data.get("text", "")
    voice = data.get("voice", "")
    provider = data.get("provider", "edge_tts")
    if not text:
        return {"status": "error", "msg": "缺少 text 参数"}
    try:
        audio_b64 = await tts_to_b64(text, voice if voice else None, provider)
        return {"status": "success", "audio": audio_b64}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


# ============================= RAG 文件管理 =============================

RAG_DIR = get_abs_path("data/rag_file")
os.makedirs(RAG_DIR, exist_ok=True)


@app.get("/rag/files")
async def list_rag_files():
    """列出当前知识库中的所有文件"""
    files = []
    if os.path.isdir(RAG_DIR):
        for f in os.listdir(RAG_DIR):
            fp = os.path.join(RAG_DIR, f)
            if os.path.isfile(fp):
                files.append({
                    "name": f,
                    "size": os.path.getsize(fp),
                })
    return {"status": "success", "files": files}


@app.post("/rag/upload")
async def upload_rag_file(file: UploadFile = File(...)):
    """上传文件到知识库"""
    ext = os.path.splitext(file.filename or ".txt")[1].lower()
    if ext not in (".txt", ".md", ".pdf", ".docx"):
        return {"status": "error", "msg": f"不支持的文件格式: {ext}"}
    filepath = os.path.join(RAG_DIR, file.filename)
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        return {"status": "error", "msg": "文件大小不能超过 20MB"}
    with open(filepath, "wb") as f:
        f.write(content)
    return {"status": "success", "msg": f"已上传: {file.filename}"}


@app.delete("/rag/file/{name}")
async def delete_rag_file(name: str):
    """从知识库中删除指定文件"""
    filepath = os.path.join(RAG_DIR, name)
    if not os.path.isfile(filepath):
        return {"status": "error", "msg": "文件不存在"}
    os.remove(filepath)
    return {"status": "success", "msg": f"已删除: {name}"}


# ============================= 配置管理 =============================

CONFIG_PATH = get_abs_path("config/config_ai.yaml")

# 模型类型 → 预设键名 / 激活字段 映射表
MODEL_TYPE_MAP = {
    "chat": {
        "presets_key": "CHAT_MODEL_PRESETS",
        "active_keys": {
            "model_name": "CHAT_MODEL_NAME",
            "provider": "CHAT_MODEL_PROVIDER",
            "api_key": "CHAT_MODEL_API_KEY",
            "base_url": "CHAT_MODEL_URL",
        },
    },
    "embedding": {
        "presets_key": "EMBEDDING_MODEL_PRESETS",
        "active_keys": {
            "model_name": "EMBEDDING_MODEL_NAME",
            "api_key": "EMBEDDING_MODEL_API_KEY",
            "base_url": "EMBEDDING_MODEL_URL",
        },
    },
    "vision": {
        "presets_key": "VISION_MODEL_PRESETS",
        "active_keys": {
            "model_name": "VISION_MODEL_NAME",
            "api_key": "VISION_MODEL_API_KEY",
            "base_url": "VISION_MODEL_URL",
        },
    },
    "image_gen": {
        "presets_key": "IMAGE_GEN_MODEL_PRESETS",
        "active_keys": {
            "model_name": "IMAGE_GEN_MODEL_NAME",
            "api_key": "IMAGE_GEN_MODEL_API_KEY",
            "base_url": "IMAGE_GEN_MODEL_URL",
        },
    },
}


def _read_config() -> dict:
    """读取 YAML 配置文件"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.FullLoader)


def _write_config(cfg: dict):
    """写入 YAML 配置文件"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


@app.get("/config")
async def get_config():
    """获取完整的 YAML 配置（前端配置页使用）"""
    return {"status": "success", "config": _read_config()}


@app.post("/config/switch")
async def switch_model(data: dict):
    """一键切换模型: 将预设中的值写入激活配置"""
    model_type = data.get("type", "chat")
    preset = data.get("preset", {})
    if model_type not in MODEL_TYPE_MAP:
        return {"status": "error", "msg": f"未知模型类型: {model_type}"}

    cfg = _read_config()
    mapping = MODEL_TYPE_MAP[model_type]
    for preset_field, config_key in mapping["active_keys"].items():
        val = preset.get(preset_field, "")
        if val:
            cfg[config_key] = val
    _write_config(cfg)
    name = preset.get("name", preset.get("model_name", ""))
    return {"status": "success", "msg": f"已切换到 {name}"}


@app.post("/config/presets/save")
async def save_presets(data: dict):
    """保存某个类型的预设列表（增/删/改后调用）"""
    model_type = data.get("type", "chat")
    presets = data.get("presets", [])
    if model_type not in MODEL_TYPE_MAP:
        return {"status": "error", "msg": f"未知模型类型: {model_type}"}
    cfg = _read_config()
    cfg[MODEL_TYPE_MAP[model_type]["presets_key"]] = presets
    _write_config(cfg)
    return {"status": "success", "msg": "预设已保存"}


@app.post("/config/update")
async def update_config_fields(data: dict):
    """更新任意配置字段（如 RAG/Agent 开关等）"""
    updates = data.get("updates", data)
    cfg = _read_config()
    for k, v in updates.items():
        if v is not None and v != "":
            cfg[k] = v
    _write_config(cfg)
    return {"status": "success", "msg": "配置已更新"}


# ============================= 人格预设 =============================

CHAT_PROMPT_PATH = get_abs_path("app/llm/prompts/chat_prompt.txt")
PROMPT_PRESETS_PATH = get_abs_path("config/personality_presets.json")

# 记录原始 prompt 内容，用于恢复默认
with open(CHAT_PROMPT_PATH, "r", encoding="utf-8") as _f:
    DEFAULT_PROMPT_CONTENT = _f.read()


def _read_presets() -> dict:
    """读取人格预设 JSON"""
    if not os.path.exists(PROMPT_PRESETS_PATH):
        return {"presets": [], "active": ""}
    with open(PROMPT_PRESETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_presets(data: dict):
    """写入人格预设 JSON"""
    os.makedirs(os.path.dirname(PROMPT_PRESETS_PATH), exist_ok=True)
    with open(PROMPT_PRESETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _sync_prompt_file(preset_name: str, presets: list):
    """将指定预设的内容同步到 chat_prompt.txt，并重载内存中的 prompt 模板"""
    from utils.file_handler import reload_chat_prompt

    for p in presets:
        if p.get("name") == preset_name:
            with open(CHAT_PROMPT_PATH, "w", encoding="utf-8") as f:
                f.write(p.get("content", ""))
            reload_chat_prompt()
            return
    # 未匹配 → 恢复默认
    with open(CHAT_PROMPT_PATH, "w", encoding="utf-8") as f:
        f.write(DEFAULT_PROMPT_CONTENT)
    reload_chat_prompt()


@app.get("/config/prompt/presets")
async def get_prompt_presets():
    """获取所有人格预设及当前激活项"""
    data = _read_presets()
    return {
        "status": "success",
        "presets": data.get("presets", []),
        "active": data.get("active", ""),
    }


@app.post("/config/prompt/presets/save")
async def save_prompt_presets(body: dict):
    """保存人格预设列表"""
    presets = body.get("presets", [])
    active = body.get("active", "")
    data = {"presets": presets, "active": active}
    _write_presets(data)
    _sync_prompt_file(active, presets)
    return {"status": "success", "msg": "人格预设已保存"}


@app.post("/config/prompt/switch")
async def switch_prompt_preset(body: dict):
    """切换当前激活的人格预设"""
    name = body.get("name", "")
    data = _read_presets()
    data["active"] = name
    _write_presets(data)
    _sync_prompt_file(name, data["presets"])
    return {"status": "success", "msg": f"已切换到人格: {name}"}


# ============================= 启动入口 =============================

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
