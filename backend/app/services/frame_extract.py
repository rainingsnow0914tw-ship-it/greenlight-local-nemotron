"""從影片抽出畫格 —— 目前用途是拿末幀去接下一鏡。

為什麼需要：
    每一鏡都是獨立生成的，模型每次都得從零猜「這個東西長什麼樣」。
    `appearance_tags` 鎖得住文字描述（多面體、琥珀綠光），
    鎖不住實際的色溫、角度、材質。所以同一個角色在相鄰兩鏡會漂移
    （實際觀察到同一個物件在相鄰鏡頭換了顏色）。

    分鏡表裡大多數鏡頭標著「接」（`continues_from`），
    那個「接」如果沒有實作 —— 標了但沒做 ——
    是那種不會報錯、只會讓成品悄悄變差的缺口。

## 為什麼用 pip 版的 ffmpeg

    抽影格必須解碼影片，純 Python 做不到（`probe()` 能自己解析 mp4 box
    是因為它只讀 metadata，不碰影像資料）。

    用 `imageio-ffmpeg` 而不是系統 ffmpeg：它把 binary 打包進 wheel，
    `pip install` 就有，**Cloud Run 部署不用改 Dockerfile**。
    系統依賴會在部署那天才咬人，而那通常是最不想除錯的時候。
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class FrameExtractError(RuntimeError):
    """抽幀失敗。"""


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover
        raise FrameExtractError(
            "缺少 imageio-ffmpeg。執行 pip install -r requirements.txt。"
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def last_frame(video: bytes, *, quality: int = 2) -> bytes:
    """抽出影片的最後一幀，回傳 JPEG 位元組。

    抽「倒數第二幀」而不是真正的最後一幀：
       影片的最末幀常常已經在淡出或壓縮品質最差（GOP 尾端），
       拿去當下一鏡的起點會讓新的一鏡從一張糊掉的畫面長出來。
       用 `sseof -0.1` 往回退 0.1 秒，取那附近的畫格。

    Args:
        quality: JPEG 品質，2 是 ffmpeg 的高品質檔（1 最好、31 最差）。
                 這張圖是要餵回模型的，壓太狠等於自己把參考弄糊。
    """
    exe = _ffmpeg()

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.mp4"
        dst = Path(tmp) / "out.jpg"
        src.write_bytes(video)

        cmd = [
            exe,
            "-hide_banner",
            "-loglevel", "error",
            # 從檔尾往回 0.1 秒開始讀 —— 見上面的說明
            "-sseof", "-0.1",
            "-i", str(src),
            # 只要一張
            "-frames:v", "1",
            "-q:v", str(quality),
            "-y",
            str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0 or not dst.exists():
            # 有些極短的片段 sseof 會失敗，退回「讀到最後一幀」的做法
            logger.debug("sseof 抽幀失敗，改用完整解碼：%s", result.stderr[:200])
            cmd_fallback = [
                exe,
                "-hide_banner",
                "-loglevel", "error",
                "-i", str(src),
                "-vf", "select=eq(n\\,0)",
                "-frames:v", "1",
                "-q:v", str(quality),
                "-y",
                str(dst),
            ]
            result = subprocess.run(
                cmd_fallback, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0 or not dst.exists():
                raise FrameExtractError(
                    f"抽幀失敗：{result.stderr[:300] or '未知錯誤'}"
                )

        data = dst.read_bytes()

    if not data:
        raise FrameExtractError("抽出來的畫格是空的")
    return data
