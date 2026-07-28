"""
控制台事件发射器 — 将 Agent 内部日志实时推送到前端终端页面。
=================================================================
- emit_log():      普通日志（规划、协作、工具调用等）
- emit_shell():    Shell 审批事件（复用 ShellApprovalManager）
- emit_tool():     工具调用事件
- emit_error():    错误事件
- get_history():   获取历史事件（新客户端回放用）

前端通过 SSE 端点 /console/stream 订阅。
内置环形缓冲区保留最近 500 条事件，新客户端连接时自动回放。
"""
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from app.agent.tools import shell_approval_mgr

# 环形缓冲区最大容量
_MAX_HISTORY = 500


@dataclass
class ConsoleEmitter:
    _listeners: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _history: deque = field(default_factory=lambda: deque(maxlen=_MAX_HISTORY))

    def add_listener(self, callback):
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback):
        with self._lock:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

    def get_history(self) -> list:
        """返回历史事件列表，供新 SSE 客户端回放"""
        with self._lock:
            return list(self._history)

    def _emit(self, event: dict):
        """广播事件给所有 SSE 监听者，同时写入历史缓冲区"""
        with self._lock:
            self._history.append(event)
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(event)
            except Exception:
                pass

    def emit_log(self, sender: str, message: str):
        self._emit({
            "type": "log",
            "sender": sender,
            "message": message,
            "time": time.time(),
        })
        print(f"[{sender}] {message}")

    def emit_tool(self, tool_name: str, args: str = ""):
        self._emit({
            "type": "tool",
            "tool": tool_name,
            "args": args[:200],
            "time": time.time(),
        })
        print(f"[Tool] {tool_name}({args[:120]})")

    def emit_metrics(self, session_id: str, run_id: str, node: str, metrics: dict):
        """Agent runtime metrics for lightweight real-time UI surfaces."""
        self._emit({
            "type": "metrics",
            "session_id": session_id,
            "run_id": run_id,
            "node": node,
            "metrics": metrics,
            "time": time.time(),
        })

    def emit_shell(self, command: str, approval_id: str):
        """Shell 审批事件"""
        self._emit({
            "type": "shell_approval",
            "command": command,
            "approval_id": approval_id,
            "time": time.time(),
        })

    def emit_error(self, sender: str, message: str):
        self._emit({
            "type": "error",
            "sender": sender,
            "message": message,
            "time": time.time(),
        })
        print(f"[ERROR] [{sender}] {message}")


# 全局单例
console = ConsoleEmitter()
