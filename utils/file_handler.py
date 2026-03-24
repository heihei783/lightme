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
        elif file.endswith(".md"):
            all_docs.extend(txt_loader(file))
        elif file.endswith(".md"):
            all_docs.extend(md_loader(file))
        elif file.endswith(".docx"):
            all_docs.extend(docx_loader(file))
    return all_docs
    


#文本切割
def text_splitter(text:str) -> list[str]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=config_ai.get("chunk_size",200),
        chunk_overlap=config_ai.get("chunk_overlap",20)
        )
    return text_splitter.split_text(text)
    



#创造聊天模型模版
def create_tempt(path:str) -> str:
    prompt = txt_loader(path)[0].page_content
    template = ChatPromptTemplate.from_messages(
        [("system",prompt),
        MessagesPlaceholder(variable_name="history_messages"),
        ("human","{input}")
]
    )
    return template
    

chat_prompt = create_tempt(r"app\llm\prompts\chat_prompt.txt")
rag_prompt = create_tempt(r"app\llm\prompts\rag_prompt.txt")



if __name__ == "__main__":
    docs = create_tempt(r"app\llm\prompts\chat_prompt.txt")
    print(docs)