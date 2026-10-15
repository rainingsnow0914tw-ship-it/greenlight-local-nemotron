# -*- coding: utf-8 -*-
"""把旁白混進影片的音軌。

三層修法的最後一段：影片模型生出畫面與環境音，TTS 生出旁白，
在這裡合成一支。

為什麼要壓低環境音而不是直接疊上去：
   Omni 生的音軌是「這個場景該有的聲音」—— 雨聲、腳步、室內殘響。
   原音量疊旁白會讓旁白被蓋掉一半。紀錄片的慣例是環境音退到背景，
   讓旁白清楚地浮在上面。壓多少寫在 config，不寫死。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import config_loader


class MixError(RuntimeError):
    """混音失敗。"""


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover
        raise MixError("缺少 imageio-ffmpeg。執行 pip install -r requirements.txt。") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _video_seconds(data: bytes) -> float:
    """量影片幾秒（純 Python 解析 mp4，不叫 ffprobe）。"""
    from .video_gen import probe

    try:
        return float(probe(data).get("seconds") or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def mix_narration(video: bytes, narration_wav: bytes, narration_seconds: float = 0.0) -> bytes:
    """把旁白疊進影片，回傳新的 mp4。

    為什麼要有這個函數？
    第三層架構把旁白從影片模型拿出來之後，成品需要有人把兩條音軌合起來。
    影片那條是環境音（模型生的），旁白那條是 TTS 生的。

    設計來源：「先念再拍」架構（旁白長度決定鏡頭長度）的必要一步。

    **影像流直接 copy 不重新編碼** —— 混音不該動到畫面。
       重編碼一次就掉一次品質，而且實測
       重編碼一支 84 秒的片要 94 秒（preset veryfast），
       每一鏡都付這個代價完全不值得。
    `duration=first` 讓成品長度跟著**影片**走。
       旁白比影片長的話尾巴會被切 —— 那正是 tts.clip_seconds_for()
       無條件進位要避免的情況，這裡是最後一道保險。

    Args:
        video: 影片模型生出來的 mp4（含環境音）
        narration_wav: TTS 生出來的旁白（wav，已含檔頭）

    Returns:
        混好的 mp4 位元組

    Raises:
        MixError: ffmpeg 失敗
    """
    cfg = config_loader.casting().get("narrator", {}).get("tts", {})
    ambient = float(cfg.get("ambient_volume", 0.35))

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        vin, ain, out = d / "in.mp4", d / "voice.wav", d / "out.mp4"
        vin.write_bytes(video)
        ain.write_bytes(narration_wav)

        vsecs = _video_seconds(video)
        # 旁白比影片長怎麼辦（TTS 15 秒、分鏡只有 10 秒，會卡掉一半）。
        #
        #    Omni 一鏡最長 10 秒，而一句旁白念完可能要 15 秒 ——
        #    這不是設定錯誤，是物理限制。三條路裡選了第三條：
        #      x 截斷旁白      → 話講一半，最糟
        #      x 叫 TTS 念快點 → 抑揚頓挫是旁白的重點，加速等於毀掉它
        #      o 讓畫面陪它等完 → 最後一幀凍結補足，紀錄片完全可以接受
        #
        #    代價：那幾秒畫面是靜止的，而且 tpad 改了影像流，
        #    不能再 -c:v copy，要重編碼（慢一點，但只有超長的鏡頭才付）。
        pad = 0.0
        if narration_seconds and vsecs and narration_seconds > vsecs + 0.2:
            pad = round(narration_seconds - vsecs + 0.3, 2)

        if pad > 0:
            cmd = [
                _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(vin), "-i", str(ain),
                "-filter_complex",
                # 影片：最後一幀凍結 pad 秒；環境音跟著補靜音，否則 amix 會提早收
                f"[0:v]tpad=stop_mode=clone:stop_duration={pad}[v];"
                f"[0:a]volume={ambient},apad[bg];"
                # normalize=0：amix 預設會把每軌音量除以輸入數 ——
                #    那會讓旁白聽起來變小聲（環境音已經壓過
                #    一次 0.35，amix 再砍半，旁白也被連坐砍半）。
                f"[bg][1:a]amix=inputs=2:duration=longest:normalize=0[aout]",
                "-map", "[v]", "-map", "[aout]",
                # veryfast：實測 preset medium 重編碼一支片要 212 秒
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                str(out),
            ]
        else:
            cmd = [
                _ffmpeg(),
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", str(vin),
                "-i", str(ain),
                "-filter_complex",
                # 環境音壓低 → 跟旁白混 → 成品長度跟著影片
                # normalize=0 的理由見上面那條註釋
                f"[0:a]volume={ambient}[bg];"
                f"[bg][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                str(out),
            ]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if proc.returncode != 0 or not out.exists():
            # 影片沒有音軌時上面的 filter 會失敗（[0:a] 不存在）。
            #    那種情況直接把旁白當成唯一音軌接上去，不要整鏡失敗 ——
            #    沒有環境音的成品仍然可用，生不出來就什麼都沒有。
            fallback = [
                _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(vin), "-i", str(ain),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", str(out),
            ]
            proc2 = subprocess.run(fallback, capture_output=True, text=True, errors="replace")
            if proc2.returncode != 0 or not out.exists():
                raise MixError(
                    f"混音失敗：{proc.stderr[:200]} / 退回單軌也失敗：{proc2.stderr[:200]}"
                )

        return out.read_bytes()
