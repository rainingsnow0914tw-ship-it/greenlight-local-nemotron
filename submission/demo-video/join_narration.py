"""把 n01～n16 接成一整條旁白，並列出每一列（demo-video-script.md 的九列）的開始時間。

每段：去掉尾巴的靜音 → 音量統一（loudnorm -16 LUFS）。同一列裡兩段之間留 0.35 秒，列與列之間留 1 秒。
產物：旁白/narration_full.wav、旁白/時間表.md
"""
import json, pathlib, subprocess, wave

HERE = pathlib.Path(__file__).resolve().parent / "旁白"
FF = __import__("imageio_ffmpeg").get_ffmpeg_exe()
CHUNKS = json.loads((HERE / "narration_chunks.json").read_text(encoding="utf-8"))
# 腳本九列（唯一的家：build_demo.py、record_app.py 都讀這裡寫出的 starts.json）
# 第 7 列（學語言）加第 17 段，把台詞表和學習筆記講清楚
# 開場是作者第一人稱的痛點（看動畫長大、學日文卡在中間、自己做教材）：18～20 新生、n03 不用；
#       片尾加 21（現在我做自己的動畫、從每一句學）
ROWS = [[18, 19], [20, 1, 2, 4], [5, 6], [7, 8], [9, 10], [11], [12, 13, 17], [14, 15], [21, 16]]
ON_SCREEN = ["開場：第 6 鏡學語言版", "首頁丟劇本、專案頁", "導演進度、關卡、製作人意見", "第 3 鏡查證卡",
             "診斷 A/B/C 修法", "嘴閉 vs 嘴動、症狀庫", "學語言模式表格", "成片面板、知識資料夾", "片尾標題"]
IN_GAP, ROW_GAP, SR = 0.35, 1.0, 48000
WORK = HERE / "_join"
WORK.mkdir(exist_ok=True)


def clean(i: int) -> pathlib.Path:
    out = WORK / f"n{i:02d}.wav"
    trail = "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.25,areverse"
    # 兩步分開跑：areverse＋loudnorm 串在同一條濾鏡，n05 會讓 ffmpeg 7.1 斷言失敗（best_input >= 0）
    tmp = WORK / f"n{i:02d}_trim.wav"
    subprocess.run([FF, "-v", "error", "-y", "-i", str(HERE / f"n{i:02d}.wav"), "-af", trail, str(tmp)], check=True)
    subprocess.run([FF, "-v", "error", "-y", "-i", str(tmp), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ac", "1", "-ar", str(SR), str(out)], check=True)
    return out


def secs(p: pathlib.Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def silence(t: float) -> pathlib.Path:
    out = WORK / f"sil_{int(t * 1000)}.wav"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{t}", str(out)], check=True)
    return out


parts, t, table = [], 0.0, []
for r, row in enumerate(ROWS):
    if r:
        parts.append(silence(ROW_GAP)); t += ROW_GAP
    table.append((r + 1, t, row))
    for k, i in enumerate(row):
        if k:
            parts.append(silence(IN_GAP)); t += IN_GAP
        p = clean(i); parts.append(p); t += secs(p)

lst = WORK / "list.txt"
lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
full = HERE / "narration_full.wav"
subprocess.run([FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(full)], check=True)

fmt = lambda s: f"{int(s // 60)}:{s % 60:05.2f}"  # noqa: E731
lines = ["# 旁白時間表（narration_full.wav）", "", f"總長 {fmt(secs(full))}", "",
         "| 列 | 開始 | 畫面 | 旁白 |", "|---|---|---|---|"]
for n, start, row in table:
    lines.append(f"| {n} | {fmt(start)} | {ON_SCREEN[n - 1]} | {' '.join(CHUNKS[i - 1] for i in row)} |")
(HERE / "時間表.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
(HERE / "starts.json").write_text(json.dumps({"rows": ROWS, "starts": [round(s, 3) for _, s, _ in table],
                                              "total": round(secs(full), 3)}, indent=1), encoding="utf-8")
print("\n".join(lines))
