import ntplib
import sqlite3
import uuid
import shutil
import os
from datetime import datetime
from utils.path_tool import get_abs_path
from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_chroma import Chroma
from utils.config_handler import config_ai
from app.llm.rag_model import embedding
from utils.file_handler import load_rag_file, is_file_exist, get_file_list


# 数据库文件路径
DB_URL = f"sqlite:///{get_abs_path('data/chat_history.db').replace('\\', '/')}"
VECTOR_DB = get_abs_path("data/vector_db")
DB_PATH = get_abs_path("data/chat_history.db")


# 获取时间
def get_time() -> str:
    try:
        client = ntplib.NTPClient()
        # 请求阿里云的时间服务器
        response = client.request("ntp.aliyun.com", version=3)
        return datetime.fromtimestamp(response.tx_time).strftime("%Y-%m-%d %H:%M:%S")
    except:
        # 如果断网了，再退回到系统时间
        print("----互联网时间获取失败，正在获取本地时间")
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 1. 专门负责获取“管理对象” (也就是那个能操作数据库的“遥控器”)
def get_history_obj(session_id: str):
    return SQLChatMessageHistory(session_id=session_id, connection_string=DB_URL)


# 2. 专门负责获取“给 AI 看的消息列表” (截断后的数据)
def get_session_history(session_id: str):
    history_obj = get_history_obj(session_id)
    limit = config_ai.get("chat_history_len", 10)
    # 返回的是 List[BaseMessage]
    return history_obj.messages[-limit:]


# 3. 向数据库添加历史记录
def add_message(session_id: str, message: str, response_text: str):
    time_str = get_time()
    # 关键：这里必须获取“对象”，而不是“列表”
    history_obj = get_history_obj(session_id)

    # 使用对象的方法存入数据库
    history_obj.add_user_message(f"[{time_str}] {message}")
    history_obj.add_ai_message(f"[{time_str}] {response_text}")


# 4. 清空会话的历史记录
def clear_session(session_id: str):
    history_obj = get_history_obj(session_id)
    # 这个内置方法会删除数据库中该 session_id 对应的所有行
    history_obj.clear()
    print(f"已清空会话 {session_id} 的所有历史记录")


# ------------------------用户不同对话逻辑-------------------------


def init_chat_list_table():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 你的会话列表表
    cursor.execute("""CREATE TABLE IF NOT EXISTS chat_list (
        session_id TEXT PRIMARY KEY, 
        chat_title TEXT, 
        create_time TEXT, 
        update_time TEXT
    )""")
    # 存储具体聊天内容的表
    cursor.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        role TEXT,
        content TEXT,
        timestamp TEXT,
        FOREIGN KEY (session_id) REFERENCES chat_list (session_id)
    )""")
    conn.commit()
    conn.close()


# 🌟 你写的创建会话逻辑
def create_new_chat(first_question: str = "新对话"):
    init_chat_list_table()
    new_id = f"chat_{uuid.uuid4().hex[:8]}"
    time_str = get_time()
    title = first_question[:15] + "..." if len(first_question) > 15 else first_question

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_list (session_id, chat_title, create_time, update_time) VALUES (?, ?, ?, ?)",
        (new_id, title, time_str, time_str),
    )
    conn.commit()
    conn.close()
    return new_id


# 🌟 你写的获取列表逻辑
def get_all_chats():
    init_chat_list_table()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT session_id, chat_title, update_time FROM chat_list ORDER BY update_time DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    # [修改] 返回 id 而非 session_id，以匹配前端字段名
    return [{"id": r[0], "title": r[1], "time": r[2]} for r in rows]


# 🌟 获取历史记录
def get_messages_by_sid(session_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
        (session_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]


# 🌟 存储单条消息并更新时间
def save_message_and_update(session_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, get_time()),
    )
    # 更新会话最后活跃时间喵！
    cursor.execute(
        "UPDATE chat_list SET update_time = ? WHERE session_id = ?",
        (get_time(), session_id),
    )
    conn.commit()
    conn.close()

def delete_chat_list(session_id: str):
    conn = None
    try:
        db_path = get_abs_path('data/chat_history.db')
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # 删除会话列表中的索引 (这是左侧列表的数据源)
        c.execute("DELETE FROM chat_list WHERE session_id = ?", (session_id,))
        print(f"(🐾) 已从列表移除会话: {session_id}")

        # 清理内存中的历史对象缓存 (防止 LangChain 还在内存里记着它)
        try:
            history = get_history_obj(session_id)
            history.clear()
        except:
            pass

        conn.commit()
        return True

    except Exception as e:
        print(f"(❌) 抹除失败惹: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


# ------------------------------------------------------------------------


# 存入向量知识库
def save_vector_db(new_file_path: str):
    weather_exist, file_hash, file_path = is_file_exist(
        new_file_path, hash_list_path="data/file_hash.txt"
    )
    if weather_exist:
        print("文件已存在，无需重复保存")
        return
    try:
        Chroma.from_documents(
            documents=load_rag_file(file_path),
            embedding=embedding,
            persist_directory=VECTOR_DB,  # 存到本地硬盘，下次不用重新加载
        )
        with open("data/file_hash.txt", "a") as f:
            f.write(file_hash + "\n")
        print("向量知识库保存成功")
    except Exception as e:
        print(f"向量知识库保存失败: {e}")


# 向量知识库搜索
def rag_search(question: str):
    vector_db = Chroma(
        persist_directory=get_abs_path("data/vector_db"), embedding_function=embedding
    )

    # 搜索前 5 个最相关的片段
    docs = vector_db.similarity_search(question, k=config_ai.get("top_k", 5))
    print(docs)
    return docs


# 清空知识库


def reset_knowledge_base():
    """
    全清函数：抹除向量库、清空哈希账本、删除原始 RAG 文件
    """
    print("(🐾) 正在执行全清程序，请稍候喵...")

    # 1. 定义相关路径
    vector_db_path = get_abs_path("data/vector_db")
    hash_list_path = get_abs_path("data/file_hash.txt")
    rag_files_dir = get_abs_path("data/rag_file")

    try:
        # 清空向量库目录
        if os.path.exists(vector_db_path):
            # 删除整个文件夹来确保彻底清空
            shutil.rmtree(vector_db_path)
            os.makedirs(vector_db_path, exist_ok=True)

        # 重置哈希账本
        if os.path.exists(hash_list_path):
            with open(hash_list_path, "w", encoding="utf-8") as f:
                f.truncate(0)

        # 清理原始上传文件
        if os.path.exists(rag_files_dir):
            for filename in os.listdir(rag_files_dir):
                file_path = os.path.join(rag_files_dir, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)

        print("\n✨所有知识记忆已全部重置，我现在啥也不懂啦~")
        return True

    except Exception as e:
        print(f"❌ 呜呜...清理过程中出错了: {e}")
        return False


if __name__ == "__main__":
    clear_session("xiaohundun_test")
    clear_session("chat_27436410")
    clear_session("session_1774948387447")
