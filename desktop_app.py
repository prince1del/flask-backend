from __future__ import annotations

import socket
import threading
import time

import webview
from app.web_app import create_app


def find_open_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(port: int) -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def wait_for_server(url: str, timeout: float = 10.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Unable to start server at {url}")


def main() -> None:
    port = find_open_port()
    url = f"http://127.0.0.1:{port}/"

    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()

    wait_for_server(url)

    # Same HoP web UI as browser/Android — webcam + file upload work on localhost.
    window = webview.create_window(
        "Nexora (created by Kunwar)",
        url,
        width=1280,
        height=900,
        resizable=True,
    )
    # Allow camera / mic prompts in desktop WebView (Edge WebView2 / WebKit).
    try:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    except Exception:
        pass
    webview.start()
    _ = window


if __name__ == "__main__":
    main()
