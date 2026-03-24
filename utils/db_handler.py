import ntplib
import sqlite3
import uuid
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

# 4. 清空会话的历史记录
def clear_session(session_id: str):
    history_obj = _get_history_obj(session_id)
    # 这个内置方法会删除数据库中该 session_id 对应的所有行
    history_obj.clear()
    print(f"已清空会话 {session_id} 的所有历史记录")

#------------------------用户不同对话逻辑-------------------------

def get_raw_db_path():
    return get_abs_path('data/chat_history.db')

# 初始化索引表（如果不存在则创建）
def init_chat_list_table():
    conn = sqlite3.connect(get_raw_db_path())
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_list (
            session_id TEXT PRIMARY KEY,
            chat_title TEXT,
            create_time DATETIME,
            update_time DATETIME
        )
    ''')
    conn.commit()
    conn.close()

# 开启一个全新的对话记录
def create_new_chat(first_question: str = "新对话"):
    init_chat_list_table() # 确保表存在
    
    new_id = f"chat_{uuid.uuid4().hex[:8]}"
    time_str = get_time() 
    
    # 截取用户第一句话的前15个字作为标题
    title = first_question[:15] + "..." if len(first_question) > 15 else first_question
    
    conn = sqlite3.connect(get_raw_db_path())
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_list (session_id, chat_title, create_time, update_time) VALUES (?, ?, ?, ?)",
        (new_id, title, time_str, time_str)
    )
    conn.commit()
    conn.close()
    return new_id

# 获取所有的对话列表（按时间倒序，最新的在上面）
def get_all_chats():
    init_chat_list_table()
    conn = sqlite3.connect(get_raw_db_path())
    cursor = conn.cursor()
    cursor.execute("SELECT session_id, chat_title, update_time FROM chat_list ORDER BY update_time DESC")
    rows = cursor.fetchall()
    conn.close()
    
    # 转换为字典列表方便前端使用
    return [{"session_id": r[0], "title": r[1], "time": r[2]} for r in rows]

# 更新对话的最后活跃时间
def update_chat_time(session_id: str):
    conn = sqlite3.connect(get_raw_db_path())
    cursor = conn.cursor()
    cursor.execute("UPDATE chat_list SET update_time = ? WHERE session_id = ?", (get_time(), session_id))
    conn.commit()
    conn.close()


#rag向量库检索





if __name__ == "__main__":
    time = get_time()
    print(time)

    test_id = "xiaohundun_test"
    add_message(test_id, "你好，数据库！", "你好，我是 AI。")
    
    # 验证读取
    history = get_session_history(test_id)
    print("历史记录中的消息：")
    print(history)