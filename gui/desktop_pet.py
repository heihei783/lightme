import sys
import time

import webview


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/web/html/desktop_pet.html"
    time.sleep(0.2)
    webview.create_window(
        title="LightMe 桌面宠物",
        url=url,
        width=240,
        height=360,
        resizable=False,
        frameless=True,
        easy_drag=True,
        shadow=False,
        on_top=True,
        transparent=True,
        background_color="#00000000",
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
