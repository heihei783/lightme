"""图片生成模块 - 通过 OpenAI 兼容 API 生成图片"""
import requests
import base64
import random
import os
from utils.config_handler import load_configai_config
from utils.path_tool import get_abs_path


def craft_life_scene(personality_text: str, recent_context: str) -> str | None:
    """
    用 chat LLM 根据 AI 人格 + 对话上下文，构思一段虚拟生活场景 prompt。
    返回精炼的场景描述，失败返回 None。
    """
    system_prompt = (
        "你是一个创意场景描述生成器。"
        "根据以下 AI 角色的人设和最近的对话氛围，想象这个角色现在正在她自己的世界里做什么。"
        "生成一段 150 字以内的画面描述，用于 AI 绘画。"
        "必须包含：主体外观描述、场景环境、动作姿态、光线氛围、艺术风格。"
        "用自然语言描述，不要加任何前缀或解释，不要用 markdown。"
    )

    user_msg = f"【AI 人设】\n{personality_text[:600]}\n\n【最近对话】\n{recent_context or '（暂无对话记录）'}"

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.llm.chat_model import get_chat_model
        model = get_chat_model()
        resp = model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ])
        prompt = resp.content.strip()
        return prompt if prompt else None
    except Exception as e:
        print(f"[LifeScene] LLM 构思场景失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def generate_life_scene_image(session_id: str) -> tuple[str, str, str] | None:
    """
    编排函数：人格 + 对话 → 场景构思 → 生图 → 存入 SQLite。
    返回 (image_b64, scene_description, image_id)，失败返回 None。
    """
    from utils.db_handler import get_session_history, add_image_message

    # 1. 读取当前人格
    prompt_path = get_abs_path("app/llm/prompts/chat_prompt.txt")
    personality = ""
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            personality = f.read()
    else:
        print("[LifeScene] ❌ chat_prompt.txt 不存在:", prompt_path)

    if not personality.strip():
        print("[LifeScene] ❌ 人格为空，跳过生图")
        return None

    print(f"[LifeScene] ✅ 人格已读取 ({len(personality)} 字)")

    # 2. 获取最近对话
    recent_context = ""
    try:
        history = get_session_history(session_id)
        parts = []
        for msg in history[-6:]:
            role = "用户" if getattr(msg, "type", "") == "human" else "AI"
            content = msg.content if hasattr(msg, "content") else str(msg)
            parts.append(f"[{role}]: {content[:150]}")
        recent_context = "\n".join(parts)
        print(f"[LifeScene] ✅ 对话历史已获取 ({len(parts)} 条)")
    except Exception as e:
        print(f"[LifeScene] ⚠️ 获取对话历史失败: {e}")

    # 3. LLM 构思场景
    print("[LifeScene] 🔄 正在用 LLM 构思场景...")
    scene_prompt = craft_life_scene(personality, recent_context)
    if not scene_prompt:
        print("[LifeScene] ❌ LLM 场景构思失败")
        return None

    print(f"[LifeScene] ✅ 场景构思完成: {scene_prompt[:80]}...")

    # 4. 生图
    print("[LifeScene] 🔄 正在调用生图 API...")
    img_b64 = generate_image(scene_prompt)
    if not img_b64:
        print("[LifeScene] ❌ 生图 API 返回空")
        return None

    print("[LifeScene] ✅ 生图成功")

    # 5. base64 存入 SQLite，聊天历史写入轻量引用
    image_id = add_image_message(session_id, scene_prompt, img_b64)
    print(f"[LifeScene] 💾 图片已存入数据库: {image_id}")
    return img_b64, scene_prompt, image_id


def generate_image(prompt: str) -> str | None:
    """调用图片生成 API，返回 base64 编码的图片字符串，失败返回 None（每次动态读取配置）"""
    cfg = load_configai_config()
    api_key = cfg.get("IMAGE_GEN_MODEL_API_KEY")
    api_base = cfg.get("IMAGE_GEN_MODEL_URL", "").rstrip("/")
    model_name = cfg.get("IMAGE_GEN_MODEL_NAME", "")

    if not api_key:
        print("[ImageGen] ❌ 缺少 IMAGE_GEN_MODEL_API_KEY")
        return None
    if not api_base:
        print("[ImageGen] ❌ 缺少 IMAGE_GEN_MODEL_URL")
        return None
    if not model_name:
        print("[ImageGen] ❌ 缺少 IMAGE_GEN_MODEL_NAME")
        return None

    url = f"{api_base}/images/generations"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "prompt": prompt[:500],
        "n": 1,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            img_data = data.get("data", [{}])[0]
            if "b64_json" in img_data:
                return img_data["b64_json"]
            elif "url" in img_data and img_data["url"]:
                img_resp = requests.get(img_data["url"], timeout=30)
                if img_resp.status_code == 200:
                    return base64.b64encode(img_resp.content).decode("utf-8")
        else:
            print(f"[ImageGen] API error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[ImageGen] Exception: {e}")
    return None


def should_generate(probability: float = 0.08) -> bool:
    """随机判断是否触发图片生成（默认 8% 概率）"""
    return random.random() < probability
