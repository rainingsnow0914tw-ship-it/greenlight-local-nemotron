"""一鏡的查證：用程式量，不是問模型「好不好」。

每一項回一筆 {id, level, value, zh, en, ja}：level ＝ pass／warn／fail／look（look＝要人或看圖模型看截圖）。
門檻照作者影片管線已校準的數字搬過來（不自己編）：
  line   台詞聽寫相似度：≥0.5 OK、0.3～0.5 疑慮、<0.3 壞            （作者管線的 asr_match.py）
  head   開頭亂語：第一個字之前有 > -6dB 的聲音且長過 0.3 秒＝亂語     （作者管線的 headnoise.py，第23集）
         第一個字拖超過 0.55 秒＝亂語被吞進第一個字 → 只提醒：實測名字「つきね」也會這樣（whisper 聽成好きね），人聽過是對的
  tail   講完之後要留 0.5 秒以上，不然下一鏡一接就像被切掉              （作者的 midcheck.py）
  late   開口超過 2 秒：台詞可能來不及講完
  lips   講話那幾秒的臉部截圖（20 格縮圖看不出嘴；第一版沒對嘴就是這樣差點放過）
  grid   20 格
"""

from __future__ import annotations

import difflib
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LINE_OK, LINE_BAD = 0.5, 0.3
HEAD_DB, HEAD_SEC, FIRST_WORD_DRAG = -6.0, 0.3, 0.55
TAIL_MIN, LATE_START = 0.5, 2.0
WHISPER_LANG = {"ja": "ja", "zh": "zh", "en": "en", "ko": "ko"}


def _ff() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _norm(s: str) -> str:
    return re.sub(r"[\s、。，,.！!？?…「」『』（）()—\-~〜・：:;；\"'’“”]", "", s or "")


