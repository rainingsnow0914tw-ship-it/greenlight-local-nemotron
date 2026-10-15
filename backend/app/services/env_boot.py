"""環境變數載入。

為什麼需要這個檔：
    專案有 `.env.example`，設計上就預期會有一份 `.env`。
    如果沒有任何地方呼叫 `load_dotenv()`，**那份 .env 從來不會被讀到**。

    症狀非常難查：後端在「某個 shell」起得來、換一個 shell 起就
    `RuntimeError: 請設定 GCP_PROJECT_ID`。看起來像時好時壞的環境問題，
    實際上是「先前那個 shell 剛好手動 export 過」。

    更糟的一種：舊的後端 process 沒被殺乾淨，新的因為 port 被佔而沒真的 bind，
    請求全部落到帶著舊環境變數的舊 process 上 —— 於是「code 改了沒生效」
    跟「環境變數有時候有有時候沒有」同時發生。

    修法就是把載入這件事寫死在啟動路徑上，不依賴呼叫者的 shell。

載入順序（後者不覆蓋前者）：
    1. 已經存在的系統環境變數  ← 最優先，正式環境靠這個（Cloud Run 注入）
    2. 專案根目錄的 .env       ← 本機開發
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_loaded = False


def project_root() -> Path:
    """專案根目錄（含 config/ 與 .env 的那一層）。唯一的家：config_loader、prompt_rooms 都來問這裡。

    打包成安裝檔（PyInstaller）之後，程式碼和 config/ 都在解開的資料夾（sys._MEIPASS）底下，
    用 __file__ 往上數三層會數到安裝資料夾外面 —— 所以打包時直接回那個資料夾。
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


def config_home() -> Path:
    """設定的家（金鑰、每一步用哪個 AI、作品放哪裡）：固定位置，使用者不用選。

    預設：Windows `%LOCALAPPDATA%\\GreenlightLocal`（安裝版的標準位置）；其他系統 `~/.greenlight-local`。
    `GREENLIGHT_HOME` 可覆寫（測試指到暫存目錄）。
    """
    override = os.getenv("GREENLIGHT_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "GreenlightLocal"
    return Path.home() / ".greenlight-local"


def data_home() -> Path:
    """作品的家（專案、圖、影片、學習紀錄）：使用者可以在設定頁選資料夾（例如換到空間大的 D 槽）。

    為什麼跟設定分開：作品會很大（一支片幾 GB），使用者習慣自己選放哪；但「作品放哪」這件事本身要記在
    一個固定的地方，不然換了資料夾就找不回來 —— 所以設定留在 config_home，作品位置記在那裡的 location.json。
    沒選過就跟設定放一起（原本的行為）。

    為什麼要有單一入口：原版三個地方各自寫死 `~/.greenlight`，本機版第一次啟動就讀到原版的舊專案
    （實測，只讀到、沒寫到）。原版的 GREENLIGHT_STORE_PATH／MEDIA_ROOT／DATA_DIR 照舊優先。
    """
    home = config_home()
    loc = home / "location.json"
    if loc.exists():
        import json

        try:
            chosen = json.loads(loc.read_text(encoding="utf-8")).get("data_dir")
            if chosen:
                return Path(chosen)
        except (ValueError, OSError):
            pass
    return home


def _fix_console_encoding() -> None:
    """Windows console 預設 cp950，印中文會壞掉。

    這不只是「看起來是亂碼」而已：
        cp950 編不出來的字元會直接拋 UnicodeEncodeError，
        把整支腳本在最後一行炸掉 —— 而前面該做的事其實都做完了。
        看起來像「腳本失敗」，實際上是「腳本成功但印不出來」，
        這種錯誤訊息會讓人回頭去 debug 根本沒壞的東西。

    只動 stdout/stderr 的編碼，不碰 locale ——
    改 locale 會影響到檔案讀寫的預設編碼，那個副作用大得多。
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 被重導向或包裝過的 stream 可能沒有 reconfigure。
            # 印不出中文比整個服務起不來好，所以吞掉。
            pass


def load() -> Path | None:
    """載入 .env。回傳實際載入的檔案路徑，沒有就 None。

    重複呼叫是安全的 —— 只會真的載入一次。
    main.py 與各個 scripts 都會呼叫，不需要互相協調誰先誰後。

    `override=False`：已存在的環境變數優先。
       正式環境（Cloud Run / Secret Manager）注入的值不能被一份
       不小心打包進 image 的 .env 蓋掉。
    """
    global _loaded
    if _loaded:
        return None

    _loaded = True
    _fix_console_encoding()
    env_path = project_root() / ".env"
    if not env_path.exists():
        return None

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        # 沒裝 python-dotenv 不該讓整個服務起不來 ——
        # 正式環境本來就靠系統環境變數，.env 只是本機的方便。
        return None

    load_dotenv(env_path, override=False)
    return env_path


def require(name: str, hint: str = "") -> str:
    """讀一個必要的環境變數，沒有就給出**可以照著做**的錯誤訊息。

    只說「請設定 GCP_PROJECT_ID」對操作者沒有幫助 ——
    他需要知道設在哪個檔、填什麼值。
    """
    load()
    value = os.getenv(name)
    if value:
        return value

    detail = f"\n{hint}" if hint else ""
    raise RuntimeError(
        f"缺少環境變數 {name}。"
        f"請在 {project_root() / '.env'} 裡加上 `{name}=...`"
        f"（可以從 .env.example 複製一份）。{detail}"
    )
