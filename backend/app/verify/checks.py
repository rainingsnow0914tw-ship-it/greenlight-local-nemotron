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
MID_GAP = 1.0   # 字跟字之間空這麼久、又有大聲音 → 提醒去聽（可能是亂語，也常常只是吸氣、哭的抽氣）
WHISPER_LANG = {"ja": "ja", "zh": "zh", "en": "en", "ko": "ko"}


def _ff() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _norm(s: str) -> str:
    # ～（全形波浪號）也是符號：「リサ、また来たね～」被判「沒唸到～」
    return re.sub(r"[\s、。，,.！!？?…「」『』（）()—\-~〜～・：:;；\"'’“”♪]", "", s or "")


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


EXTRA_LEAD_CHARS = 3   # 台詞前多出幾個字才算亂語（少於 3 個多半是名字聽錯，例：つきね聽成「好きね」）


def _line_start(words: list, say: str, min_lead: int = EXTRA_LEAD_CHARS) -> tuple[str, float] | None:
    """台詞前面多聽到的字與台詞開始的秒數；台詞就從頭開始（或對不到）回 None。"""
    heard = [(_norm(w), a) for w, a, _ in words]
    flat = "".join(w for w, _ in heard)
    target = _norm(say)
    if not flat or not target:
        return None
    probe = target[:min(4, len(target))]
    sm = difflib.SequenceMatcher(None, flat, probe, autojunk=False)
    m = sm.find_longest_match(0, len(flat), 0, len(probe))
    if m.size < min(2, len(probe)):
        return _line_start_kana(words, say, min_lead)
    start = m.a - m.b              # 台詞第一個字在聽寫裡的位置（對齊到 probe 開頭）
    if start < min_lead:
        return None
    pos = 0
    for w, t in heard:              # 字元位置 → 那個字的開始秒數
        if pos + len(w) > start:
            return flat[:start], t
        pos += len(w)
    return None


def _line_start_kana(words: list, say: str, min_lead: int = EXTRA_LEAD_CHARS) -> tuple[str, float] | None:
    """字面對不到台詞開頭時，改用假名讀音找（範例 2：台詞「七個光点」聽寫成「ちここうてん」、
    「あ、わたし」聽成「あ私」—— 字面找不到就當成開頭乾淨，前面的亂語「事前、信じておいよ。お、おぬく」沒剪）。"""
    if _kana("あ") == "":
        return None
    starts = []
    for w, a, _ in words:
        starts += [a] * len(_norm(w))
    surface = "".join(_norm(w) for w, _, _ in words)
    kana, at, kstart = "", 0, []
    for tok in _TAGGER(surface):
        k = _kana(tok.feature.kana or tok.surface) if getattr(tok.feature, "kana", None) else _kana(tok.surface)
        kana += k
        kstart += [(at, starts[at] if at < len(starts) else 0.0)] * len(k)
        at += len(tok.surface)
    probe = _kana(say)[:6]   # 6 個音：名字、數字常被聽成別的字（ななこ→ちこ），4 個音對不到 3 個
    if len(probe) < 3 or not kana:
        return None
    m = difflib.SequenceMatcher(None, kana, probe, autojunk=False).find_longest_match(0, len(kana), 0, len(probe))
    if m.size < 3:
        return None
    pos = m.a - m.b
    if pos < min_lead or pos >= len(kstart):
        return None
    ci, t = kstart[pos]
    return surface[:ci], t


def _tail_missing(heard: str, say: str, n: int = 3) -> str:
    """台詞最後 n 個字有沒有出現在聽到的最後 2n 個字裡；回傳沒聽到的那幾個字（空字串＝唸完了）。

    s061_15：台詞「…思ってるだけ？」，片長 9 秒不夠，聽到「…思ってるで」—— 整句相似度 0.9 照樣過，
    只有看尾巴才抓得到。被切掉的解法是加秒數重生，剪接救不回來。"""
    tail, got = _norm(say)[-n:], _norm(heard)[-2 * n:]
    miss = "".join(ch for ch in tail if ch not in got)
    if miss:
        # 再用假名比一次：聽寫常把名字寫成同音的漢字（範例 2：シェンシー 聽成「戦士」＝せんし），字面比會誤報
        kt, kg = _kana(say)[-n:], _kana(heard)[-2 * n:]
        if kt and not [ch for ch in kt if ch not in kg]:
            return ""
    return miss


