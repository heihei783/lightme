from langchain_community.document_loaders import TextLoader
from langchain_core.prompts import ChatPromptTemplate,MessagesPlaceholder
from utils.path_tool import get_abs_path


def loader_text(path:str) -> str:
    loader = TextLoader(get_abs_path(path),encoding="utf-8")
    docs = loader.load()
    return docs[0].page_content

def create_tempt(path:str) -> str:
    prompt = loader_text(path)
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