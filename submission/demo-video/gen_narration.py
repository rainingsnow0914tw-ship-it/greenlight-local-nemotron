"""評審影片的英文旁白：用 MiniMax H3（本機 ComfyUI）生，只取聲音。

用 H3 生、只取聲音，比一般 TTS 自然；聲音是年輕、專業的女聲。
做法：畫面＝年輕女主持人在安靜的錄音室對鏡頭講（有人在講，聲音最乾淨、最像真人旁白）；
第一段定聲音，之後每段把第一段的聲音當參考音訊 <Audio 1>（H3 ReferenceToVideo 的 ref_audios，節點說明：提示詞用同樣的標記），
維持同一個人。每段生完用 whisper 對英文稿。

  python gen_narration.py 2          → 生第 2 段（沒有參考聲音時就是「定聲音」那一段）
  python gen_narration.py 1 3 4 ...  → 有 narrator_ref.wav 之後，其他段都用它當參考
"""
import json, pathlib, random, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[2] / "backend"   # greenlight-local/backend
sys.path.insert(0, str(ROOT))
from app.engines import comfy  # noqa: E402

URL = "http://127.0.0.1:8188"
HERE = pathlib.Path(__file__).resolve().parent / "旁白"
HERE.mkdir(exist_ok=True)
CHUNKS = json.loads((HERE / "narration_chunks.json").read_text(encoding="utf-8"))   # 原本放 %TEMP%，會被清掉 → 搬進 旁白/
REF = HERE / "narrator_ref.wav"
FF = __import__("imageio_ffmpeg").get_ffmpeg_exe()

PROMPT = """實拍電影畫面，35mm 底片拍攝，柔和的攝影棚燈光，乾淨的淺灰色背景。
人物：一位二十多歲的年輕女性主持人，黑色及肩直髮，穿簡單的米白色襯衫，坐在桌前，正面對著鏡頭。
場景：安靜的錄音室，背景沒有任何文字、沒有其他人。
鏡頭：中近景，鏡頭固定不動。
表演：她看著鏡頭，語氣自然、專業、親切地說話，說完輕輕微笑。
{voice}說英語台詞︰「{text}」"""
VOICE_FIRST = "她用年輕女性清晰、專業、溫和的聲音"
VOICE_REF = "她用參考音訊 <Audio 1> 裡同一個年輕女性的聲音"


def render(idx: int):
    text = CHUNKS[idx - 1]
    secs = min(10.0, round(len(text.split()) / 2.5 + 1.5, 1))
    use_ref = REF.exists()
    prompt = PROMPT.format(voice=VOICE_REF if use_ref else VOICE_FIRST, text=text)
    wf = comfy.build_workflow(prompt, [], seed=random.randint(1, 2**48), duration_seconds=secs, megapixels=0.92,
                              prefix=f"greenlight/narration/n{idx:02d}")
    if use_ref:
        name = comfy.upload_refs(URL, [str(REF)])[0]
        wf["960"] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        wf["140:131"]["inputs"]["ref_audios.ref_audio_0"] = ["960", 0]
    print(f"n{idx:02d} {secs}s ref={use_ref}: {text}", flush=True)
    video, info = comfy.render(URL, wf)
    mp4 = HERE / f"n{idx:02d}.mp4"
    mp4.write_bytes(video)
    wav = HERE / f"n{idx:02d}.wav"
    subprocess.run([FF, "-v", "error", "-y", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "48000", str(wav)], check=True)
    print(f"  done {info['render_seconds']}s → {wav.name}", flush=True)
    return wav


def heard_ok(wav: pathlib.Path, text: str) -> tuple[bool, float, str]:
    """聽寫比對：有參考聲音時 H3 可能把參考音檔的內容也唸進去、或唸錯 → 生完就驗，不要一路錯到底。
    開頭多唸的（n03：「So thank you to Yao.」＝症狀庫 S02 開頭亂語）照稿子第一個字的時間剪掉，剪完再比一次。"""
    import difflib, re
    from app.verify import asr

    # whisper 把數字寫成阿拉伯數字（n05：「Twenty-five」聽成「25」，唸對卻判錯）→ 比對前把稿子的數字詞換成數字
    nums = {"twentyfive": "25", "nineteen": "19", "eight": "8", "four": "4", "one": "1"}
    def norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9]", "", s.lower().replace("-", ""))
        return s

    def norm_text(s: str) -> str:
        s = s.lower().replace("-", "")
        for k, v in nums.items():
            s = re.sub(rf"\b{k}\b", v, s)
        return norm(s)

    first, last = norm(text.split()[0]), norm(text.split()[-1])
    for _ in range(2):  # 開頭剪一次後重聽，還有多的（n04：「our feed.」）再剪一次
        words = asr.transcribe(str(wav), "en")
        start = next((a for w, a, _ in words if norm(w) == first), None)
        if not (start and start > 0.3):
            break
        cut = wav.with_name(wav.stem + "_cut.wav")
        subprocess.run([FF, "-v", "error", "-y", "-ss", f"{max(start - 0.12, 0):.2f}", "-i", str(wav), str(cut)], check=True)
        cut.replace(wav)
        print(f"  trimmed {start - 0.12:.2f}s of extra speech before '{text.split()[0]}'", flush=True)
    # 結尾多唸的：稿子最後一個字之後還有字 → 照最後一個字結束時間 +0.35 秒剪掉
    end = next((b for w, _, b in reversed(words) if norm(w) == last), None)
    if end is not None and words[-1][2] > end + 0.2:
        cut = wav.with_name(wav.stem + "_cut.wav")
        subprocess.run([FF, "-v", "error", "-y", "-i", str(wav), "-t", f"{end + 0.35:.2f}", str(cut)], check=True)
        cut.replace(wav)
        print(f"  trimmed extra speech after '{text.split()[-1]}' (kept {end + 0.35:.2f}s)", flush=True)
        words = asr.transcribe(str(wav), "en")
    heard = " ".join(w.strip() for w, _, _ in words)
    sim = difflib.SequenceMatcher(None, norm(heard), norm_text(text)).ratio()
    return sim >= 0.9, round(sim, 2), heard


if __name__ == "__main__":
    if sys.argv[1] == "--check":  # 只重驗已經生好的（剪頭尾＋比對），不重生
        for i in map(int, sys.argv[2:]):
            ok, sim, heard = heard_ok(HERE / f"n{i:02d}.wav", CHUNKS[i - 1])
            print(f"  check n{i:02d}: {'OK' if ok else 'X'} similarity {sim} heard: {heard}", flush=True)
        sys.exit(0)
    for i in map(int, sys.argv[1:]):
        for attempt in (1, 2):
            wav = render(i)
            ok, sim, heard = heard_ok(wav, CHUNKS[i - 1])
            print(f"  check n{i:02d} try {attempt}: similarity {sim} heard: {heard}", flush=True)
            if ok:
                break
        else:
            print(f"STOP n{i:02d} still wrong after 2 tries", flush=True)
            sys.exit(1)
    print("ALL DONE", flush=True)
