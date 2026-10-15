"""組評審示範影片：九列，每列＝畫面（app 實錄／我們生的片）＋旁白，照 旁白/時間表.md 的時間接。

沿用 nebius-director/video/build_demo.py 的做法（寫成腳本，素材換了重跑就好，不手剪）。
素材只讀：app 實錄在 錄影/（record_app.py 錄的）、生好的片在測試專案 greenlight-local/_private/示範專案（不進 git）。
錄影的起點從片尾倒推（片長 − 動作秒數）：Playwright 錄影開頭會掉幀，記下的起點不準（第 4 列差 2.6 秒）。

輸出：成片/greenlight_local_demo.mp4（1920x1080、30fps）
"""
import json
import os
import pathlib
import re
import subprocess

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parents[1]          # greenlight-local/（這支在 submission/demo-video/）
WS = HERE.parents[2]           # 工作區根目錄（ComfyUI 在這裡，只讀）
REC = HERE / "錄影"
NARR = HERE / "旁白" / "narration_full.wav"
GL = APP / "_private" / "示範專案"     # 測試專案（第 61 場），私人資料夾、不進 git
OUT = HERE / "成片"
WORK = OUT / "_work"
WORK.mkdir(parents=True, exist_ok=True)
FF = __import__("imageio_ffmpeg").get_ffmpeg_exe()
W, H, FPS = 1920, 1080, 30
LEAD = 1.5                    # 開場先讓日文台詞響 1.5 秒，旁白才進來
_T = json.loads((HERE / "旁白" / "starts.json").read_text(encoding="utf-8"))   # join_narration.py 寫的
STARTS, ROWS = _T["starts"], _T["rows"]
TAIL = 1.5                    # 片尾標題多停一下
FONT = "C:/Windows/Fonts/segoeui.ttf"
FONT_B = "C:/Windows/Fonts/segoeuib.ttf"
BG, FG, DIM, ACCENT = (24, 20, 26), (240, 236, 240), (170, 160, 175), (118, 185, 0)


def run(args):
    subprocess.run([FF, "-v", "error", "-y", *args], check=True)


def duration(p: pathlib.Path) -> float:
    o = subprocess.run([FF, "-i", str(p)], capture_output=True, encoding="utf-8", errors="replace").stderr or ""
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", o).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def row_len(i: int) -> float:
    total = duration(NARR)
    end = STARTS[i + 1] if i + 1 < len(STARTS) else total + TAIL
    return round(end - STARTS[i] + (LEAD if i == 0 else 0), 3)


VF = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x18141a,fps={FPS},format=yuv420p"


def seg_app(i: int, row: int) -> pathlib.Path:
    """app 實錄：從片尾倒推起點，取這一列的長度。"""
    ready = json.loads((REC / "ready.json").read_text(encoding="utf-8"))[str(row)]
    src = REC / f"{row}.webm"
    start = duration(src) - ready["seconds"]
    n = row_len(i)
    out = WORK / f"seg{i + 1}.mp4"
    # 錄影比旁白那一列短一點點時（第 2 列短 0.06 秒，整支後面全往前偏）：最後一格多停補滿，每列長度一定等於旁白
    run(["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{n:.3f}", "-vf", f"{VF},tpad=stop_mode=clone:stop_duration=2,fade=in:d=0.25,fade=out:st={n - 0.25:.3f}:d=0.25",
         "-an", "-c:v", "libx264", "-crf", "18", "-preset", "medium", str(out)])
    return out


