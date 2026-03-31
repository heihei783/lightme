import edge_tts
import asyncio

# 推荐几个适合猫娘的声音：
# zh-CN-XiaoxiaoNeural (活泼少女)
# zh-CN-XiaoyiNeural (可爱童声)
VOICE = "zh-CN-XiaoyiNeural" 

async def text_to_speech(text, output_path="data/voice/output.mp3"):
    """将文字转为语音文件"""
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_path)
    return output_path

# 测试一下
if __name__ == "__main__":
    test_text = "主人，欢迎回来喵！今天也要陪我玩吗？"
    asyncio.run(text_to_speech(test_text))
    print("语音已生成到 data/output.mp3 喵！")