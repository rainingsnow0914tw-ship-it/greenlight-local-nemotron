"""Greenlight Local 的桌面入口：雙擊 → 背景開本機伺服器 → 開一個獨立視窗 → 關視窗就整個結束。

為什麼是「本機伺服器＋視窗」而不是重寫成桌面程式：介面和後端都是現成的（Greenlight 原版＋這次加的），
視窗用 Windows 內建的 Edge WebView2（pywebview），使用者不用裝瀏覽器、也看不到網址列，打開就是一個 app。

只聽 127.0.0.1、埠號每次挑空的 —— 不對外開任何埠，跟使用者自己的 ComfyUI（8188／8189）也不會撞。
資料一律在 %LOCALAPPDATA%\\GreenlightLocal（env_boot.data_home），程式資料夾可以整個刪掉重裝不掉資料。

用法：GreenlightLocal.exe            開視窗
      GreenlightLocal.exe --server   只開伺服器、印網址（測試打包用，不開視窗）
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

TITLE = "Greenlight Local · NVIDIA Nemotron Edition"


def _root() -> Path:
    """打包後：PyInstaller 解開的資料夾；開發時：repo 根目錄。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _attach_log() -> None:
    """沒有主控台的 EXE（雙擊開的）stdout／stderr 是 None：uvicorn 一設定 log 就去問 isatty() → 整個 app 開不起來。
    沒有就接到記錄檔（資料夾裡的 logs/app.log），之後出錯也有東西可以查。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    home = os.getenv("GREENLIGHT_HOME") or os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser("~"),
                                                        "GreenlightLocal")
    try:
        os.makedirs(os.path.join(home, "logs"), exist_ok=True)
        log = open(os.path.join(home, "logs", "app.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def _quiet_subprocesses() -> None:
    """沒有主控台的 EXE 叫 ffmpeg 這類主控台程式：Windows 會替每一次另開一個黑色視窗（畫面一直閃），
    而且子程式的輸入沒交代時偶爾會被干擾、還沒讀檔就結束（ffmpeg 輸出整個空白 → 成片失敗）。
    一個地方統一設定，所有呼叫點（含第三方套件）都套到：不開視窗、輸入接空的。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    import subprocess

    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int):
    root = _root()
    backend = root / "backend" if (root / "backend").exists() else root
    sys.path.insert(0, str(backend))
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    import uvicorn

    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True, name="greenlight-server")
    t.start()
    return server


def _wait_up(url: str, seconds: float = 60) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> int:
    _attach_log()
    _quiet_subprocesses()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    server = _start_server(port)
    if not _wait_up(url):
        print("Greenlight Local could not start its local server", file=sys.stderr)
        return 1
    if "--server" in sys.argv:
        print(url, flush=True)
        # 打包成無主控台的 exe 時 print 看不到：測試用 --url-file 把網址寫進檔案
        if "--url-file" in sys.argv:
            Path(sys.argv[sys.argv.index("--url-file") + 1]).write_text(url, encoding="utf-8")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        server.should_exit = True
        return 0

    import webview

    webview.create_window(TITLE, url, width=1440, height=920, min_size=(900, 640))
    webview.start()                # 視窗關掉才回來
    server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
