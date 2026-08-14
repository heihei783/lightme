"""
Agent 工具定义 —— Agent 可调用的所有工具函数
"""

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from fnmatch import fnmatch
from pathlib import Path
from tavily import TavilyClient
from utils.config_handler import config_ai
from langchain_core.tools import tool

# shell 命令审批: true 时执行前需确认
SHELL_REQUIRE_APPROVAL = config_ai.get("shell_require_approval", True)
SHELL_APPROVAL_TIMEOUT = config_ai.get("shell_approval_timeout", 60)
SHELL_COMMAND_TIMEOUT = int(config_ai.get("shell_command_timeout", 30))
TOOL_OUTPUT_LIMIT = int(config_ai.get("agent_tool_output_limit", 4000))
DANGEROUS_SHELL_PATTERNS = (
    "rm -rf",
    "del /s",
    "format ",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "reg delete",
    "remove-item -recurse",
    "remove-item -force -recurse",
)


def _resolve_path(path: str | None = None) -> str:
    """Resolve project-relative paths while preserving absolute paths."""
    from utils.path_tool import get_abs_path

    clean_path = (path or ".").strip() or "."
    if os.path.isabs(clean_path):
        return os.path.abspath(clean_path)
    return os.path.abspath(get_abs_path(clean_path))


def _truncate(text: str, limit: int = TOOL_OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [已截断，原始长度 {len(text)} 字符]"


def _format_error(action: str, error: Exception) -> str:
    return f"{action}失败: {type(error).__name__}: {error}"


def _is_dangerous_shell_command(command: str) -> bool:
    lowered = " ".join(command.lower().split())
    return any(pattern in lowered for pattern in DANGEROUS_SHELL_PATTERNS)


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

_knowledge_rag = None
_knowledge_rag_lock = threading.Lock()


def _get_knowledge_rag():
    global _knowledge_rag
    if _knowledge_rag is None:
        with _knowledge_rag_lock:
            if _knowledge_rag is None:
                from utils.rag_handler import AdvancedRAG

                _knowledge_rag = AdvancedRAG()
    return _knowledge_rag


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
def list_directory(path: str = ".", max_items: int = 100) -> str:
    """
    列出目录内容，返回文件名、类型、大小和修改时间。
    参数 path: 目录路径，支持项目相对路径或绝对路径。
    参数 max_items: 最多返回条目数，默认 100。
    """
    try:
        target = _resolve_path(path)
        if not os.path.exists(target):
            return f"目录不存在: {target}"
        if not os.path.isdir(target):
            return f"路径不是目录: {target}"

        entries = []
        for item in sorted(Path(target).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            stat = item.stat()
            item_type = "dir" if item.is_dir() else "file"
            size = "-" if item.is_dir() else str(stat.st_size)
            modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
            entries.append(f"{item_type}\t{size}\t{modified}\t{item.name}")
            if len(entries) >= max(1, max_items):
                break

        header = f"目录: {target}\n条目数: {len(entries)}"
        return _truncate(header + "\n" + "\n".join(entries))
    except Exception as e:
        return _format_error("列出目录", e)


@tool
def search_files(pattern: str, root: str = ".", max_results: int = 100) -> str:
    """
    按文件名模式搜索文件和目录。
    参数 pattern: 文件名模式，如 *.py、config_*.yaml 或 README.md。
    参数 root: 搜索根目录，支持项目相对路径或绝对路径。
    参数 max_results: 最多返回结果数，默认 100。
    """
    try:
        if not pattern.strip():
            return "搜索模式为空，未执行"
        search_root = _resolve_path(root)
        if not os.path.isdir(search_root):
            return f"搜索根目录不存在或不是目录: {search_root}"

        results = []
        lowered_pattern = pattern.lower()
        for current_root, dirs, files in os.walk(search_root):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", ".ruff_cache"}]
            names = [(name, "dir") for name in dirs] + [(name, "file") for name in files]
            for name, item_type in names:
                if fnmatch(name.lower(), lowered_pattern) or lowered_pattern in name.lower():
                    full_path = os.path.join(current_root, name)
                    results.append(f"{item_type}\t{full_path}")
                    if len(results) >= max(1, max_results):
                        header = f"搜索根目录: {search_root}\n模式: {pattern}\n结果数: {len(results)}"
                        return _truncate(header + "\n" + "\n".join(results))

        header = f"搜索根目录: {search_root}\n模式: {pattern}\n结果数: {len(results)}"
        return _truncate(header + ("\n" + "\n".join(results) if results else "\n未找到匹配项"))
    except Exception as e:
        return _format_error("搜索文件", e)


@tool
def get_file_info(path: str) -> str:
    """
    获取文件或目录的元信息。
    参数 path: 文件或目录路径，支持项目相对路径或绝对路径。
    """
    try:
        target = _resolve_path(path)
        if not os.path.exists(target):
            return f"路径不存在: {target}"
        stat = os.stat(target)
        info = {
            "path": target,
            "type": "directory" if os.path.isdir(target) else "file",
            "size_bytes": stat.st_size,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)),
            "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "readable": os.access(target, os.R_OK),
            "writable": os.access(target, os.W_OK),
        }
        return "\n".join(f"{key}: {value}" for key, value in info.items())
    except Exception as e:
        return _format_error("获取文件信息", e)


