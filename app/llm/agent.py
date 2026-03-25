from langchain_core.tools import tool
from utils.db_handler import rag_search 
from langchain.agents import create_agent
from app.llm.chat_model import chat_model
from utils.file_handler import agent_prompt,txt_loader
from utils.path_tool import get_abs_path



@tool
def search_knowledge_base(query: str,description="检索知识库") -> str:
    """
    在这里检索知识库
    """
    
    docs = rag_search(query) 
    return "\n\n".join([doc.page_content for doc in docs])

tools = [search_knowledge_base]


#创建智能体
def init_agent():
    chat_prompt = txt_loader(get_abs_path(r"app\llm\prompts\chat_prompt.txt"))[0].page_content
    conbine_prompt ="人格提示词："+ chat_prompt + "工具提示词：" + agent_prompt
    agent = create_agent(
        model=chat_model, 
        tools=tools, 
        system_prompt=agent_prompt)
    return agent

agent = init_agent()


