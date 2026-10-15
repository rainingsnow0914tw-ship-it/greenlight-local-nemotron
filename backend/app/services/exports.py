"""成品另存一份到使用者選的「成品輸出資料夾」。

為什麼要有：原版是雲端網頁，成品在網頁上按下載；本機 app 的使用者習慣是「做好的東西出現在我選的資料夾」
（一般程式的習慣：儲存和下載的位置都能自己選）。
作品資料夾裡那份照舊留著（版本、接片都靠它）；這裡是另外複製一份給人拿去用。

一個專案一個子資料夾（用作品名）。複製失敗（磁碟滿、資料夾被刪）只記 log，不擋接片 —— 成品已經存在作品資料夾了。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from . import settings_store
from .env_boot import data_home

logger = logging.getLogger(__name__)


def export_dir() -> Path:
    return Path(settings_store.load().get("export_dir") or (data_home() / "exports"))


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "Greenlight"


def publish(project_id: str, name: str, data: bytes, ext: str = "mp4") -> str | None:
    """另存一份成品，回傳存到哪（失敗回 None）。"""
    try:
        from . import project_store

        state = project_store.get_store().load(project_id)
        title = (state.screenplay.title if state and state.screenplay else "") or project_id
        folder = export_dir() / _safe(title)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{_safe(name)}.{ext}"
        dest.write_bytes(data)
        return str(dest)
    except Exception as exc:  # noqa: BLE001 - 另存失敗不能擋住接片
        logger.warning("成品另存到輸出資料夾失敗（作品資料夾裡那份還在）：%s", exc)
        return None
