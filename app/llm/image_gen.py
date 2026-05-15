"""图片生成模块 - 通过 OpenAI 兼容 API 生成图片"""
import requests
import base64
import random
from utils.config_handler import load_configai_config


def generate_image(prompt: str) -> str | None:
    """调用图片生成 API，返回 base64 编码的图片字符串，失败返回 None（每次动态读取配置）"""
    cfg = load_configai_config()
    api_key = cfg.get("IMAGE_GEN_MODEL_API_KEY")
    api_base = cfg.get("IMAGE_GEN_MODEL_URL", "").rstrip("/")
    model_name = cfg.get("IMAGE_GEN_MODEL_NAME", "")

    if not api_key or not api_base or not model_name:
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
        "size": "1024x1024",
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