def _line_end(words, say: str):
    """台詞講完之後還有沒有多出來的話（模型自己加的亂語、即興）。回傳 (多出來的字, 台詞最後一個字結束的秒數) 或 None。

    範例 2 s005_01：「…毎回やり直し？」唸完了，後面又冒出「ま、すでにだっしらりあえ」——
    結尾檢查只看最後幾個字，判成「沒唸完、加長重生」，重生幾次都一樣；其實剪掉尾巴就好。
    用假名比（聽寫常寫成別的漢字：やりなおし／やり直し），找台詞最後 4 個音在聽寫裡的位置。"""
    # 讀音要整句一起查（聽寫一個字一段：單獨的「直」字典讀成じか），再照字的位置對回每段的結束秒數
    ends = []
    for w, _, b in words:
        ends += [b] * len(_norm(w))
    surface = "".join(_norm(w) for w, _, _ in words)
    kw, at = [], 0
    if _kana("あ") == "":       # 沒有字典
        return None
    for tok in _TAGGER(surface):
        at += len(tok.surface)
        kw.append((_kana(tok.feature.kana or tok.surface) if getattr(tok.feature, "kana", None) else _kana(tok.surface),
                   ends[min(at, len(ends)) - 1] if ends else 0.0))
    flat = "".join(k for k, _ in kw)
    probe = _kana(say)[-4:]
    if len(probe) < 3 or not flat:
        return None
    m = difflib.SequenceMatcher(None, flat, probe, autojunk=False).find_longest_match(0, len(flat), 0, len(probe))
    if m.size < 3 or m.b + m.size < len(probe) - 1:     # 台詞的最後一兩個音要對得上，才算找到結尾
        return None
    end = m.a + m.size + (len(probe) - m.b - m.size)
    extra = flat[end:]
    if len(extra) < 3:
        return None
    pos = 0
    for k, b in kw:
        pos += len(k)
        if pos >= end:
            return extra, b
    return None


def _inserted(heard: str, say: str) -> str:
    """聽到的跟台詞用假名對齊，台詞中間（不算頭尾）插進 3 個音以上的那一段；沒有回空字串。"""
    a, b = _kana(say), _kana(heard)
    if len(a) < 6 or not b:
        return ""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    blocks = [m for m in sm.get_matching_blocks() if m.size]
    if not blocks:
        return ""
    lo, hi = blocks[0].b, blocks[-1].b + blocks[-1].size      # 台詞在聽寫裡的範圍（頭尾多的不算，交給 head／tail_extra）
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace") and j1 > lo and j2 < hi and (j2 - j1) - (i2 - i1) >= 3:
            return b[j1:j2]
    return ""


def _kana(s: str) -> str:
    """整句換成平假名讀音、拿掉長音和小字（しぇんしー → しんし），只給結尾比對用；沒有字典就回空字串。"""
    try:
        import fugashi
    except ImportError:
        return ""
    global _TAGGER
    if _TAGGER is None:
        _TAGGER = fugashi.Tagger()
    out = "".join((w.feature.kana or w.surface) if getattr(w.feature, "kana", None) else w.surface for w in _TAGGER(_norm(s)))
    out = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in out)
    return re.sub(r"[ーぁぃぅぇぉゃゅょゎっ]", "", out)


_TAGGER = None


