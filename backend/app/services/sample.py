"""內建範例：第一次開 app 時，把安裝檔裡附的範例專案放進專案清單。

為什麼要有：多數人沒有 24GB 顯示卡、也還沒有 Nebius 金鑰，打開一個空的 app 看不出它在做什麼。
範例是一個已經跑完的專案（導演回合、關卡、製作人意見、試拍查證與診斷、學語言台詞與筆記、成片），
不用跑任何東西就能看過程與結果。範例由 scripts/make_sample.py 產生，打包進安裝檔的 sample/。

只放一次：放過就在設定資料夾記一個標記；使用者自己刪掉範例，下次不會又冒出來。
這一步失敗不能讓 app 開不起來 —— 呼叫端包在 try 裡。
"""

from __future__ import annotations

import json
import logging
import shutil

from .env_boot import config_home, data_home, project_root

logger = logging.getLogger(__name__)
MARKER = ".sample_installed"


def install_if_needed() -> str:
    """回傳做了什麼（給 log 看）：installed／already／no-sample。"""
    src = project_root() / "sample"
    marker = config_home() / MARKER
    if marker.exists():
        return "already"
    if not (src / "projects").exists():
        return "no-sample"
    from . import media_store

    for pj in (src / "projects").glob("*.json"):
        dst = data_home() / "projects" / pj.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(pj, dst)
        pid = pj.stem
        m_src, m_dst = src / "media" / pid, media_store.root() / pid
        if m_src.exists():
            m_dst.mkdir(parents=True, exist_ok=True)
            for f in m_src.iterdir():
                if not (m_dst / f.name).exists():
                    shutil.copyfile(f, m_dst / f.name)
    # 預先翻好的英日文說明：沒有金鑰也能看到乾淨的介面語言
    cache_src = src / "cache_ui_translate.json"
    if cache_src.exists():
        cache_dst = config_home() / "cache" / "ui_translate.json"
        cache_dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            cur = json.loads(cache_dst.read_text(encoding="utf-8")) if cache_dst.exists() else {}
        except Exception:  # noqa: BLE001
            cur = {}
        cur = {**json.loads(cache_src.read_text(encoding="utf-8")), **cur}
        cache_dst.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    config_home().mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")
    return "installed"
