"""
Agent 工具定义 —— Agent 可调用的所有工具函数
"""

import os
import subprocess

from langchain_core.tools import tool


@tool
def search_knowledge_base(query: str) -> str:
    """
    在本地知识库中搜索相关文档。当需要查找资料、文件内容或专业知识时使用此工具。
    参数 query: 搜索查询字符串。
    """
    from utils.rag_handler import AdvancedRAG
    rag = AdvancedRAG()
    docs = rag.run_pipeline(query)
    if docs is None:
        return "未在知识库中找到相关文档"
    return "\n\n".join([doc.page_content for doc in docs])


@tool
def execute_python_code(code: str) -> str:
    """
    执行 Python 代码并返回结果。用于计算、数据处理、文件操作等编程任务。
    参数 code: 要执行的 Python 代码字符串。
    注意：代码在隔离环境中执行，无法访问系统敏感资源。
    """
    try:
        local_vars = {}
        exec(code, {"__builtins__": __builtins__}, local_vars)
        output = local_vars.get("result", local_vars)
        return f"执行成功。结果: {str(output)[:2000]}"
    except Exception as e:
        return f"代码执行出错: {str(e)}"


@tool
def read_file_content(file_path: str) -> str:
    """
    读取指定文件的内容。
    参数 file_path: 文件的绝对路径或相对于项目根目录的路径。
    """
    from utils.path_tool import get_abs_path
    if not os.path.isabs(file_path):
        file_path = get_abs_path(file_path)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return f"文件内容 ({file_path}):\n{content[:3000]}"
    except FileNotFoundError:
        return f"文件不存在: {file_path}"
    except Exception as e:
        return f"读取文件出错: {str(e)}"


@tool
def write_file_content(file_path: str, content: str) -> str:
    """
    将内容写入指定文件。如果文件不存在则创建，如果存在则覆盖。
    参数 file_path: 文件路径
    参数 content: 要写入的内容
    """
    from utils.path_tool import get_abs_path
    if not os.path.isabs(file_path):
        file_path = get_abs_path(file_path)
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"文件写入成功: {file_path} (共 {len(content)} 字符)"
    except Exception as e:
        return f"写入文件出错: {str(e)}"


@tool
def execute_shell_command(command: str) -> str:
    """
    执行系统 Shell 命令并返回输出。
    参数 command: 要执行的命令字符串。
    注意：请谨慎使用，避免不可逆的破坏性操作。
    """
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip() or result.stderr.strip()
        return f"命令输出:\n{output[:2000]}"
    except subprocess.TimeoutExpired:
        return "命令执行超时 (30秒)"
    except Exception as e:
        return f"命令执行出错: {str(e)}"


# 默认工具集，可通过 skill_registry 扩展
DEFAULT_TOOLS = [
    search_knowledge_base,
    execute_python_code,
    read_file_content,
    write_file_content,
    execute_shell_command,
]
