"""本機聽寫：faster-whisper large-v3，跑 CPU（int8）。

為什麼跑 CPU：查證要在生成之間跑，而 ComfyUI 生片時顯存吃到 20GB 上下 —— 聽寫搶顯示卡會擠爆它。
CPU 上一鏡 8 秒約 6 秒（實測，載入另 5 秒、只載一次），結果跟 GPU 版一樣。
為什麼自己解碼音訊：faster-whisper 讀檔走 PyAV，版本對不上就整個打不開（實測）；
用 app 本來就有的 ffmpeg 解成 16kHz 單聲道交給它，少一個會壞的依賴。
模型用本機快取（~/.cache/huggingface），沒有的話第一次會下載（約 3GB）。
"""

from __future__ import annotations

import subprocess
import threading

_model = None
_lock = threading.Lock()
MODEL = "large-v3"   # 作者影片管線的聽寫也是這個（headnoise／midcheck），結果才對得起來


def _audio(path: str):
    import imageio_ffmpeg
    import numpy as np

    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", path, "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


def transcribe(path: str, lang: str) -> list[tuple[str, float, float]]:
    """影片 → [(字, 開始秒, 結束秒)]。沒有人聲就是空清單。"""
    global _model
    from faster_whisper import WhisperModel

    with _lock:
        if _model is None:
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
        segs, _ = _model.transcribe(_audio(path), language=lang, word_timestamps=True)
        return [(w.word, round(w.start, 2), round(w.end, 2)) for s in segs for w in (s.words or [])]
