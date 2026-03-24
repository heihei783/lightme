import ntplib
from datetime import datetime
from utils.path_tool import get_abs_path
from langchain_community.chat_message_histories import SQLChatMessageHistory
from utils.config_handler import config_ai


# 数据库文件路径
DB_URL = f"sqlite:///{get_abs_path('data/chat_history.db').replace('\\', '/')}"

#获取时间
def get_time() ->str:
    try:
        client = ntplib.NTPClient()
        # 请求阿里云的时间服务器
        response = client.request('ntp.aliyun.com', version=3)
        print("----正在获取互联网时间")
        return datetime.fromtimestamp(response.tx_time).strftime("%Y-%m-%d %H:%M:%S")
    except:
        # 如果断网了，再退回到系统时间
        print("----互联网时间获取失败，正在获取本地时间")
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 1. 专门负责获取“管理对象” (也就是那个能操作数据库的“遥控器”)
def _get_history_obj(session_id: str):
    return SQLChatMessageHistory(
        session_id=session_id,
        connection_string=DB_URL
    )

# 2. 专门负责获取“给 AI 看的消息列表” (截断后的数据)
def get_session_history(session_id: str):
    history_obj = _get_history_obj(session_id)
    limit = config_ai.get("chat_history_len", 10)
    # 返回的是 List[BaseMessage]
    return history_obj.messages[-limit:]

# 3. 向数据库添加历史记录
def add_message(session_id: str, user_input: str, ai_response: str):
    time_str = get_time()
    # 关键：这里必须获取“对象”，而不是“列表”
    history_obj = _get_history_obj(session_id)
    
    # 使用对象的方法存入数据库
    history_obj.add_user_message(f"[{time_str}] {user_input}")
    history_obj.add_ai_message(f"[{time_str}] {ai_response}")


if __name__ == "__main__":
    time = get_time()
    print(time)

    test_id = "xiaohundun_test"
    add_message(test_id, "你好，数据库！", "你好，我是 AI。")
    
    # 验证读取
    history = get_session_history(test_id)
    print("历史记录中的消息：")
    print(history)