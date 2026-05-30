import ntplib
import sqlite3
import uuid
import shutil
import os
import json
from datetime import datetime
from langchain_core.documents import Document
from langchain_community.storage import SQLStore
from utils.path_tool import get_abs_path
from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_chroma import Chroma
from utils.config_handler import config_ai
from app.llm.embed_model import embedding
from utils.file_handler import is_file_exist, child_splitter, parent_splitter,get_file_doc
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_classic.storage import EncoderBackedStore



# 数据库文件路径
DB_URL = f"sqlite:///{get_abs_path('data/chat_history.db').replace('\\', '/')}"
VECTOR_DB = get_abs_path("data/vector_db")
DB_PATH = get_abs_path("data/chat_history.db")
PARENT_DB = get_abs_path("data/parent_document.db")
HASH_FILE_PATH = get_abs_path("data/file_hash.txt")

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


# 1. 专门负责获取"管理对象" (也就是那个能操作数据库的"遥控器")
def get_history_obj(session_id: str):
    return SQLChatMessageHistory(session_id=session_id, connection_string=DB_URL)


# 2. 专门负责获取"给 AI 看的消息列表" (截断后的数据)
def get_session_history(session_id: str):
    history_obj = get_history_obj(session_id)
    limit = config_ai.get("chat_history_len", 10)
    # 返回的是 List[BaseMessage]
    return history_obj.messages[-limit:]


# 3. 向数据库添加历史记录
def add_message(session_id: str, message: str, response_text: str):
    time_str = get_time()
    history_obj = get_history_obj(session_id)

    history_obj.add_user_message(f"[{time_str}] {message}")
    history_obj.add_ai_message(f"[{time_str}] {response_text}")

    # 同步更新会话列表的活跃时间
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE chat_list SET update_time = ? WHERE session_id = ?", (time_str, session_id))
    conn.commit()
    conn.close()


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
    cursor.execute("""CREATE TABLE IF NOT EXISTS chat_list (
        session_id TEXT PRIMARY KEY,
        chat_title TEXT,
        create_time TEXT,
        update_time TEXT
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


#  获取列表逻辑
def get_all_chats() -> list[dict]:
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

class ParentDocument:
    # 存储父类文本信息并初始化检索器
    def __init__(self):
        raw_storage = SQLStore(db_url=f"sqlite:///{PARENT_DB}", namespace="parent_docs")
        raw_storage.create_schema()
        self.vector_db = Chroma(persist_directory=VECTOR_DB, embedding_function=embedding)
        self.store = SQLStore(db_url=f"sqlite:///{PARENT_DB}",namespace="parent_docs")
        self.store.create_schema()
        self.store = EncoderBackedStore(
        store=raw_storage,
        key_encoder=lambda k: k,
        value_serializer=lambda v: json.dumps(v.dict()).encode('utf-8'),
        value_deserializer=lambda v: Document(**json.loads(v.decode('utf-8')))
    )
        
        self.retriever = self.get_retriever()
    def get_retriever(self):
        retriever = ParentDocumentRetriever(
            vectorstore=self.vector_db,
            docstore=self.store,
            child_splitter=child_splitter(),
            parent_splitter=parent_splitter(),
        )
        return retriever

    # 存入知识库的函数
    def save_to_rag(self, new_file_path: str):
        # 1. 依然使用你的哈希校验
        weather_exist, file_hash, file_path = is_file_exist(new_file_path)
        if weather_exist:
            print("⚠️ 该文件已存在于知识库中，跳过入库喵！")
            return
        
        # 2. 获取 retriever
        retriever = self.get_retriever()
        
        # 3. 加载原始文档（不要手动切割！）
        raw_docs = get_file_doc(new_file_path) # 这里只需返回未切割的 Document 列表
        if not raw_docs or len(raw_docs[0].page_content.strip()) == 0:
            print("❌ 错误：读取到的文件内容为空，请检查文件路径或内容喵！")
            return
        try:
            # 4. 【核心改动】交给 retriever 统一处理父子切割和存储
            retriever.add_documents(raw_docs)
            
            # 记录哈希
            with open("data/file_hash.txt", "a") as f:
                f.write(file_hash + "\n")
            print("父子索引知识库构建成功")
        except Exception as e:
            print(f"保存失败: {e}")

    # 清空知识库


    def reset_rag_system():
        print("🚀 开始清理 RAG 系统数据...")

        # 1. 清理向量数据库 (Chroma)
        if os.path.exists(VECTOR_DB):
            try:
                # Chroma 建议直接物理删除文件夹最彻底
                shutil.rmtree(VECTOR_DB)
                print(f"✅ 已删除向量库文件夹: {VECTOR_DB}")
            except Exception as e:
                print(f"❌ 删除向量库失败 (可能文件被占用): {e}")
        
        # 2. 清理父类数据库 (SQLite)
        if os.path.exists(PARENT_DB):
            try:
                os.remove(PARENT_DB)
                print(f"✅ 已删除父类数据库文件: {PARENT_DB}")
            except Exception as e:
                print(f"❌ 删除父类数据库失败: {e}")

        # 3. 重置哈希记录
        if os.path.exists(HASH_FILE_PATH):
            try:
                os.remove(HASH_FILE_PATH)
                print(f"✅ 已重置哈希记录文件: {HASH_FILE_PATH}")
            except Exception as e:
                print(f"❌ 重置哈希记录失败: {e}")

        print("\n✨ 清理完成！现在你可以重新运行入库脚本了喵~")

if __name__ == "__main__":
    rag_storage = ParentDocument()
    rag_storage.save_to_rag(get_abs_path(r"data/rag_file/如何成为galgame高手.txt"))
    # reset_rag_system()
