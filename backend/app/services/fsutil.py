"""檔案小工具：只有一件事 —— Windows 上的原子取代要重試。"""
from __future__ import annotations

import time
from pathlib import Path


def replace(tmp: Path, dst: Path, tries: int = 40, wait: float = 0.05) -> None:
    """tmp → dst 的原子取代；被別人開著（拒絕存取）就等一下再試，最多約 2 秒。

    為什麼：Windows 上目標檔只要被別的 handle 開著（後端另一條執行緒正在讀它回應畫面輪詢、
    OneDrive／防毒在掃），os.replace 就丟 PermissionError。這種鎖通常幾毫秒就放開。
    第三世試跑：選場景、成片、導演三個面板每 3 秒輪詢 → 準備參考卡存檔撞到，整個工作失敗。
    """
    for i in range(tries):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait)