@tool
def copy_file(src: str, dst: str, overwrite: bool = False) -> str:
    """
    复制文件。
    参数 src: 源文件路径。
    参数 dst: 目标文件路径；如果是目录，则复制到该目录内。
    参数 overwrite: 目标存在时是否覆盖，默认 false。
    """
    try:
        source = _resolve_path(src)
        target = _resolve_path(dst)
        if not os.path.isfile(source):
            return f"源文件不存在或不是文件: {source}"
        if os.path.isdir(target):
            target = os.path.join(target, os.path.basename(source))
        if os.path.exists(target) and not overwrite:
            return f"目标已存在，未覆盖: {target}"
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
        return f"文件复制成功:\n源: {source}\n目标: {target}"
    except Exception as e:
        return _format_error("复制文件", e)


@tool
def move_file(src: str, dst: str, overwrite: bool = False) -> str:
    """
    移动或重命名文件/目录。
    参数 src: 源路径。
    参数 dst: 目标路径。
    参数 overwrite: 目标存在时是否覆盖，默认 false。
    """
    try:
        source = _resolve_path(src)
        target = _resolve_path(dst)
        if not os.path.exists(source):
            return f"源路径不存在: {source}"
        if os.path.isdir(target):
            target = os.path.join(target, os.path.basename(source))
        if os.path.exists(target):
            if not overwrite:
                return f"目标已存在，未覆盖: {target}"
            if os.path.isdir(target):
                return f"目标是目录，为避免误删未覆盖: {target}"
            os.remove(target)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(source, target)
        return f"移动成功:\n源: {source}\n目标: {target}"
    except Exception as e:
        return _format_error("移动文件", e)


@tool
def make_directory(path: str, exist_ok: bool = True) -> str:
    """
    创建目录。
    参数 path: 要创建的目录路径。
    参数 exist_ok: 目录已存在时是否视为成功，默认 true。
    """
    try:
        target = _resolve_path(path)
        os.makedirs(target, exist_ok=exist_ok)
        return f"目录已创建或已存在: {target}"
    except Exception as e:
        return _format_error("创建目录", e)


@tool
def open_path(path: str) -> str:
    """
    使用系统默认程序打开文件或目录。
    参数 path: 文件或目录路径。
    """
    try:
        target = _resolve_path(path)
        if not os.path.exists(target):
            return f"路径不存在，无法打开: {target}"
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, target])
        return f"已请求系统打开: {target}"
    except Exception as e:
        return _format_error("打开路径", e)


@tool
def open_url(url: str) -> str:
    """
    使用默认浏览器打开 URL。
    参数 url: 要打开的网址。
    """
    try:
        clean_url = url.strip()
        if not clean_url:
            return "URL 为空，未打开"
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url
        opened = webbrowser.open(clean_url)
        return f"已请求浏览器打开: {clean_url}" if opened else f"浏览器打开请求失败: {clean_url}"
    except Exception as e:
        return _format_error("打开 URL", e)


