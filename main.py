"""
LightMe 启动入口
================
多线程架构:
  - Backend Thread:  FastAPI 服务 (AI 问答 / Agent / RAG / 数据库)
  - GUI Thread:      pywebview 桌面窗口 (前端访问后端) — 主线程
  - Web 模式:        后端 + 自动打开系统浏览器

用法:
  python main.py              → 后端 + GUI 桌面窗口 (默认)
  python main.py --web        → 后端 + 自动打开浏览器
  python main.py --server     → 仅后端 (适合服务器部署)
  python main.py --port 9000  → 指定端口启动
"""

import argparse
import signal
import threading
import time
import traceback
import webbrowser

import uvicorn

from web.web_py import app


# ============================================================
# 全局控制
# ============================================================

_stop_event = threading.Event()


def _handle_exit_signal(signum, frame):
    """Ctrl+C / SIGTERM → 通知所有线程退出"""
    name = signal.Signals(signum).name if hasattr(signal, "Signals") else signum
    print(f"\n[Main] 收到信号 {name}，正在优雅退出...")
    _stop_event.set()


signal.signal(signal.SIGINT, _handle_exit_signal)
signal.signal(signal.SIGTERM, _handle_exit_signal)


# ============================================================
# Thread 1: FastAPI 后端服务
# ============================================================

def start_backend(host: str = "127.0.0.1", port: int = 8000):
    """
    启动 FastAPI 后端服务。

    提供的功能:
      - /chat          AI 对话 & Agent 自主执行 (流式)
      - /sessions      会话列表管理
      - /history/{id}  历史消息查询
      - /config        模型配置 CRUD
      - /rag/*         知识库文件上传/管理
      - /tts           文字转语音
      - /image-gen     AI 图片生成
      - /tools-and-skills  可用工具 & 技能列表
    """
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    print(f"[Backend] FastAPI 启动 → http://{host}:{port}")
    try:
        server.run()
    except Exception:
        if not _stop_event.is_set():
            traceback.print_exc()


# ============================================================
# Thread 2: GUI 桌面窗口 (pywebview)
# ============================================================

def start_gui(url: str = "http://127.0.0.1:8000/web/html/index.html"):
    """
    启动桌面 GUI 窗口。

    内嵌后端页面，用户无需打开浏览器即可使用。
    """
    try:
        import webview
    except ImportError:
        print("[GUI] pywebview 未安装，跳过 GUI 启动。使用 --server 模式仅启动后端。")
        return

    print(f"[GUI] 桌面窗口启动 → {url}")

    window = webview.create_window(
        title="LightMe AI 终端",
        url=url,
        width=1100,
        height=750,
        resizable=True,
        min_size=(800, 600),
    )

    # webview.start() 是阻塞调用，会接管当前线程的事件循环
    # 当窗口关闭时自动返回
    webview.start(debug=False)

    # 窗口关闭 → 通知其他线程退出
    print("[GUI] 窗口已关闭")
    _stop_event.set()



# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="LightMe AI — 多线程 Agent 系统启动入口")
    parser.add_argument(
        "--web", action="store_true",
        help="Web 模式: 启动后端 + 自动打开浏览器"
    )
    parser.add_argument(
        "--server", action="store_true",
        help="Server 模式: 仅启动后端 (无 GUI，无浏览器)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="后端监听地址 (默认: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="后端监听端口 (默认: 8000)"
    )
    args = parser.parse_args()

    # 三选一: GUI(默认) / Web / Server
    if args.server:
        mode = "server"
    elif args.web:
        mode = "web"
    else:
        mode = "gui"

    frontend_url = f"http://{args.host}:{args.port}/web/html/index.html"

    print("=" * 60)
    print("LightMe AI 系统启动中...")
    print(f"  启动模式: {mode}")
    print(f"  后端地址: http://{args.host}:{args.port}")
    print(f"  前端入口: {frontend_url}")
    print(f"  API 文档: http://{args.host}:{args.port}/docs")
    print("=" * 60)

    # --- 后端服务 (daemon 线程) ---
    backend_thread = threading.Thread(
        target=start_backend,
        args=(args.host, args.port),
        daemon=True,
        name="Backend",
    )
    backend_thread.start()

    # 等待后端就绪
    import urllib.request
    backend_ready = False
    for _ in range(30):
        if _stop_event.is_set():
            break
        try:
            urllib.request.urlopen(f"http://{args.host}:{args.port}/sessions", timeout=1)
            backend_ready = True
            break
        except Exception:
            time.sleep(0.5)

    if not backend_ready:
        print("[Main] 后端未能在 15 秒内就绪，请检查配置")
        return

    print("[Main] 后端就绪")

    # --- 根据模式启动前端 ---
    if mode == "gui":
        print("[Main] GUI 模式 → 桌面窗口启动中 (主线程)")
        start_gui(frontend_url)

    elif mode == "web":
        print(f"[Main] Web 模式 → 自动打开浏览器: {frontend_url}")
        webbrowser.open(frontend_url)
        print("[Main] 按 Ctrl+C 退出")
        try:
            while not _stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    else:  # server
        print("[Main] Server 模式运行中，按 Ctrl+C 退出")
        try:
            while not _stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    print("[Main] LightMe 已退出")


if __name__ == "__main__":
    main()
