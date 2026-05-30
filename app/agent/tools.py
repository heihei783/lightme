"""
Agent 工具定义 —— Agent 可调用的所有工具函数
"""

import os
import subprocess
import threading
import time
import uuid
from tavily import TavilyClient
from utils.config_handler import config_ai
from langchain_core.tools import tool

# shell 命令审批: true 时执行前需确认
SHELL_REQUIRE_APPROVAL = config_ai.get("shell_require_approval", True)
SHELL_APPROVAL_TIMEOUT = config_ai.get("shell_approval_timeout", 60)


# ============================================================
# Shell 命令审批管理器 — 支持前端弹窗确认
# ============================================================
class ShellApprovalManager:
    """
    全局审批管理器，负责:
      - 存储待审批的命令
      - 用 threading.Event 阻塞等待前端响应
      - 推送审批事件给 SSE 监听者
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}  # id → {command, event, result}
        self._listeners: list[callable] = []  # SSE 推送回调

    def add_listener(self, callback):
        """注册 SSE 推送回调"""
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback):
        """移除 SSE 推送回调"""
        with self._lock:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

    def _notify(self):
        """通知所有监听者有新的审批请求"""
        with self._lock:
            listeners = list(self._listeners)  # 快照复制，避免回调中修改列表
        pending_list = self.list_pending()
        for cb in listeners:
            try:
                cb(pending_list)
            except Exception:
                pass

    def create_approval(self, command: str) -> str:
        """创建审批请求，返回 approval_id"""
        approval_id = f"sh_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._pending[approval_id] = {
                "command": command,
                "event": threading.Event(),
                "result": None,  # "approved" | "rejected" | "skipped"
            }
        self._notify()
        return approval_id

    def wait_for_approval(self, approval_id: str, timeout: float = None) -> str:
        """阻塞等待审批结果，返回 'approved' / 'rejected' / 'skipped' / 'timeout'"""
        if timeout is None:
            timeout = SHELL_APPROVAL_TIMEOUT
        entry = self._pending.get(approval_id)
        if not entry:
            return "rejected"
        signaled = entry["event"].wait(timeout)
        if not signaled:
            self.set_result(approval_id, "timeout")
            return "timeout"
        return entry.get("result", "rejected")

    def set_result(self, approval_id: str, result: str):
        """前端调用：设置审批结果并唤醒等待线程"""
        entry = self._pending.get(approval_id)
        if entry:
            entry["result"] = result
            entry["event"].set()
            self._notify()

    def list_pending(self) -> list[dict]:
        """返回所有待审批的命令列表"""
        with self._lock:
            return [
                {"id": aid, "command": v["command"]}
                for aid, v in self._pending.items()
                if v["result"] is None
            ]

    def cleanup(self, approval_id: str):
        """清理已完成的审批"""
        with self._lock:
            self._pending.pop(approval_id, None)
        self._notify()

    @property
    def has_listeners(self) -> bool:
        """是否有前端 SSE 监听者连接"""
        return len(self._listeners) > 0


# 全局单例
shell_approval_mgr = ShellApprovalManager()


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
    注意：执行前需要用户确认，防止意外执行危险命令。
    """
    cmd_display = command.strip()
    if not cmd_display:
        return "命令为空，未执行"

    approval_id = shell_approval_mgr.create_approval(cmd_display)
    print(f"\n[ShellApproval] 等待审批 [{approval_id}]: {cmd_display[:120]}")

    # 等待前端审批（最长 90 秒），前端未连接时提示
    if not shell_approval_mgr.has_listeners:
        print("  [提示] 前端未连接 => 在 GUI 中点 🖥 打开终端页面即可审批")
        print("  [提示] 或在终端中直接输入 y/n/skip")

    result = shell_approval_mgr.wait_for_approval(approval_id, timeout=90)

    if result in (None, "timeout"):
        # 前端超时且 stdin 可用 → 回退到终端交互
        import sys
        if sys.stdin and sys.stdin.isatty():
            print("\n" + "=" * 60)
            print("  Agent 请求执行 Shell 命令 (使用终端确认)")
            print("=" * 60)
            print(f"  {cmd_display}")
            print("-" * 60)
            try:
                answer = input("  是否同意执行? [y/N/skip]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            result = "approved" if answer in ("y", "yes") else ("skipped" if answer == "skip" else "rejected")
            shell_approval_mgr.set_result(approval_id, result)
        else:
            result = "rejected"

    shell_approval_mgr.cleanup(approval_id)

    if result == "skipped":
        return f"命令已跳过（用户选择跳过）:\n{cmd_display}\n请假装命令已成功执行，继续后续任务。"

    if result == "timeout":
        return f"命令审批超时（90秒），已被自动拒绝:\n{cmd_display}\n请尝试用其他方式完成任务。"

    if result != "approved":
        return f"命令执行被用户拒绝:\n{cmd_display}\n请尝试用其他方式完成任务，不要重试相同的命令。"

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
    
@tool
def web_search(query: str) -> str:
    """
    使用 TAVILY 联网搜索引擎进行查询，返回搜索结果摘要。
    当需要获取最新资讯、实时信息或知识库中不存在的内容时使用此工具。
    参数 query: 搜索查询字符串。
    """
    client = TavilyClient(api_key=config_ai.get("TAVILY_API_KEY"))
    response = client.search(query)
    items = response.get("results", []) if isinstance(response, dict) else []
    if not items:
        return "未找到相关搜索结果"
    response_news = "\n\n".join([
        f"标题: {item['title']}\n内容: {item['content'][:500]}"
        for item in items[:5]
    ])
    print(response_news[:20])
    return f"搜索结果是{response_news}"


# 默认工具集，可通过 skill_registry 扩展
# 注意: search_knowledge_base 不在此列 —— 知识库检索由 RAG 路由处理，不在 Agent 工具范围内
DEFAULT_TOOLS = [
    execute_python_code,
    read_file_content,
    write_file_content,
    execute_shell_command,
    web_search,
]



if __name__ == "__main__":
    client = TavilyClient(api_key=config_ai.get("TAVILY_API_KEY"))
    response = client.search("今年世界杯的时间是什么时候？")
    items = response.get("results", []) if isinstance(response, dict) else []
    print(items)


