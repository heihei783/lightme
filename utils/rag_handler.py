from app.llm.chat_model import chat_model
from utils.path_tool import get_abs_path
from utils.file_handler import config_ai
from app.llm.embed_model import embedding
from utils.db_handler import ParentDocument


"""Context Enrichment (父子索引/上下文增强)，
Query Transformation (查询转换)，
Query Router (查询路由)，
Hierarchical Index (层次索引)"""


import uuid
from langchain_community.vectorstores import Chroma
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_core.stores import InMemoryStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

class AdvancedRAG:
    def __init__(self):
        parentdocument = ParentDocument()
        self.retriever = parentdocument.retriever
        self.vector_db = parentdocument.vector_db
        self.store = parentdocument.store

    def query_router(self, question: str) -> str:
        """功能 3: 查询路由 (Query Router)"""
        # 让 LLM 判断：'search' (需要RAG) 或 'chat' (直接闲聊)
        prompt = f"判断以下用户意图，只返回单词 'search' 或 'chat'：\n{question}"
        decision = chat_model.invoke(prompt).content.strip().lower()
        print(f"用户的意图是：{decision}")
        return "search" if "search" in decision else "chat"

    def query_transform(self, question: str) -> list[str]:
        """功能 2: 查询转换 (Query Transformation)"""
        # 让 LLM 生成多个变体问题
        prompt = f"请将问题 '{question}' 改写为 3 个意思相近的搜索关键词，每行一个。"
        response = chat_model.invoke(prompt)
        variants = [q.strip() for q in response.content.split("\n") if q.strip()]
        print(f"我的问题是：{question}，查询转至的三个问题是：{variants}")
        return [question] + variants

    def hierarchical_search(self, question: str):
        """功能 1 & 4: 层次索引 + 上下文增强检索"""
        # 这里集成父子索引逻辑
        # retriever 会自动：1. 搜索子块 2. 找到对应的父块 3. 返回完整的父块上下文
        docs = self.retriever.invoke(question)
        return docs

    def run_pipeline(self, question: str):
        """完整的进阶 RAG 流水线（含内部路由决策）"""

        # 第一步：路由决策
        if self.query_router(question) == "chat":
            return None # 走普通对话逻辑

        # 第二步：查询转换（把 1 个问题变 4 个）
        all_queries = self.query_transform(question)

        # 第三步：层次化检索 (Hierarchical + Enrichment)
        final_docs = []
        for q in all_queries:
            # 这里的检索会自动返回更有逻辑的”父块”
            docs = self.hierarchical_search(q)
            final_docs.extend(docs)

        # 第四步：去重
        unique_docs = {doc.page_content: doc for doc in final_docs}.values()
        return list(unique_docs)

    def search_only(self, question: str):
        """跳过内部 query_router，直接执行查询转换 + 层次检索
        用于主路由已确定走 RAG 时，避免重复的意图判断"""
        all_queries = self.query_transform(question)
        final_docs = []
        for q in all_queries:
            docs = self.hierarchical_search(q)
            final_docs.extend(docs)
        unique_docs = {doc.page_content: doc for doc in final_docs}.values()
        return list(unique_docs)




if __name__ == "__main__":
    rag = AdvancedRAG()
    docs = rag.run_pipeline("我该哪里去找galgame资源？通过向量检索给我。")
    print(docs)
    docs = rag.run_pipeline("我喜欢你呀！")
    print(docs)




# 最初的索引，没舍得删。向量知识库搜索
# def rag_search(question: str):
#     vector_db = Chroma(
#         persist_directory=get_abs_path("data/vector_db"), embedding_function=embedding
#     )

#     # 搜索前 5 个最相关的片段
#     docs = vector_db.similarity_search(question, k=config_ai.get("top_k", 5))
#     print(docs)
#     return docs
