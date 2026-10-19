"""學習字幕：上行＝原聲台詞（漢字上面標讀音），下行＝學習者的母語。燒進成片，一種母語一版。

版型照作者一部日文學習動畫定案的版型（看過實際成片才定）：
  - 1080p 下：原聲 48、讀音 24、母語 40；先試過 1.5 倍太大、再縮 30%
  - 盡量一行：太寬先把字縮到 85%，還放不下才在標點斷成兩行（挑兩行最平均的位置）
  - 一句講完才換下一句（同一鏡兩人一來一往 → 照台詞長短分時間，不擠在同一個畫面）
為什麼用 PIL 畫圖再疊、不用 ASS 字幕：libass 不支援讀音標在字的正上方（ruby），
  硬用 \\pos 一個字一個字擺，換字型就全跑掉。畫成透明圖疊上去，看到的就是畫的。

時間軸用每一鏡的實際片長（clip.seconds），不是請求的秒數；字幕結束比切點早 0.15 秒（避免下一鏡閃一格）。
還沒做：照語音辨識對到每個字的時間（第三世用 whisper 對；這裡先用一鏡的時間）。
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from . import gates

SIZE = {"top": 48, "ruby": 24, "bottom": 40}      # 1080p 的字級
SHRINK = 0.85
BOTTOM_MARGIN = 46
FONTS = {
    "ja": ["YuGothB.ttc", "meiryob.ttc", "msgothic.ttc"],
    "zh": ["msjhbd.ttc", "msjh.ttc", "msyhbd.ttc"],
    "ko": ["malgunbd.ttf", "malgun.ttf"],
    "en": ["segoeuib.ttf", "arialbd.ttf"],
}
_FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/opentype/noto"), Path("/usr/share/fonts/truetype")]


class StudySubError(RuntimeError):
    pass


def font_path(lang: str) -> str:
    for name in FONTS.get(lang, []) + FONTS["zh"] + ["NotoSansCJK-Bold.ttc"]:
        for d in _FONT_DIRS:
            p = d / name
            if p.exists():
                return str(p)
    raise StudySubError(f"找不到 {lang} 的字型（{', '.join(FONTS.get(lang, []))}）")


def cues(seg, film: str) -> list[dict]:
    """段落成片的時間軸 → 每一句學習字幕的起訖。只算有生成檔的鏡頭（成片就是它們接起來的）。"""
    by_shot: dict[str, list[dict]] = {}
    for ln in seg.lines:
        by_shot.setdefault(ln["shot_id"], []).append(ln)
    out, cursor = [], 0.0
    for clip in [c for c in seg.clips if c.uri]:
        dur = float(clip.seconds or 0)
        lines = sorted(by_shot.get(clip.shot_id, []), key=lambda x: x.get("k", 0))
        if lines:
            weights = [max(1, gates.speak_units(ln["spoken"], film)) for ln in lines]
            span = max(dur - 0.15, 0.5)
            t = cursor
            for ln, w in zip(lines, weights):
                d = span * w / sum(weights)
                out.append({"start": round(t, 2), "end": round(t + d, 2), "line": ln})
                t += d
        cursor += dur
    return out


def _split_top(toks, font, maxw):
    width = lambda ts: sum(font.getlength(x) for x, _ in ts)  # noqa: E731
    if width(toks) <= maxw or len(toks) < 2:
        return [toks]
    cands = [k + 1 for k, (x, _) in enumerate(toks[:-1]) if x in "、。？！…　，,.?! "] or list(range(1, len(toks)))
    k = min(cands, key=lambda k: max(width(toks[:k]), width(toks[k:])))
    # 兩半各自還太寬就再斷（直式 1080 寬：一句斷兩行還是超出畫面、左右被切掉）
    return _split_top(toks[:k], font, maxw) + _split_top([x for x in toks[k:] if x[0] not in "　 "], font, maxw)


def _split_bottom(s, font, lang, limit):
    spaced = lang in ("en", "ko")
    words = s.split(" ") if spaced else list(s)
    lines, cur = [], ""
    for w in words:
        nxt = (cur + " " + w).strip() if spaced else cur + w
        if font.getlength(nxt) > limit and cur:
            lines.append(cur)
            cur = w
        else:
            cur = nxt
    lines.append(cur)
    return lines


def render(line: dict, film: str, lang: str, size: tuple[int, int], out: Path, names: dict | None = None) -> None:
    """一句字幕畫成一張透明圖（跟影片一樣大）。names＝名字表 {名字: 唸法}，名字的讀音照它標。"""
    from PIL import Image, ImageDraw, ImageFont

    W, H = size
    k0 = min(W, H) / 1080   # 字級照短邊：直式 1080×1920 照高度算會大 1.78 倍、一行塞不下（範例 2）
    maxw = W * 0.958
    toks = gates.tokens(gates.name_ruby(line["ruby"] or line["spoken"], names))
    bottom = (line.get("subs") or {}).get(lang, "")

    def fit(path, px, width_of):
        k = max(SHRINK, min(1.0, maxw / max(1, width_of(ImageFont.truetype(path, px)))))
        return ImageFont.truetype(path, round(px * k)), round(px * k)

    ftp, fbp = font_path(film), font_path(lang)
    ft, st = fit(ftp, round(SIZE["top"] * k0), lambda f: sum(f.getlength(b) for b, _ in toks))
    sr = round(SIZE["ruby"] * k0 * st / (SIZE["top"] * k0))
    fr = ImageFont.truetype(ftp, max(8, sr))
    fb, sb = fit(fbp, round(SIZE["bottom"] * k0), lambda f: f.getlength(bottom))

    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    y = H - BOTTOM_MARGIN * k0
    rows = _split_bottom(bottom, fb, lang, maxw) if bottom else []
    if len(rows) > 1:   # 斷了就兩行平均分（不要剩一個字吊在第二行）
        rows = _split_bottom(bottom, fb, lang, fb.getlength(bottom) / len(rows) * 1.08)
    for row in reversed(rows):
        y -= sb + 12 * k0
        d.text(((W - fb.getlength(row)) / 2, y), row, font=fb, fill=(255, 236, 170),
               stroke_width=max(1, round(3 * k0)), stroke_fill=(0, 0, 0))
    y -= 14 * k0
    has_ruby = any(r for _, r in toks)
    for row in reversed(_split_top(toks, ft, maxw)):
        y -= st + 8 * k0
        x = (W - sum(ft.getlength(b) for b, _ in row)) / 2
        for base, rb in row:
            bw = ft.getlength(base)
            if rb:
                rw = fr.getlength(rb)
                d.text((x + (bw - rw) / 2, y - sr - 2), rb, font=fr, fill=(255, 255, 255),
                       stroke_width=max(1, round(2 * k0)), stroke_fill=(0, 0, 0))
            d.text((x, y), base, font=ft, fill=(255, 255, 255), stroke_width=max(1, round(4 * k0)), stroke_fill=(0, 0, 0))
            x += bw
        if has_ruby:
            y -= sr + 6 * k0
    im.save(out)


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(video: Path) -> tuple[int, int, float]:
    """影片的寬、高、秒數（只靠 ffmpeg，不需要另裝 ffprobe）。"""
    err = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    t = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m or not t:
        raise StudySubError("讀不到影片的尺寸或長度")
    return int(m.group(1)), int(m.group(2)), int(t.group(1)) * 3600 + int(t.group(2)) * 60 + float(t.group(3))


def burn(video: Path, items: list[dict], film: str, lang: str, out: Path, names: dict | None = None) -> Path:
    """把學習字幕燒進影片。items＝cues() 的結果。會重新編碼（燒字幕的本質）。"""
    if not items:
        raise StudySubError("這一段沒有導過的台詞可以上字幕")
    W, H, T = probe(video)
    with tempfile.TemporaryDirectory() as tmp:
        pngs = []
        for i, c in enumerate(items):
            p = Path(tmp) / f"sub_{i:03d}.png"
            render(c["line"], film, lang, (W, H), p, names)
            pngs.append((p, c["start"], c["end"]))
        # 單張圖只有一格，不 loop 的話過了第一句就沒圖可疊（第三世 踩過：只剩第一句有字幕）
        ins = sum([["-loop", "1", "-t", f"{T:.2f}", "-i", str(p)] for p, _, _ in pngs], [])
        fc, prev = "", "0:v"
        for k, (_, a, b) in enumerate(pngs):
            fc += f"[{prev}][{k + 1}:v]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[o{k}];"
            prev = f"o{k}"
        script = Path(tmp) / "fc.txt"
        script.write_text(fc.rstrip(";"), encoding="utf-8")   # 幾十句的濾鏡太長，放檔案不放命令列
        cmd = [_ffmpeg(), "-v", "error", "-y", "-i", str(video), *ins, "-filter_complex_script", str(script),
               "-map", f"[{prev}]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{T:.2f}", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise StudySubError(f"燒字幕失敗：{r.stderr[-400:]}")
    return out


def srt(items: list[dict], lang: str) -> str:
    """雙語 SRT（上行原聲、下行母語；讀音寫成括號，SRT 沒辦法標在字上面）。"""
    def ts(x):
        ms = int(round(x * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for i, c in enumerate(items, 1):
        ln = c["line"]
        top = "".join(f"{b}({r})" if r else b for b, r in gates.tokens(ln["ruby"] or ln["spoken"]))
        blocks.append(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{top}\n{(ln.get('subs') or {}).get(lang, '')}\n")
    return "\n".join(blocks)