def _duration(path: str) -> float:
    err = subprocess.run([_ff(), "-hide_banner", "-i", path], capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _max_db(path: str, until: float) -> float:
    err = subprocess.run([_ff(), "-t", f"{until:.2f}", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = re.search(r"max_volume: ([-\d.]+) dB", err)
    return float(m.group(1)) if m else -99.0


def _item(cid, level, value, zh, en, ja):
    return {"id": cid, "level": level, "value": value, "zh": zh, "en": en, "ja": ja}


def run(path: str, *, say: str, film: str, voice_role: str = "character", save=None) -> dict:
    """查一鏡。say＝送去生成的台詞（名字已換成讀音的那份），save(name, bytes, mime)→uri 用來存截圖。"""
    from . import asr

    dur = _duration(path)
    words = asr.transcribe(path, WHISPER_LANG.get(film, film)) if say else []
    heard = "".join(w for w, _, _ in words).strip()
    out: list[dict] = []

    if say:
        if not words:
            out.append(_item("line", "fail", 0.0, "聽不到任何台詞（沒唸出來）", "no speech heard at all", "セリフが聞こえない"))
        else:
            sim = round(difflib.SequenceMatcher(None, _norm(heard), _norm(say)).ratio(), 2)
            lv = "pass" if sim >= LINE_OK else "warn" if sim >= LINE_BAD else "fail"
            out.append(_item("line", lv, sim, f"台詞聽寫相似度 {sim}（聽到：{heard}）",
                             f"line match {sim} (heard: {heard})", f"セリフ一致度 {sim}（聞こえた：{heard}）"))
            first, last = words[0][1], words[-1][2]
            vol = _max_db(path, first - 0.05) if first > HEAD_SEC else -99.0
            if vol > HEAD_DB:
                out.append(_item("head", "fail", round(first, 2),
                                 f"開頭亂語：第一個字在 {first:.2f} 秒，之前有 {vol:.1f}dB 的聲音 → 剪掉開頭",
                                 f"gibberish before the line: first word at {first:.2f}s after {vol:.1f} dB of sound — trim the head",
                                 f"冒頭に意味不明な声：最初の語は {first:.2f} 秒、その前に {vol:.1f}dB → 頭を切る"))
            elif len(words) > 1 and words[1][1] - words[0][1] > FIRST_WORD_DRAG and len(words[0][0].strip()) <= 2:
                out.append(_item("head", "warn", round(words[1][1] - words[0][1], 2),
                                 f"第一個字拖了 {words[1][1] - words[0][1]:.2f} 秒：可能有亂語，也可能只是名字聽不懂 —— 請聽開頭",
                                 "the first word drags — maybe gibberish, maybe just a name the recogniser does not know; listen to the start",
                                 "最初の語が長い：意味不明な声か、名前が聞き取れないだけか —— 冒頭を聞いてください"))
            else:
                out.append(_item("head", "pass", round(first, 2), f"開頭乾淨（第一個字 {first:.2f} 秒）",
                                 f"clean start (first word {first:.2f}s)", f"冒頭はきれい（最初の語 {first:.2f} 秒）"))
            tail = round(dur - last, 2)
            out.append(_item("tail", "pass" if tail >= TAIL_MIN else "warn", tail,
                             f"講完後還有 {tail} 秒" + ("" if tail >= TAIL_MIN else "（少於 0.5 秒，接下一鏡會像被切掉）"),
                             f"{tail}s left after the line" + ("" if tail >= TAIL_MIN else " (under 0.5s — sounds cut off)"),
                             f"言い終えてから {tail} 秒" + ("" if tail >= TAIL_MIN else "（0.5 秒未満、切れたように聞こえる）")))
            if first > LATE_START:
                out.append(_item("late", "warn", round(first, 2), f"開口太晚（{first:.2f} 秒）", f"starts late ({first:.2f}s)",
                                 f"話し始めが遅い（{first:.2f} 秒）"))

    images = {}
    if save:
        with tempfile.TemporaryDirectory() as tmp:
            g = Path(tmp) / "grid.jpg"
            subprocess.run([_ff(), "-v", "error", "-y", "-i", path, "-vf", f"fps=20/{max(dur, 0.1):.2f},scale=384:-1,tile=5x4",
                            "-frames:v", "1", str(g)])
            if g.exists():
                images["grid"] = save("grid", g.read_bytes(), "image/jpeg")
            if words:
                a, b = words[0][1], words[-1][2]
                m = Path(tmp) / "mouth.jpg"
                subprocess.run([_ff(), "-v", "error", "-y", "-ss", f"{a:.2f}", "-t", f"{max(b - a, 0.2):.2f}", "-i", path,
                                "-vf", f"fps=1/{max((b - a) / 8, 0.05):.3f},crop=iw:ih*0.5:0:0,scale=480:-1,tile=4x2",
                                "-frames:v", "1", str(m)])
                if m.exists():
                    images["mouth"] = save("mouth", m.read_bytes(), "image/jpeg")
    if say and words:
        inner = voice_role in ("narrator", "offscreen")
        out.append(_item("lips", "look", None,
                         "看講話時段的臉：心聲／旁白鏡嘴應該閉著" if inner else "看講話時段的臉：嘴有沒有跟著動（沒動＝像旁白）",
                         "check the face while speaking: the mouth should stay closed (inner voice)" if inner
                         else "check the face while speaking: does the mouth move? (still = sounds like a voice-over)",
                         "話している間の顔：心の声なので口は閉じたまま" if inner else "話している間の顔：口が動いているか（動かない＝ナレーション風）"))

    return {"checked_at": datetime.now(timezone.utc).isoformat(), "seconds": round(dur, 2), "heard": heard,
            "words": [[w, a, b] for w, a, b in words], "checks": out, "images": images,
            "passed": not any(c["level"] == "fail" for c in out), "trim_head":
            round(max(0.0, words[0][1] - 0.1), 2) if any(c["id"] == "head" and c["level"] == "fail" for c in out) else 0.0}
