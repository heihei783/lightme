import asyncio
import base64
import io

import edge_tts
import requests

from utils.config_handler import config_ai

# ---------------------------------------------------------------------------
# EdgeTTS 音色映射 — 显示名称 → edge_tts voice id
# ---------------------------------------------------------------------------
EDGE_VOICE_MAP = {
    "小艺 (EdgeTTS)": "zh-CN-XiaoyiNeural",
    "晓晓 (EdgeTTS)": "zh-CN-XiaoxiaoNeural",
    "云希 (EdgeTTS)": "zh-CN-YunxiNeural",
    "云扬 (EdgeTTS)": "zh-CN-YunyangNeural",
    "晓辰 (EdgeTTS)": "zh-CN-XiaochenNeural",
    "晓涵 (EdgeTTS)": "zh-CN-XiaohanNeural",
    "Nanami (EdgeTTS)": "ja-JP-NanamiNeural",
    "Aoi (EdgeTTS)": "ja-JP-AoiNeural",
}

# ---------------------------------------------------------------------------
# FishAudio 音色映射 — 显示名称 → reference_id
# ---------------------------------------------------------------------------
FISH_VOICE_MAP = {
    "爱丽丝": "e488ebeadd83496b97a3cd472dcd04ab",
    "塔菲": "55b28b196e1c4fff9a55cd32a46eff25",
    "小孩姐": "4ca68a299cb24ae599dbb828dc31a73c",
    "才羽桃": "05c25a82cfe0426ab63d3d71ba8656cf",
    "可莉": "0b8449eb752c4f888f463fc5d2c0db65",
    "丁真": "54a5170264694bfc8e9ad98df7bd89c3",
    "小团团": "0da1e00b71164d8cb3761c714b11da64",
    "曼波": "0f08cacd3e354471a4b94dd00b4cc4a3",
    "东雪莲": "94abfed6539d48d281fdb06dc0e09664",
}

# 默认音色
EDGE_VOICE = "zh-CN-XiaoyiNeural"
DEFAULT_FISH_URL = "https://api.rubia.top/v1/tts"


def get_default_voice() -> dict:
    """返回配置文件中当前激活的 TTS 音色。"""
    return {
        "voice": config_ai.get("TTS_MODEL_NAME") or EDGE_VOICE,
        "provider": config_ai.get("TTS_MODEL_PROVIDER") or "edge_tts",
    }


def get_voice_list() -> list[dict]:
    """返回配置预设及内置音色，供首页下拉框使用。"""
    voices = []
    seen = set()
    for name, voice_id in EDGE_VOICE_MAP.items():
        voices.append({"name": name, "voice": voice_id, "provider": "edge_tts"})
        seen.add(("edge_tts", voice_id))
    for preset in config_ai.get("TTS_MODEL_PRESETS", []):
        provider = preset.get("provider") or "edge_tts"
        voice = preset.get("model_name") or preset.get("voice")
        if not voice:
            continue
        voices.append({
            "name": preset.get("name") or voice,
            "voice": voice,
            "provider": provider,
        })
        seen.add((provider, voice))
    for name, model_id in FISH_VOICE_MAP.items():
        if ("fish_audio", model_id) not in seen:
            voices.append({"name": f"{name} (FishAudio)", "voice": model_id, "provider": "fish_audio"})
    return voices


async def edge_tts_to_b64(text: str, voice: str = None) -> str:
    """使用 EdgeTTS 生成语音，返回 base64 编码字符串"""
    v = voice or EDGE_VOICE
    communicate = edge_tts.Communicate(text, v)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def _fish_endpoint(base_url: str | None) -> str:
    url = str(base_url or DEFAULT_FISH_URL).rstrip("/")
    return url if url.endswith("/tts") else f"{url}/tts"


def fish_tts_to_b64(
    text: str,
    voice_name: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str | None:
    """使用 FishAudio 生成语音，返回 base64 编码字符串"""
    model_id = FISH_VOICE_MAP.get(voice_name, voice_name)
    key = api_key or config_ai.get("TTS_MODEL_API_KEY") or config_ai.get("MY_FISH_AUDIO_KEY")
    if not model_id or not key:
        return None
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "reference_id": model_id,
        "format": "mp3",
        "latency": "normal",
    }
    try:
        response = requests.post(
            _fish_endpoint(base_url or config_ai.get("TTS_MODEL_URL")),
            json=payload, headers=headers, timeout=25,
        )
        if response.status_code == 200:
            return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"[FishTTS Exception] {e}")
    return None


async def tts_to_b64(text: str, voice: str = None, provider: str = None) -> str:
    """统一 TTS 入口：根据 provider 路由到 EdgeTTS 或 FishAudio"""
    provider = provider or config_ai.get("TTS_MODEL_PROVIDER") or "edge_tts"
    voice = voice or config_ai.get("TTS_MODEL_NAME") or EDGE_VOICE
    if provider == "fish_audio":
        result = fish_tts_to_b64(text, voice)
        if result is None:
            raise RuntimeError(f"FishAudio TTS 失败，请检查 TTS API Key、Endpoint 或 Reference ID: {voice}")
        return result
    if provider == "edge_tts":
        return await edge_tts_to_b64(text, voice)
    raise ValueError(f"暂不支持的 TTS Provider: {provider}")


async def text_to_speech(text, output_path="data/voice/output.mp3"):
    """将文字转为语音文件（EdgeTTS）"""
    voice = config_ai.get("TTS_MODEL_NAME") or EDGE_VOICE
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path


if __name__ == "__main__":
    test_text = "主人，欢迎回来喵！今天也要陪我玩吗？"
    asyncio.run(text_to_speech(test_text))
    print("语音已生成到 data/output.mp3 喵！")
