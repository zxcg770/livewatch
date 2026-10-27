"""AI 盯播桌面应用入口。

    uv run app.py            # 桌面窗口（pywebview，Mac 用 WKWebView，Windows 用 Edge WebView2）
    uv run app.py --browser  # 只启动服务，用浏览器打开（调试用）
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
import webbrowser

import uvicorn

import livewatch  # noqa: F401  先导入：把控制台输出设成 UTF-8


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true", help="不开桌面窗口，用浏览器访问")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}"
    # 只监听本机回环地址，不对局域网暴露
    server = uvicorn.Server(uvicorn.Config("livewatch.server:app", host="127.0.0.1", port=port, log_level="warning"))

    if args.browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"LiveWatch 已启动：{url}")
        server.run()
        return

    import webview

    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    webview.create_window("AI 盯播 · LiveWatch", url, width=1440, height=900, min_size=(1100, 700))
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