def _max_db_between(path: str, start: float, end: float) -> float:
    err = subprocess.run([_ff(), "-ss", f"{start:.2f}", "-t", f"{max(end - start, 0.05):.2f}", "-i", path,
                          "-af", "volumedetect", "-f", "null", "-"],
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
            over = _line_end(words, say)
            # 台詞後面多了話 → 已經在聽寫裡找到台詞的結尾＝唸完了（不然多出來的話會被當成「沒唸完」）；成片時剪在台詞結束後
            cut = "" if over else _tail_missing(heard, say)
            if over and dur - over[1] > 0.6:
                trim_tail = round(min(dur, over[1] + 0.4), 2)
                out.append(_item("tail_extra", "warn", trim_tail,
                                 f"台詞講完後又多了話（{over[0]}）→ 成片時剪在 {trim_tail} 秒（不用重生）",
                                 f"extra speech after the line ({over[0]}) — finishing cuts the shot at {trim_tail}s (no re-render)",
                                 f"セリフの後に余計な声（{over[0]}）→ 仕上げで {trim_tail} 秒で切ります（再生成なし）"))
            if cut:
                # 第 61 場 18 鏡誤報 0、舊 15 抓到→ 硬錯
                out.append(_item("tail_cut", "fail", cut,
                                 f"台詞結尾沒唸完（少了「{cut}」）→ 秒數不夠，加長這一鏡再生",
                                 f"the end of the line was cut off (missing “{cut}”) — the shot is too short; lengthen it and regenerate",
                                 f"セリフの最後が切れている（「{cut}」がない）→ 尺が足りない。長くして作り直す"))
            first, last = words[0][1], words[-1][2]
            vol = _max_db(path, first - 0.05) if first > HEAD_SEC else -99.0
            lead = _line_start(words, say)
            # 找得到台詞真正的開頭（前面有被聽寫成字的亂語）就用它，不管第一個字之前有沒有聲音：
            #   範例 2 s001_02「あ、イオンゴオキオユ答えは…」—— 舊寫法先看到「第一個字前有聲音」就剪到第一個字
            #   （1.62 秒），亂語是被聽成字的那幾個，留在成片裡
            if lead is not None:
                # 亂語被聽寫成「字」的情況（s001_04：聽到「アハマイスコスヤ町だの何言ってんの」）：
                #   上面只看「第一個字之前有沒有聲音」，亂語自己就是第一個字，抓不到。改找台詞真正從哪裡開始。
                extra, at = lead
                out.append(_item("head", "fail", round(at, 2),
                                 f"開頭亂語：台詞前多了「{extra}」，台詞從 {at:.2f} 秒才開始 → 剪掉開頭",
                                 f"gibberish before the line: heard “{extra}” first, the line starts at {at:.2f}s — trim the head",
                                 f"冒頭に意味不明な声：セリフの前に「{extra}」、セリフは {at:.2f} 秒から → 頭を切る"))
                first = at
            elif vol > HEAD_DB:
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
            # 台詞中間的亂語（s001_08：「え？」之後 1.9 秒、-0.8dB 的聲音沒被聽寫成字，然後才「何？」）。
            #   開頭的剪得掉，夾在中間的剪不掉 —— fail＝要重拍。
            for (_, _, a_end), (_, b_start, _) in zip(words, words[1:]):
                if b_start - a_end > MID_GAP:
                    v = _max_db_between(path, a_end + 0.1, b_start - 0.1)
                    if v > HEAD_DB:
                        # 只提醒、不判失敗：作者管線 的教訓（cutmid.py 檔頭）—— 中段那段「多出來的聲音」
                        #   後來證實是哭的抽氣，whisper 把非語言的聲音寫成字不等於有多的台詞，機器聽到的不構成重拍的理由。
                        out.append(_item("gap", "warn", round(b_start - a_end, 2),
                                         f"台詞中間 {a_end:.2f}～{b_start:.2f} 秒有聲音（{v:.1f}dB）：可能是吸氣、哭的抽氣，也可能是亂語 —— 請聽這一段",
                                         f"sound between words at {a_end:.2f}–{b_start:.2f}s ({v:.1f} dB): maybe a breath or a sob, maybe gibberish — listen to this part",
                                         f"セリフの途中 {a_end:.2f}～{b_start:.2f} 秒に音（{v:.1f}dB）：息継ぎや泣き声かもしれないし、意味不明な声かもしれない —— この部分を聞いてください"))
                        break
            # 台詞中間多出來的音（被聽寫成字的）：範例 2 s007_01 聽到「次回は岩井は私が」，人耳聽到「次回は　うわ」——
            #   整句相似度照樣過、中間沒有停頓所以上面那個也沒叫。用假名對齊台詞，中間插進 3 個音以上就提醒（理由同上：只提醒）
            ins = _inserted(heard, say)
            if ins:
                out.append(_item("insert", "warn", ins,
                                 f"台詞中間多了「{ins}」：可能是唸糊的怪音 —— 請聽這一段，是的話重拍（剪不掉）",
                                 f"extra sounds inside the line (“{ins}”): maybe garbled speech — listen; if so, regenerate (it cannot be cut out)",
                                 f"セリフの途中に余計な音（「{ins}」）：言い間違いかもしれない —— 聞いて、そうなら作り直し（切れない）"))
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
            "passed": not any(c["level"] == "fail" for c in out),
            "trim_tail": next((c["value"] for c in out if c["id"] == "tail_extra"), 0.0), "trim_head":
            round(max(0.0, next((c["value"] for c in out if c["id"] == "head" and c["level"] == "fail"), 0.1) - 0.1), 2)
            if any(c["id"] == "head" and c["level"] == "fail" for c in out) else 0.0}
