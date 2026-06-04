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
import base64
import concurrent.futures
import json
import os
import threading as _threading
from time import sleep
import uuid

import uvicorn
import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
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
# 线程池 & 会话并发控制
# ---------------------------------------------------------------------------
# 聊天请求使用固定大小线程池，避免每个请求创建一个新线程
_chat_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="ChatLoop"
)

# 防止同一会话并发请求导致消息交错/历史损坏
_session_busy: dict[str, bool] = {}
_session_busy_lock = _threading.Lock()


def _try_acquire_session(sid: str) -> bool:
    """标记会话为忙碌。返回 True 表示获取成功，False 表示已有请求在处理中。"""
    with _session_busy_lock:
        if _session_busy.get(sid, False):
            return False
        _session_busy[sid] = True
        return True


def _release_session(sid: str):
    """标记会话为空闲。"""
    with _session_busy_lock:
        _session_busy.pop(sid, None)


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
app.mount("/images", StaticFiles(directory="data/images"), name="images")

# 头像存储目录（mount 在 /avatar/upload 路由之后注册，避免被 StaticFiles 拦截 POST）
AVATAR_DIR = get_abs_path("data/avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

# Live2D 模型目录 
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
    """获取指定会话的历史消息（支持文本和图片消息）"""
    history = db.get_history_obj(session_id)
    messages = []
    for msg in history.messages:
        role = "user-msg" if msg.type == "human" else "ai-msg"
        content = msg.content
        # 检测 AI 图片消息: ... [IMG]image_id  或  [IMG]image_id|scene_desc
        img_idx = content.find("[IMG]")
        if role == "ai-msg" and img_idx != -1:
            tail = content[img_idx + len("[IMG]"):].strip()
            # 兼容旧格式 [IMG]img_id|scene_desc
            image_id = tail.split("|")[0].strip()
            messages.append({
                "role": "ai-img",
                "content": "AI 的生活瞬间",
                "image": f"/image/{image_id}" if image_id else "",
            })
        else:
            # 截断过长消息，加速前端渲染
            display = content[:500] if len(content) > 500 else content
            messages.append({"role": role, "content": display})
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

    线程优化:
      - ThreadPoolExecutor 复用线程，避免每次请求创建新线程
      - 同一会话同时只允许一个请求，防止消息交错/历史损坏
      - 客户端断开时通过 cancel_flag 通知工作线程提前结束
    """
    data = await request.json()
    message = data.get("message")
    sid = data.get("session_id")
    image_b64 = data.get("image")

    if not sid or sid == "new":
        sid = db.create_new_chat(message)

    # 防止同一会话并发请求
    if not _try_acquire_session(sid):
        return StreamingResponse(
            iter(["⚠️ 当前会话正在处理中，请等待上一条消息完成后再发送。"]),
            media_type="text/plain",
            headers={"X-Session-Id": sid},
        )

    async def response_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        cancel_flag = [False]  # 可变容器，跨线程传递取消信号

        def _run_in_thread():
            """在线程池中运行 chat_loop，chunk 通过 queue 跨线程传递"""
            try:
                for chunk in chat_loop(sid, message, image_b64):
                    if cancel_flag[0]:
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, f"\n[错误] {e}")
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # 结束信号
                _release_session(sid)

        _chat_executor.submit(_run_in_thread)

        try:
            while True:
                # 检测客户端断开 → 通知工作线程停止
                if await request.is_disconnected():
                    cancel_flag[0] = True
                    break
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.5)
                    if chunk is None:
                        break
                    yield chunk
                except asyncio.TimeoutError:
                    continue
        finally:
            cancel_flag[0] = True  # 确保工作线程最终停止

    return StreamingResponse(
        response_stream(),
        media_type="text/plain",
        headers={"X-Session-Id": sid},
    )


# ============================= 头像上传 =============================

AVATAR_CONFIG_PATH = get_abs_path("data/avatar_config.json")


def _read_avatar_config() -> dict:
    """读取头像配置（持久化当前使用的头像文件名）"""
    if not os.path.exists(AVATAR_CONFIG_PATH):
        return {"user_avatar": "", "ai_avatar": ""}
    with open(AVATAR_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_avatar_config(cfg: dict):
    """写入头像配置"""
    os.makedirs(os.path.dirname(AVATAR_CONFIG_PATH), exist_ok=True)
    with open(AVATAR_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


@app.get("/avatar/current")
async def get_current_avatars():
    """获取当前用户头像和 AI 头像的文件名"""
    cfg = _read_avatar_config()
    return {
        "status": "success",
        "user_avatar": cfg.get("user_avatar", ""),
        "ai_avatar": cfg.get("ai_avatar", ""),
    }


@app.post("/avatar/upload")
async def upload_avatar(file: UploadFile = File(...), type: str = Form("user")):
    """上传用户头像，保存到 data/avatars/ 目录，返回访问 URL
    type: 'user' 或 'ai'，指定头像类型
    """
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

    # 持久化当前头像文件名到 JSON 配置（解决 GUI 模式重启丢失问题）
    cfg = _read_avatar_config()
    if type == "ai":
        cfg["ai_avatar"] = filename
    else:
        cfg["user_avatar"] = filename
    _write_avatar_config(cfg)

    return {"status": "success", "filename": filename, "url": f"/avatar/{filename}", "type": type}

# 头像静态文件挂载 — 必须在 /avatar/upload 路由之后，否则 POST 会被 StaticFiles 拦截
app.mount("/avatar", StaticFiles(directory=AVATAR_DIR), name="avatar")

# ============================= 图片生成 =============================

@app.get("/image/{image_id}")
async def serve_image(image_id: str):
    """从 SQLite 数据库读取生成图片并返回 PNG"""
    from fastapi.responses import Response

    img = db.get_image(image_id)
    if not img:
        return Response(status_code=404)
    return Response(
        content=base64.b64decode(img["b64"]),
        media_type="image/png",
    )


@app.post("/image-gen")
async def image_gen(request: Request):
    """AI 虚拟生活场景生图：根据人格+对话上下文构思场景并生图，存入 SQLite"""
    from app.llm.image_gen import generate_life_scene_image

    data = await request.json()
    session_id = data.get("session_id", "")
    if not session_id:
        return {"status": "error", "msg": "缺少 session_id 参数"}
    result = generate_life_scene_image(session_id)
    if result:
        img_b64, scene_desc, image_id = result
        return {"status": "success", "image": img_b64, "scene": scene_desc, "image_id": image_id}
    return {"status": "error", "msg": "图片生成失败，请检查生图模型和对话模型配置"}


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


# ============================= 工具 & 技能列表 =============================

@app.get("/tools-and-skills")
async def get_tools_and_skills():
    """返回当前所有可用工具和注册技能的列表，供前端展示"""
    from app.agent.tools import DEFAULT_TOOLS
    from app.agent.skills import skill_registry
    from app.agent.skill_loader import get_skill_tools

    # 基础工具
    base_tools = []
    for t in DEFAULT_TOOLS:
        desc = (t.description or "").split("\n")[0].strip()
        base_tools.append({"name": t.name, "description": desc})

    # 技能列表 + 每个技能附带自己的工具
    skills_data = []
    all_skill_tools = []
    for s in skill_registry.list_all():
        skill = skill_registry.get_by_name(s["name"])
        skill_tools = []
        if skill and skill.has_tools():
            for st in get_skill_tools(skill):
                desc = (st.description or "").split("\n")[0].strip()
                skill_tools.append({"name": st.name, "description": desc})
                all_skill_tools.append({"name": st.name, "description": desc, "skill": s["name"]})

        skills_data.append({
            "name": s["name"],
            "description": s.get("description", ""),
            "category": s.get("category", "general"),
            "keywords": s.get("keywords", []),
            "tools": skill_tools,
        })

    return {
        "status": "success",
        "base_tools": base_tools,
        "skills": skills_data,
        "total_tools": len(base_tools) + len(all_skill_tools),
        "total_skills": len(skills_data),
    }


# ============================= Shell 命令审批 =============================

import asyncio as _asyncio

from app.agent.tools import shell_approval_mgr
from utils.console_emitter import console


@app.get("/shell/approval-stream")
async def shell_approval_stream(request: Request):
    """SSE 端点 — 当有新的 Shell 审批请求时实时推送到前端"""

    async def event_stream():
        queue = _asyncio.Queue()

        def _push(pending_list: list):
            data = json.dumps(pending_list, ensure_ascii=False)
            try:
                queue.put_nowait(data)
            except Exception:
                pass

        shell_approval_mgr.add_listener(_push)
        try:
            # 先推送当前状态
            yield f"data: {json.dumps(shell_approval_mgr.list_pending(), ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await _asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except _asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            shell_approval_mgr.remove_listener(_push)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/shell/pending")
async def shell_pending():
    """返回当前待审批的 Shell 命令列表"""
    return {"status": "success", "pending": shell_approval_mgr.list_pending()}


@app.post("/shell/approve")
async def shell_approve(data: dict):
    """
    审批 Shell 命令
    Body: { "approval_id": "sh_xxx", "action": "approved" | "rejected" | "skipped" }
    """
    approval_id = data.get("approval_id")
    action = data.get("action", "rejected")
    if not approval_id:
        return {"status": "error", "msg": "缺少 approval_id"}
    if action not in ("approved", "rejected", "skipped"):
        return {"status": "error", "msg": f"无效 action: {action}"}
    shell_approval_mgr.set_result(approval_id, action)
    return {"status": "success", "msg": f"命令 {approval_id} {action}"}


# ============================= 控制台日志流 =============================

@app.get("/console/stream")
async def console_stream(request: Request):
    """SSE 端点 — 实时推送 Agent 内部日志到前端终端页面。
    新客户端连接时先回放环形缓冲区中的历史事件，再推送实时事件。"""

    async def event_stream():
        queue = _asyncio.Queue()

        def _push(event: dict):
            try:
                queue.put_nowait(json.dumps(event, ensure_ascii=False))
            except Exception:
                pass

        console.add_listener(_push)

        def _shell_push(pending_list: list):
            for item in pending_list:
                console.emit_shell(item["command"], item["id"])

        shell_approval_mgr.add_listener(_shell_push)
        try:
            # --- 历史回放: 新打开的终端页也能看到之前的日志 ---
            history = console.get_history()
            yield f"data: {json.dumps({'type': 'replay_start', 'count': len(history)}, ensure_ascii=False)}\n\n"
            for event in history:
                if event.get("type") == "shell_approval":
                    # 历史中的审批事件已处理完毕，转为普通日志展示，不弹审批栏
                    event = {
                        **event,
                        "type": "log",
                        "sender": "Shell",
                        "message": f"⚠ 历史审批: {event.get('command', '')[:100]}"
                    }
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # 再发送当前待审批的 shell 命令（这些才是需要弹审批栏的）
            pending = shell_approval_mgr.list_pending()
            for item in pending:
                console.emit_shell(item["command"], item["id"])

            yield f"data: {json.dumps({'type': 'connected', 'message': f'控制台已连接 (历史 {len(history)} 条)'}, ensure_ascii=False)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await _asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except _asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            console.remove_listener(_push)
            shell_approval_mgr.remove_listener(_shell_push)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================= 启动入口 =============================

@app.on_event("shutdown")
async def shutdown_event():
    """服务关闭时清理线程池资源"""
    _chat_executor.shutdown(wait=False)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
