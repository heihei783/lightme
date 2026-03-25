from langchain_community.document_loaders import TextLoader,PyPDFLoader,UnstructuredMarkdownLoader,Docx2txtLoader
from langchain_core.prompts import ChatPromptTemplate,MessagesPlaceholder
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.path_tool import get_abs_path
from utils.config_handler import config_ai
import os

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
        
        

#读取知识库中的不同文件
def load_rag_file():
    dir_path = get_abs_path(r"data\rag_file")
    file_path_list = get_file_list(dir_path)
    all_docs = []

    for file in file_path_list:
        if file.endswith(".pdf"):
            all_docs.extend(pdf_loader(file))
        elif file.endswith(".txt"):
            all_docs.extend(txt_loader(file))
        elif file.endswith(".md"):
            all_docs.extend(md_loader(file))
        elif file.endswith(".docx"):
            all_docs.extend(docx_loader(file))
    return all_docs
    


#文本切割
def text_splitter(text:list[Document]):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=config_ai.get("chunk_size",200),
        chunk_overlap=config_ai.get("chunk_overlap",20)
        )
    
    return text_splitter.split_documents(text)
    
#加载知识库并切割
def load_rag_file_and_split():
    all_docs = load_rag_file()
    
    # 过滤掉内容为空或只有空白符的原始文档
    valid_raw_docs = [d for d in all_docs if d.page_content and d.page_content.strip()]
    
    if not valid_raw_docs:
        print("❌ 未发现有效内容")
        return []

    # 进行切分
    splits = text_splitter(valid_raw_docs)
    
    final_splits = []
    for doc in splits:
        clean_content = doc.page_content.strip()
        if clean_content:
            doc.page_content = clean_content
            final_splits.append(doc)
    return final_splits


#创造聊天模型模版
def create_chat_tempt() -> str:
    prompt = txt_loader(get_abs_path(r"app\llm\prompts\chat_prompt.txt"))[0].page_content
    template = ChatPromptTemplate.from_messages(
        [("system",prompt),
        MessagesPlaceholder(variable_name="history_messages"),
        ("human","{input}"),
]
    )
    print(template)
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