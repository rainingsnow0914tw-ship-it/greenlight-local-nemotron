"""量一鏡講話時的音高（基頻，Hz）—— 抓「角色的聲音變了」：範例 2 月音有一鏡變成男生的聲音，
聽寫只聽字、聽不出男女，台詞檢查全過。

量法：講話時段的聲音切 40 毫秒一格，每格用自相關找 70～400Hz 之間的週期，有聲的格取中位數。
女聲多在 180～300Hz、男聲 90～150Hz；不求準到幾 Hz，只求分得出同一個角色是不是突然低了一截。
只用 numpy（app 已經有），不加 librosa／scipy。
"""

from __future__ import annotations

import subprocess

import numpy as np

SR = 16000


def _ff() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def median_f0(path: str, start: float = 0.0, end: float | None = None) -> float | None:
    """start～end 秒之間有聲部分的音高中位數；量不到（太短、沒聲音）回 None。"""
    args = [_ff(), "-v", "error", "-ss", f"{max(0.0, start):.2f}", "-i", path]
    if end is not None and end > start:
        args += ["-t", f"{end - start:.2f}"]
    args += ["-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(args, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    n = int(SR * 0.04)
    if len(x) < n * 5:
        return None
    lo, hi = SR // 400, SR // 70
    loud = np.sqrt(np.mean(x ** 2)) * 0.5
    f0s = []
    for i in range(0, len(x) - n, n // 2):
        fr = x[i:i + n] - np.mean(x[i:i + n])
        if np.sqrt(np.mean(fr ** 2)) < loud:
            continue
        ac = np.correlate(fr, fr, "full")[n - 1:]
        if ac[0] <= 0:
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] / ac[0] > 0.45:          # 週期性夠強才算有聲（氣音、雜音不算）
            f0s.append(SR / lag)
    return round(float(np.median(f0s)), 1) if len(f0s) >= 5 else None
