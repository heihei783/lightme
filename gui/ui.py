import webview
import threading
import uvicorn
import time
from web.web_py import app # 确保能导入你的 FastAPI 实例
from utils.path_tool import get_abs_path

def start_backend():
    # 使用 log_level="error" 确保终端不会刷出一堆无用的访问日志
    # 并且绝对不要在这里调用 webbrowser.open()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

if __name__ == "__main__":
    # 1. 启动后端线程（设置为守护线程，随窗口关闭而自尽）
    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()

    # 2. 等待后端热身（0.5秒通常足够了，或者用我之前给你的端口探测逻辑）
    time.sleep(0.5)

    # 3. 创建 pywebview 窗口
    # 这里就是魔法发生的地方：它会自己开一个独立的窗口，而不是调用系统浏览器
    window = webview.create_window(
        title='LightMe AI 终端',
        url='http://127.0.0.1:8000/web/html/index.html',
        width=1100,
        height=750,
        resizable=True,
        min_size=(800, 600),
        # 如果你想让它更像个真正的“App”，可以开启无边框（需要 CSS 配合拖拽）
        # frameless=True 
    )

    # 4. 启动 GUI
    # debug=False 时，用户右键不会弹出检查元素，看起来更像正式软件
    webview.start(debug=True)