def seg_open(i: int) -> pathlib.Path:
    """開場：完成的學語言成片（放大到 1080p、日文＋讀音＋英文字幕），保留它自己的聲音。"""
    n = row_len(i)
    out = WORK / f"seg{i + 1}.mp4"
    run(["-i", str(GL / "finish" / "ja-en.mp4"), "-t", f"{n:.3f}", "-vf", f"{VF},fade=out:st={n - 0.25:.3f}:d=0.25",
         "-af", f"apad,atrim=0:{n:.3f}", "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)])
    return out


def card(path: pathlib.Path, lines: list[tuple[str, int, tuple, str]], size=(W, H), bg=BG):
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    total = sum(s + 24 for _, s, _, _ in lines)
    y = (size[1] - total) // 2
    for text, s, color, font in lines:
        f = ImageFont.truetype(font, s)
        w = d.textlength(text, font=f)
        d.text(((size[0] - w) / 2, y), text, font=f, fill=color)
        y += s + 24
    img.save(path)


def seg_mouth(i: int) -> pathlib.Path:
    """嘴閉 vs 嘴動：同一句台詞，左邊沒寫誰在講、右邊寫了（症狀庫 S01 那兩行提示詞）。"""
    n = row_len(i)
    lab_l, lab_r = WORK / "lab_l.png", WORK / "lab_r.png"
    card(lab_l, [("Speaker not named", 40, FG, FONT_B), ("voice plays, mouth stays closed", 30, DIM, FONT)], size=(940, 150))
    card(lab_r, [("Speaker named", 40, FG, FONT_B), ("lip-synced", 30, ACCENT, FONT)], size=(940, 150))
    out = WORK / f"seg{i + 1}.mp4"
    shots = GL / "shots"
    run(["-i", str(shots / "s001_06_v1_rep1.mp4"), "-i", str(shots / "s001_06.mp4"), "-loop", "1", "-i", str(lab_l),
         "-loop", "1", "-i", str(lab_r), "-filter_complex",
         f"color=c=0x18141a:s={W}x{H}:r={FPS}:d={n:.3f}[bg];"
         "[0:v]scale=940:-2,setpts=PTS-STARTPTS[a];[1:v]scale=940:-2,setpts=PTS-STARTPTS[b];"
         "[bg][a]overlay=13:330:shortest=0[x];[x][b]overlay=967:330[y];[y][2:v]overlay=13:150[z];[z][3:v]overlay=967:150,"
         f"trim=0:{n:.3f},fade=in:d=0.25,fade=out:st={n - 0.25:.3f}:d=0.25,format=yuv420p[v]",
         "-map", "[v]", "-t", f"{n:.3f}", "-c:v", "libx264", "-crf", "18", str(out)])
    return out


# 片尾「Now I make my own anime, and I learn from every line.」配作者自己做的學日文動畫（只讀）：
#    第 1 話日英版 0:45 前後＝「でも今朝、籠の中にジャスミンの香りがした。」漢字上有讀音、下面英文
S3_CLIP = WS / "ComfyUI" / "第三世動畫" / "成品" / "第01話" / "第01話_日英.mp4"
S3_START = 43.5


def seg_title(i: int) -> pathlib.Path:
    """片尾：先接作者的學日文動畫（第一句旁白的長度），再切標題卡（最後一句旁白＋多停 TAIL）。"""
    n = row_len(i)
    first = ROWS[i][0]
    a = round(duration(HERE / "旁白" / "_join" / f"n{first:02d}.wav") + IN_GAP, 3)
    png = WORK / "title.png"
    card(png, [("Greenlight Local", 110, FG, FONT_B), ("NVIDIA Nemotron Edition", 54, ACCENT, FONT_B),
               ("", 20, FG, FONT), ("From story to film, checked shot by shot, on your own GPU.", 38, DIM, FONT)])
    clip, title = WORK / "seg9a.mp4", WORK / "seg9b.mp4"
    run(["-ss", f"{S3_START}", "-i", str(S3_CLIP), "-t", f"{a:.3f}", "-vf", f"{VF},fade=in:d=0.25,fade=out:st={a - 0.3:.3f}:d=0.3",
         "-an", "-c:v", "libx264", "-crf", "18", str(clip)])
    run(["-loop", "1", "-i", str(png), "-t", f"{n - a:.3f}", "-vf", f"fps={FPS},format=yuv420p,fade=in:d=0.4",
         "-c:v", "libx264", "-crf", "18", str(title)])
    out = WORK / f"seg{i + 1}.mp4"
    lst = WORK / "seg9.txt"
    lst.write_text(f"file '{clip.as_posix()}'\nfile '{title.as_posix()}'\n", encoding="utf-8")
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


CHUNKS = json.loads((HERE / "旁白" / "narration_chunks.json").read_text(encoding="utf-8"))
IN_GAP, ROW_GAP = 0.35, 1.0
MAX_CHARS = 80                # 一句超過就在逗號處拆成兩張（一張最多兩行）


def narration_cues() -> list[tuple[float, float, str]]:
    """旁白字幕：一句一張，講完那一句才換（作者規則：字幕講完一句才換下一句）。

    時間用每一段旁白的逐字聽寫（旁白/_join/nXX.wav 是接成整條時用的同一份），不用字數比例估：
    每一段在整條裡的起點照 join_narration.py 的接法算（段內 0.35 秒、列間 1 秒），再加開場 LEAD。
    聽寫的字跟稿子差一點（25／twenty-five、re -roll），所以句子邊界用「字元位置的比例」對到聽寫的字。"""
    import re as _re
    import sys
    sys.path.insert(0, str(APP / "backend"))
    from app.verify import asr

    norm = lambda s: _re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    cues, t = [], LEAD
    for r, row in enumerate(ROWS):
        if r:
            t += ROW_GAP
        for k, i in enumerate(row):
            if k:
                t += IN_GAP
            wav = HERE / "旁白" / "_join" / f"n{i:02d}.wav"
            words = [(w, a, b) for w, a, b in asr.transcribe(str(wav), "en") if norm(w)]
            text = CHUNKS[i - 1]
            # 稿子拆句，太長的句子在最靠中間的逗號再拆
            pieces = []
            for sent in _re.findall(r"[^.!?]+[.!?]?", text):
                sent = sent.strip()
                if not sent:
                    continue
                if len(sent) > MAX_CHARS and "," in sent:
                    cut = min((m.end() for m in _re.finditer(",", sent)), key=lambda e: abs(e - len(sent) / 2))
                    pieces += [sent[:cut].strip(), sent[cut:].strip()]
                else:
                    pieces.append(sent)
            total = len(norm(text)) or 1
            cum, acc = [], 0                        # 每個聽寫字結束時，在全段字元裡的比例
            heard_total = sum(len(norm(w)) for w, _, _ in words) or 1
            for w, _, _ in words:
                acc += len(norm(w))
                cum.append(acc / heard_total)
            done = 0
            for p in pieces:
                f0, done = done / total, done + len(norm(p))
                f1 = done / total
                i0 = next((j for j, c in enumerate(cum) if c > f0 + 1e-6), len(words) - 1)
                i1 = next((j for j, c in enumerate(cum) if c >= f1 - 0.02), len(words) - 1)
                cues.append((t + words[i0][1], t + words[i1][2], p))
            t += duration(wav)
    # 停留規則照作者 作者管線的 make_jaen_subs.py：講完多停 HOLD 秒，
    #    下一句一開始就換（不留空檔、不重疊），最短 0.8 秒
    out = []
    for j, (a, b, p) in enumerate(cues):
        nxt = cues[j + 1][0] if j + 1 < len(cues) else b + HOLD
        out.append((a, max(b, min(max(b + HOLD, a + 0.8), nxt)), p))
    return out


HOLD = 1.0


def ass_file(cues, path: pathlib.Path) -> None:
    """ASS 字幕：白字黑邊、底部置中；開場那一列（成片自己有日英字幕在底部）改放上方。"""
    def ts(x):
        h, x = divmod(x, 3600)
        m, s = divmod(x, 60)
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"
    top_until = LEAD + STARTS[1]
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
             "MarginR, MarginV, Encoding",
             # 字型與粗細、外框、左右邊界照作者 make_jaen_subs.py（Segoe UI 粗體、邊框 2、陰影 2、左右 120）；
             # 這支只有一行英文，用它上面那行的字級 46。
             # 跟動畫不同：底下是淺粉色的 app 畫面、還有字 → 白字細邊看不清（抽格），改半透明深色底框（BorderStyle 3）
             "Style: Narr,Segoe UI,46,&H00FFFFFF,&H00FFFFFF,&H38141018,&H38141018,-1,0,0,0,100,100,0,0,3,10,0,2,120,120,40,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    # 片尾第三世那段也自帶日英字幕在底部 → 那一句旁白也放上方
    end_from = LEAD + STARTS[-1]
    end_until = end_from + duration(HERE / "旁白" / "_join" / f"n{ROWS[-1][0]:02d}.wav") + IN_GAP
    for a, b, p in cues:
        pos = "{\\an8}" if a < top_until or end_from - 0.1 <= a < end_until else ""
        lines.append(f"Dialogue: 0,{ts(a)},{ts(b)},Narr,,0,0,0,,{pos}{p}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


if __name__ == "__main__":
    segs = [seg_open(0), seg_app(1, 2), seg_app(2, 3), seg_app(3, 4), seg_app(4, 5), seg_mouth(5),
            seg_app(6, 7), seg_app(7, 8), seg_title(8)]
    for s in segs:
        print(f"{s.name} {duration(s):.2f}s")
    # 畫面：全部接起來（第一段有聲音，其他段無聲 → 先只接畫面）
    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    video = WORK / "video.mp4"
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v", "-c", "copy", str(video)])
    # 聲音：開場片的原聲（壓低，旁白進來後再降）＋旁白（延後 LEAD 秒）
    total = duration(video)
    OUT.mkdir(exist_ok=True)
    final = OUT / "greenlight_local_demo.mp4"
    # 旁白英文字幕燒進畫面（ffmpeg 的 ass 濾鏡吃不下中文路徑 → 在 WORK 裡用相對檔名）
    cues = narration_cues()
    ass_file(cues, WORK / "narration.ass")
    (OUT / "narration_en.srt").write_text("".join(
        f"{j}\n{a // 3600:02.0f}:{a % 3600 // 60:02.0f}:{a % 60:06.3f}".replace(".", ",") +
        f" --> {b // 3600:02.0f}:{b % 3600 // 60:02.0f}:{b % 60:06.3f}".replace(".", ",") + f"\n{p}\n\n"
        for j, (a, b, p) in enumerate(cues, 1)), encoding="utf-8")
    # 燒字幕用作者現成的 burn_subs.py（處理過 Windows 路徑、-ss 位置兩個地雷，燒完自己驗）—— 只呼叫、不改它
    subbed = WORK / "video_subs.mp4"
    import sys
    subprocess.run([sys.executable, str(WS / "ComfyUI" / "aibao_scripts" / "burn_subs.py"),
                    str(video), str(WORK / "narration.ass"), "--out", str(subbed), "--crf", "18"], check=True)
    for a, b, p in cues:
        print(f"  {a:6.2f}-{b:6.2f}  {p}")
    run(["-i", str(subbed), "-i", str(segs[0]), "-i", str(NARR), "-filter_complex",
         f"[1:a]volume='if(lt(t,{LEAD}),0.9,0.22)':eval=frame,apad,atrim=0:{total:.3f}[bgm];"
         f"[2:a]adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},aformat=channel_layouts=stereo,apad,atrim=0:{total:.3f}[nar];"
         "[bgm][nar]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(final)])
    print(f"done {final} {duration(final):.2f}s")