@tool
def list_processes(name_filter: str = "", max_results: int = 50) -> str:
    """
    列出当前进程。Windows 下使用 tasklist，其他系统使用 ps。
    参数 name_filter: 可选进程名过滤关键词。
    参数 max_results: 最多返回行数，默认 50。
    """
    try:
        if os.name == "nt":
            result = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=10)
        else:
            result = subprocess.run(["ps", "-eo", "pid,comm,%cpu,%mem"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.splitlines()
        if name_filter:
            needle = name_filter.lower()
            header = lines[:3] if os.name == "nt" else lines[:1]
            body = [line for line in lines[len(header):] if needle in line.lower()]
            lines = header + body
        limited = lines[:max(1, max_results)]
        return _truncate("\n".join(limited) if limited else "未找到进程")
    except Exception as e:
        return _format_error("列出进程", e)


@tool
def start_app(command: str) -> str:
    """
    启动本机应用或命令。
    参数 command: 应用路径或启动命令，例如 notepad、calc、绝对路径。
    """
    try:
        clean_command = command.strip()
        if not clean_command:
            return "启动命令为空，未执行"
        subprocess.Popen(clean_command, shell=True)
        return f"已请求启动: {clean_command}"
    except Exception as e:
        return _format_error("启动应用", e)


@tool
def get_system_info() -> str:
    """
    获取当前操作系统和 Python 运行环境信息。
    """
    try:
        info = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "cwd": os.getcwd(),
        }
        return "\n".join(f"{key}: {value}" for key, value in info.items())
    except Exception as e:
        return _format_error("获取系统信息", e)


@tool
def get_disk_usage(path: str = ".") -> str:
    """
    获取指定路径所在磁盘的空间使用情况。
    参数 path: 文件或目录路径，默认项目根目录。
    """
    try:
        target = _resolve_path(path)
        usage = shutil.disk_usage(target)
        gib = 1024 ** 3
        return (
            f"path: {target}\n"
            f"total_gb: {usage.total / gib:.2f}\n"
            f"used_gb: {usage.used / gib:.2f}\n"
            f"free_gb: {usage.free / gib:.2f}"
        )
    except Exception as e:
        return _format_error("获取磁盘信息", e)


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
    if _is_dangerous_shell_command(cmd_display):
        return f"命令包含高风险操作，已拒绝执行:\n{cmd_display}"

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
            command, shell=True, capture_output=True, text=True, timeout=SHELL_COMMAND_TIMEOUT
        )
        stdout = _truncate(result.stdout.strip())
        stderr = _truncate(result.stderr.strip())
        return (
            f"exit_code: {result.returncode}\n"
            f"stdout:\n{stdout or '(empty)'}\n\n"
            f"stderr:\n{stderr or '(empty)'}"
        )
    except subprocess.TimeoutExpired:
        return f"命令执行超时 ({SHELL_COMMAND_TIMEOUT}秒)"
    except Exception as e:
        return f"命令执行出错: {str(e)}"
    
@tool
def knowledge_search(query: str) -> str:
    """
    检索 LightMe 本地知识库，返回相关父文档片段和来源。
    适用于用户询问已上传文档、项目知识或明确要求查询知识库的任务。
    参数 query: 需要在知识库中检索的问题或关键词。
    """
    if not config_ai.get("rag_open", False):
        return "知识库检索当前未启用，请在设置中开启 RAG。"
    try:
        docs = _get_knowledge_rag().hierarchical_search(query)
        if not docs:
            return "知识库中未找到相关内容。"
        sections = []
        for index, doc in enumerate(docs[:5], 1):
            metadata = getattr(doc, "metadata", {}) or {}
            source = metadata.get("source") or metadata.get("file_name") or metadata.get("title") or "knowledge_base"
            content = str(getattr(doc, "page_content", "") or "").strip()
            sections.append(f"[{index}] 来源: {source}\n内容: {content[:900]}")
        return _truncate("知识库检索结果:\n\n" + "\n\n".join(sections))
    except Exception as e:
        return _format_error("知识库检索", e)


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
DEFAULT_TOOLS = [
    knowledge_search,
    execute_python_code,
    read_file_content,
    write_file_content,
    list_directory,
    search_files,
    get_file_info,
    copy_file,
    move_file,
    make_directory,
    open_path,
    open_url,
    list_processes,
    start_app,
    get_system_info,
    get_disk_usage,
    execute_shell_command,
    web_search,
]



if __name__ == "__main__":
    client = TavilyClient(api_key=config_ai.get("TAVILY_API_KEY"))
    response = client.search("今年世界杯的时间是什么时候？")
    items = response.get("results", []) if isinstance(response, dict) else []
    print(items)


