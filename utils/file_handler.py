from langchain_community.document_loaders import TextLoader,PyPDFLoader,UnstructuredMarkdownLoader,Docx2txtLoader
from langchain_core.prompts import ChatPromptTemplate,MessagesPlaceholder
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.path_tool import get_abs_path
from utils.config_handler import config_ai
import os
import hashlib

#加载不同的文本
def pdf_loader(filepath:str,passwd:str = None) -> list[Document]:
    return PyPDFLoader(filepath,passwd).load()
    

def txt_loader(filepath:str ) -> list[Document]:
    return TextLoader(filepath,encoding="utf-8").load()


def md_loader(filepath:str) -> list[Document]:
    return UnstructuredMarkdownLoader(filepath,encoding="utf-8").load()


def docx_loader(filepath:str) -> list[Document]:
    return Docx2txtLoader(filepath).load()


#得到当前文件夹内部的文件列表
def get_file_list(dir_path:str) -> list[str]:
    file_path_list = []
    for root, dirs, files in os.walk(dir_path):
        for file in files:
            file_path = os.path.join(root, file)
            file_path_list.append(file_path)
    return file_path_list
        
        
    

#文本切割
def text_splitter(text:list[Document]):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=config_ai.get("chunk_size",200),
        chunk_overlap=config_ai.get("chunk_overlap",20)
        )
    
    return text_splitter.split_documents(text)

#判断文件类型
def get_file_type(file_path:str) -> str:
    if file_path.endswith(".pdf"):
        return pdf_loader(file_path)
    elif file_path.endswith(".txt"):
        return txt_loader(file_path)
    elif file_path.endswith(".md"):
        return md_loader(file_path)
    elif file_path.endswith(".docx"):
        return docx_loader(file_path)


#加载知识库并切割
def load_rag_file(file_path:str):
    valid_raw_docs = get_file_type(file_path)

    # 进行切分
    splits = text_splitter(valid_raw_docs)
    
    final_splits = []
    for doc in splits:
        clean_content = doc.page_content.strip()
        if clean_content:
            doc.page_content = clean_content
            final_splits.append(doc)
    return final_splits


#计算哈希值
def is_file_exist(new_file_path, hash_list_path="data/file_hash.txt"):
    """
    检查新上传的文件内容是否已存在于账本中。
    如果不存在，则将新哈希追加到账本。
    """
    # 1. 计算上传文件的哈希值
    sha256_hash = hashlib.sha256()
    with open(new_file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    current_hash = sha256_hash.hexdigest()

    # 2. 读取账本（如果账本不存在则创建）
    if not os.path.exists(hash_list_path):
        os.makedirs(os.path.dirname(hash_list_path), exist_ok=True)
        with open(hash_list_path, "w") as f: pass

    with open(hash_list_path, "r") as f:
        # 使用 set 提高查询效率
        recorded_hashes = set(line.strip() for line in f.readlines())

    # 3. 对比与追加
    if current_hash in recorded_hashes:
        return True, current_hash,new_file_path  # 返回 True 表示已存在（重复）
    else:
        return False, current_hash,new_file_path # 返回 False 表示是新文件


#创造聊天模型模版
def create_chat_tempt() -> str:
    prompt = txt_loader(get_abs_path(r"app\llm\prompts\chat_prompt.txt"))[0].page_content
    template = ChatPromptTemplate.from_messages(
        [("system",prompt),
        MessagesPlaceholder(variable_name="history_messages"),
        ("human","{input}"),
        ]
        )
    return template

#创造rag模型模版
def create_rag_tempt() -> str:
    prompt = txt_loader(get_abs_path(r"app\llm\prompts\rag_prompt.txt"))[0].page_content
    template = ChatPromptTemplate.from_messages(
        [("system",prompt),
        MessagesPlaceholder(variable_name="history_messages"),
        ("human","{input}")
]
    )
    return template

#创造agent模型提示词
def create_agent_tempt() -> str:
    prompt = txt_loader(get_abs_path(r"app\llm\prompts\agent_prompt.txt"))[0].page_content

    return prompt
    

chat_prompt = create_chat_tempt()
rag_prompt = create_rag_tempt()
agent_prompt = create_agent_tempt()


if __name__ == "__main__":
    docs = create_chat_tempt()
    print(docs)