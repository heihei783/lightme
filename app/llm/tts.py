import edge_tts
import asyncio
import requests
from utils.config_handler import config_ai
import base64
# 推荐几个适合猫娘的声音：
# zh-CN-XiaoxiaoNeural (活泼少女)
# zh-CN-XiaoyiNeural (可爱童声)
VOICE = "zh-CN-XiaoyiNeural" 
VOICE_MODEL_MAP = {
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
async def text_to_speech(text, output_path="data/voice/output.mp3"):
    """将文字转为语音文件"""
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_path)
    return output_path

def get_tts_audio(text: str, voice_name: str):
    model_id = VOICE_MODEL_MAP.get(voice_name)
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
            url, json=payload, headers=headers, timeout=25, verify=False
        )
        if response.status_code == 200:
            return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        print(f"[TTS Exception] {e}")
    return None


# 测试一下
if __name__ == "__main__":
    test_text = "主人，欢迎回来喵！今天也要陪我玩吗？"
    asyncio.run(text_to_speech(test_text))
    print("语音已生成到 data/output.mp3 喵！")