import edge_tts
import asyncio
import requests
from utils.config_handler import config_ai
import base64
import io

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


def get_voice_list() -> list[dict]:
    """返回所有可用音色列表（EdgeTTS + FishAudio）"""
    voices = []
    for name, voice_id in EDGE_VOICE_MAP.items():
        voices.append({"name": name, "voice": voice_id, "provider": "edge_tts"})
    for name, model_id in FISH_VOICE_MAP.items():
        voices.append({"name": f"{name} (FishAudio)", "voice": name, "provider": "fish_audio"})
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


def fish_tts_to_b64(text: str, voice_name: str) -> str | None:
    """使用 FishAudio 生成语音，返回 base64 编码字符串"""
    model_id = FISH_VOICE_MAP.get(voice_name)
    if not model_id or not config_ai.get('MY_FISH_AUDIO_KEY'):
        return None
    url = "https://api.rubia.top/v1/tts"
    headers = {
        "Authorization": f"Bearer {config_ai.get('MY_FISH_AUDIO_KEY')}",
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
            url, json=payload, headers=headers, timeout=25,
        )
        if response.status_code == 200:
            return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"[FishTTS Exception] {e}")
    return None


async def tts_to_b64(text: str, voice: str = None, provider: str = "edge_tts") -> str:
    """统一 TTS 入口：根据 provider 路由到 EdgeTTS 或 FishAudio"""
    if provider == "fish_audio":
        result = fish_tts_to_b64(text, voice)
        if result is None:
            raise RuntimeError(f"FishAudio TTS 失败，请检查 MY_FISH_AUDIO_KEY 配置或音色名称: {voice}")
        return result
    return await edge_tts_to_b64(text, voice)


async def text_to_speech(text, output_path="data/voice/output.mp3"):
    """将文字转为语音文件（EdgeTTS）"""
    communicate = edge_tts.Communicate(text, EDGE_VOICE)
    await communicate.save(output_path)
    return output_path


if __name__ == "__main__":
    test_text = "主人，欢迎回来喵！今天也要陪我玩吗？"
    asyncio.run(text_to_speech(test_text))
    print("语音已生成到 data/output.mp3 喵！")
