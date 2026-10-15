"""接片與字幕 —— 第 9 關。

把逐鏡生成的片段接成一支完整影片，並產出字幕檔。

## 為什麼字幕是外掛不是燒進畫面

    外掛字幕之後隨時可以改成多語言版本：
    影片只生一次，字幕想出幾種語言都行 ——
    中文版、英文版、日文版共用同一批片段，只換一個文字檔。
    燒進畫面的話，換一種語言就要把整支重新輸出一次。

    這也是 `constraints.yaml` 的 `no_subtitle_burn_in` 一直在阻止
    Veo 自己燒字幕的原因：**燒進去就改不掉了**。
    那個決定權留到最後一刻。

## 時間軸不用猜

    每一鏡的**實際**秒數都存在 `clips` 裡 —— 那是從 mp4 的 mvhd box
    量出來的，不是請求的值。累加就是每句台詞的起訖時間。

    前提是「回 200 不代表它照做了」：
    如果用請求的秒數而不是實測值，
    每一鏡零點零幾秒的誤差累積下來，後段鏡頭的字幕就會對不上嘴。

## 為什麼用 ffmpeg 的 concat demuxer

    Veo 同一個檔位輸出的編碼參數一致，可以直接串接不重新編碼 ——
    快、無損、不會二次壓縮。編碼真的不一致時退回重編碼路徑。
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import media_store

logger = logging.getLogger(__name__)

# ffmpeg 的統一上限。
#
# `subprocess.run` 的 timeout 跟 `requests` 的不一樣：它會真的 kill 掉進程，
# 是名副其實的總時間。但**它一樣必須小於 Cloud Run 的 900 秒請求上限** ——
# 大於等於 900 的值永遠等不到：
# 平台會先砍掉整個請求，而平台砍掉的話 except 跑不到、
# 成品狀態存不下、使用者只拿到一個 504。
#
# 600 秒的依據：接片實測 94.7 秒（preset veryfast，真實檔案），
# 六倍餘裕。跑超過十分鐘的話問題不在等得不夠久，在別的地方。
_FFMPEG_TIMEOUT_SECONDS = 600


class AssembleError(RuntimeError):
    """接片失敗。"""


def font_report() -> dict[str, object]:
    """容器裡有沒有可用的字型。**$0，唯讀。**

    為什麼要有：
       「有沒有裝字型」是**部署期**的事實，不是每次燒字幕才該去猜的東西。
       沒有字型時 ffmpeg 照樣 exit 0、後端照樣回 200，
       使用者拿到的是一支畫面上一個字都沒有的成品 ——
       整條鏈路沒有任何一層察覺出問題。

       所以把它變成**開機就看得見**的狀態：啟動時記一行 log、
       health 也能問。這樣換 base image 而字型沒跟上的時候，
       是部署當下就發現，不是使用者交片前一刻才發現。

    **「有字型」不等於「用得到字型」**：
       只數字型檔的話，health 會回報有字型、有 CJK ——
       一個漂亮的綠燈。但 ffmpeg 那邊實際的錯誤可能是
       `Fontconfig error: Cannot load default config file`：
       字型檔在，**fontconfig 的設定檔不在**，libass 一個字都畫不出來。
       （`--no-install-recommends` 裝字型時會把 fontconfig 跳過。）

       所以這裡兩個都要看。只檢查其中一半的檢查，比沒有檢查更危險 ——
       它會給出一個看起來已經驗過的綠燈。
    """
    roots = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    files: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for ext in ("*.ttc", "*.ttf", "*.otf", "*.otc"):
            files.extend(p.name for p in root.rglob(ext))

    cjk = [f for f in files if "cjk" in f.lower() or "noto" in f.lower()]
    has_config = any(
        Path(p).exists()
        for p in ("/etc/fonts/fonts.conf", "/usr/local/etc/fonts/fonts.conf")
    )
    return {
        "total": len(files),
        "cjk": len(cjk),
        "fontconfig": has_config,
        # 兩者缺一，字幕就是畫不出來 —— 用一個欄位講結論，不要讓人自己推。
        "usable": bool(files and cjk and has_config),
        "samples": sorted(files)[:5],
    }


@dataclass
class Cue:
    """一句字幕。"""

    index: int
    start: float
    end: float
    text: str
    speaker: str = ""


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover
        raise AssembleError(
            "缺少 imageio-ffmpeg。執行 pip install -r requirements.txt。"
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _timestamp(seconds: float) -> str:
    """秒數 → SRT 時間格式 `HH:MM:SS,mmm`。"""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(clips: list, shots: list) -> list[Cue]:
    """依實際片長排出字幕時間軸。

    Args:
        clips: 已生成的 ClipRecord，順序就是成片順序
        shots: 對應的 Shot（提供台詞與說話者）

    用 `clip.seconds`（實測）不用 `shot.base_duration_seconds`（請求值）。
       每一鏡零點零幾秒的誤差累積到十幾鏡之後就會明顯對不上。
    """
    by_id = {s.shot_id: s for s in shots}
    cues: list[Cue] = []
    cursor = 0.0
    index = 1

    for clip in clips:
        shot = by_id.get(clip.shot_id)
        duration = float(clip.seconds or 0)
        if shot and shot.dialogue:
            text = shot.dialogue.strip()
            cues.append(
                Cue(
                    index=index,
                    start=cursor,
                    # 字幕結束時間比鏡頭早 0.15 秒：
                    #    緊貼切點的字幕會在下一鏡開頭閃一格，看起來像殘影。
                    #    這是剪接的慣例，不是技術限制。
                    end=cursor + max(duration - 0.15, 0.5),
                    text=text,
                    speaker=(shot.speaker or ""),
                )
            )
            index += 1
        cursor += duration

    return cues


def _is_cjk(ch: str) -> bool:
    """中日韓表意文字、假名、全形標點。"""
    code = ord(ch)
    return (
        0x3000 <= code <= 0x303F      # 全形標點
        or 0x3040 <= code <= 0x30FF   # 平假名、片假名
        or 0x4E00 <= code <= 0x9FFF   # 漢字
        or 0xFF00 <= code <= 0xFFEF   # 全形英數與標點
    )


def wrap_subtitle(text: str, *, cjk_per_line: int = 18, latin_per_line: int = 42) -> str:
    """把一句字幕折成多行。

    **為什麼一定要在這裡自己折**：

       ffmpeg 燒錄時會留一行看起來無害的訊息 ——
       `libass wasn't built with ASS_FEATURE_WRAP_UNICODE support`。
       那不是雜訊，那正是**中文字幕衝出畫面**的原因：
       libass 沒有 unicode 斷行能力，只能在空白處換行，
       而中文與日文句子**沒有空格** —— 於是整句不換行，一路衝出邊界。

       英文看起來正常，純粹是因為它有空格。所以這個 bug 會讓
       三語裡的**兩語**壞掉。

       修在自己這一層而不是換一個 libass：**硬需求不要交給
       控制不了的東西**（跟「機率性輸出保證不了硬需求，所以 code 層 clamp」
       同一條原則）。SRT 是這裡產的，折行也該由這裡決定。

    標點不落行首：中文排版裡「，。！？」出現在行首是明顯的瑕疵，
       所以折行點往前挪一格，把標點留在上一行尾。

    Args:
        text: 一句字幕
        cjk_per_line: 中日文每行字數（18 是字幕的常見上限，再多會擋畫面）
        latin_per_line: 拉丁文字每行字元數
    """
    text = text.strip()
    if not text:
        return text

    cjk_count = sum(1 for ch in text if _is_cjk(ch))
    is_cjk_line = cjk_count * 2 >= len(text)  # 過半是 CJK 就當 CJK 處理

    if not is_cjk_line:
        # 有空格可斷，照單字折
        words, lines, cur = text.split(), [], ""
        for word in words:
            if cur and len(cur) + 1 + len(word) > latin_per_line:
                lines.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}" if cur else word
        if cur:
            lines.append(cur)
        return "\n".join(lines)

    # CJK：優先在標點處斷，語意才不會被切斷。
    #
    # 純按字數硬折會產生「我後來一／直記得」這種斷在詞中間的行 ——
    #    技術上沒錯，讀起來很差。字幕是給人看的，斷句要落在語意邊界上。
    #    所以先照標點切成小段，再貪婪地把小段組成不超長的行；
    #    只有單一小段自己就超長時才退回硬折。
    import re as _re

    pieces = [p for p in _re.split(r"(?<=[，。、！？；：])", text) if p]
    lines: list[str] = []
    cur = ""
    for piece in pieces:
        if len(cur) + len(piece) <= cjk_per_line:
            cur += piece
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while len(piece) > cjk_per_line:  # 沒有標點可斷的超長段落
            lines.append(piece[:cjk_per_line])
            piece = piece[cjk_per_line:]
        cur = piece
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def to_srt(cues: list[Cue]) -> str:
    """產出 SRT 檔內容。

    SRT 而不是 VTT：播放器支援度最廣（VLC、YouTube、剪輯軟體都吃），
    而且純文字、使用者改得動。

    折行在這裡做，見 `wrap_subtitle()` —— 燒錄時 libass 折不動中日文。
       外掛字幕也一起受益：播放器對超長行的處理同樣不一致。
    """
    blocks = []
    for cue in cues:
        blocks.append(
            f"{cue.index}\n"
            f"{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n"
            f"{wrap_subtitle(cue.text)}\n"
        )
    return "\n".join(blocks)


def concat(project_id: str, clips: list, output_name: str) -> tuple[str, float]:
    """把**鏡頭片段**接成一支段落成片。回傳 (media_store URI, 總長秒數)。

    先試 concat demuxer（不重新編碼，快且無損）；
    編碼參數不一致導致失敗時，退回重新編碼。
    """
    sources = [
        (c.uri, float(c.seconds or 0), c.shot_id) for c in clips if c.uri
    ]
    return concat_sources(project_id, sources, output_name)


def concat_sources(
    project_id: str,
    sources: list[tuple[str, float, str]],
    output_name: str,
) -> tuple[str, float]:
    """把任意一串影片檔按順序接起來。回傳 (media_store URI, 總長秒數)。

    為什麼要有這個函式？
        接片有兩種尺度 —— 鏡頭接成段落、段落接成全片。
        ffmpeg 那段邏輯（concat demuxer、失敗退回重編碼、
        路徑檢查、存進媒體庫）兩邊一模一樣，只有輸入的形狀不同：
        clip 有 `.shot_id`、segment 有 `.order`，欄位名對不上。

        若用 `concat()` 加一個假的 clip-like 物件混過去，
        會讓「輸入必須長得像 clip」這個隱含契約散進呼叫端。
        所以抽出這一層，兩種尺度各自組 tuple。

    設計來源：全片合成需求。

    不能刪：`concat()` 只是它的一層薄殼。

    走 `-c copy` 的前提是所有來源編碼一致。Veo 同檔位輸出一致、
       段落成片也都是同一條路產出的，所以正常會走快路徑。
       真的不一致時自動退回重編碼（慢很多但不會失敗）。

    Args:
        project_id: 專案 ID，決定輸出存到哪個媒體目錄。
        sources: `(uri, seconds, label)` 串列。label 只用在錯誤訊息裡，
            讓「找不到檔案」講得出是哪一段。
        output_name: 輸出檔名主體（不含副檔名）。

    Returns:
        (media_store URI, 總長秒數)。

    Raises:
        AssembleError: 沒有可用來源、來源檔案不存在、或 ffmpeg 兩條路都失敗。
    """
    exe = _ffmpeg()
    total = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        paths: list[Path] = []

        for i, (uri, seconds, label) in enumerate(sources):
            if not uri:
                continue

            # 兩種來源都要吃：
            #    本機 `local://` 直接用原路徑（不複製，省一次 I/O）；
            #    雲端 `gs://` 先抓下來放暫存 —— ffmpeg 讀不了 GCS URI。
            #
            #    只走 path_for() 的話，那支對 gs:// 一律回 None，
            #    於是**在雲端接片一定失敗**、訊息還是「找不到片段檔案」，
            #    看起來像檔案掉了，其實是缺了雲端分支。
            path = media_store.path_for(uri)
            if path is None or not path.exists():
                loaded = media_store.load(uri)
                if loaded is None:
                    raise AssembleError(f"找不到片段檔案：{label}（{uri}）")
                data, _mime = loaded
                path = Path(tmp) / f"src{i:04d}.mp4"
                path.write_bytes(data)

            paths.append(path)
            total += float(seconds or 0)

        if not paths:
            raise AssembleError("沒有任何已生成的片段可以接。")

        listfile = Path(tmp) / "list.txt"
        # ffmpeg 的 concat demuxer 要求這種格式，路徑裡的單引號要跳脫
        listfile.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in paths),
            encoding="utf-8",
        )
        out = Path(tmp) / "out.mp4"

        fast = [
            exe, "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(listfile),
            "-c", "copy",
            "-movflags", "+faststart",
            "-y", str(out),
        ]
        result = subprocess.run(fast, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS)

        if result.returncode != 0 or not out.exists():
            logger.warning("concat demuxer 失敗，改用重新編碼：%s", result.stderr[:200])
            # 重新編碼路徑：慢很多，但能吃下編碼不一致的片段
            slow = [
                exe, "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(listfile),
                # preset 理由同 burn_subtitles 的修復記錄：來源已經被 Veo
                # 壓過一次，母帶級參數換不到看得見的畫質，只換到超時風險。
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-y", str(out),
            ]
            result = subprocess.run(slow, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS)
            if result.returncode != 0 or not out.exists():
                raise AssembleError(f"接片失敗：{result.stderr[:400]}")

        data = out.read_bytes()

    uri = media_store.save(project_id, output_name, data, "video/mp4")
    from . import exports

    exports.publish(project_id, output_name, data)      # Greenlight Local：另存一份到使用者選的成品資料夾
    return uri, round(total, 2)


def mux_subtitles(video: bytes, srt_by_lang: dict[str, str]) -> bytes:
    """把字幕**內嵌成 mp4 的字幕軌**（可開關、可多語）。

    為什麼優先用內嵌而不是燒錄：

        內嵌  檔案自帶字幕，播放器可以開關、可以切語言，**不重新編碼**、
              不破壞畫面。一個檔案裝三種語言。
        燒錄  畫死在畫面上，到哪都看得到，但改不掉、只能一種語言，
              而且要重新編碼整支（畫質會掉一層）。

        外掛 .srt 的缺點是檔案分家、容易搞丟；內嵌解決了那個問題，
        又保留了「影片只生一次、字幕想幾種都行」的設計。

    mov_text 在 VLC / QuickTime / 剪輯軟體支援良好，
       但 Windows 內建的「電影與電視」不一定認得。真的要保證看得到，
       用 burn_subtitles。
    """
    exe = _ffmpeg()

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        src = base / "in.mp4"
        src.write_bytes(video)
        out = base / "out.mp4"

        cmd = [exe, "-hide_banner", "-loglevel", "error", "-i", str(src)]
        langs = []
        for lang, srt in srt_by_lang.items():
            if not srt.strip():
                continue
            path = base / f"{lang}.srt"
            path.write_text(srt, encoding="utf-8")
            cmd += ["-i", str(path)]
            langs.append(lang)

        if not langs:
            return video

        cmd += ["-map", "0:v", "-map", "0:a?"]
        for i, _ in enumerate(langs, start=1):
            cmd += ["-map", f"{i}:0"]

        # 影音直接複製，只有字幕需要轉成 mp4 認得的 mov_text
        cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]
        for i, lang in enumerate(langs):
            # ISO 639-2 三字母碼，播放器靠這個顯示語言名稱
            iso = {"zh": "chi", "en": "eng", "ja": "jpn"}.get(lang, "und")
            cmd += [f"-metadata:s:s:{i}", f"language={iso}"]
        cmd += ["-movflags", "+faststart", "-y", str(out)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS)
        if result.returncode != 0 or not out.exists():
            raise AssembleError(f"內嵌字幕失敗：{result.stderr[:400]}")
        return out.read_bytes()


def burn_subtitles(video: bytes, srt: str, *, font_size: int = 22) -> bytes:
    """把字幕**燒進畫面**。會重新編碼。

    只在「一定要看得到」的場合用 —— 例如對外展示的成片，
    觀看者不會自己去載字幕檔。

    這跟 `constraints.yaml` 的 no_subtitle_burn_in 不衝突：
       那條擋的是**讓 Veo 自己燒**（時機與內容都控制不了）。
       這裡是在最後一步、用可控的字型與時間軸自己燒。

    preset / crf 的選擇依據：
        在真實雲端環境實測，一支 35MB / 90 秒的成片
        用 `-preset medium -crf 18` 燒字幕要 212 秒；
        整部片是這個長度的三倍，會撞上 Cloud Run 請求逾時。
        那是**母帶級**設定，但輸入影片已經被 Veo 壓過一次了 ——
        拿高品質參數去重壓一份已經有損的來源，多花的時間換不到看得見的畫質。
        改成 `veryfast` / `crf 20` 之後同一支影片、同一台 Cloud Run 實測 94.7 秒
        （約快 2.2 倍）；整片約 280 秒，在 900 秒的請求上限之內。
    這個問題在本機測不到 —— 測試會把 burn 換成樁（真跑 ffmpeg 要好幾分鐘），
        所以測試通過不代表功能不會在真實資料上超時。
        **會重新編碼的路徑，一定要用真實檔案在雲端量一次。**
    不要為了省時間改用 `-c:v copy` —— 那不會燒字幕，只會複製原檔。
        燒字幕的本質就是重新編碼，能省的只有 preset。
    """
    exe = _ffmpeg()

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        src = base / "in.mp4"
        src.write_bytes(video)
        sub = base / "sub.srt"
        sub.write_text(srt, encoding="utf-8")
        out = base / "out.mp4"

        # subtitles filter 的路徑參數在 Windows 要特別處理：
        # 冒號是 filter 的分隔符號，反斜線是跳脫字元，兩個都要轉。
        escaped = sub.as_posix().replace(":", r"\:")
        # 明講字型：不指定的話 libass 用 fontconfig 的預設，
        #    而預設在不同 base image 上是不同的東西 —— 中文字幕最容易變豆腐塊。
        #    Noto Sans CJK TC 由 Dockerfile 的 fonts-noto-cjk 提供，
        #    一套字型同時吃中文、日文與拉丁字母。
        style = (
            f"FontName=Noto Sans CJK TC,FontSize={font_size},"
            f"PrimaryColour=&HFFFFFF,"
            f"OutlineColour=&H000000,BorderStyle=1,Outline=1.5,Shadow=0,"
            f"MarginV=28"
        )

        cmd = [
            exe, "-hide_banner",
            # warning 不是 error：字型問題是 **warning 級**，
            #    用 error 等級會把唯一的線索過濾掉，剩下一個乾淨的 exit 0。
            "-loglevel", "warning",
            "-i", str(src),
            "-vf", f"subtitles='{escaped}':force_style='{style}'",
            # preset/crf 的依據見上方 docstring —— 動之前先讀。
            #    來源影片已經被 Veo 壓過，這裡是第二次編碼，
            #    再高的品質參數也救不回第一次丟掉的資訊。
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy",
            "-movflags", "+faststart",
            "-y", str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS)
        if result.returncode != 0 or not out.exists():
            raise AssembleError(f"燒錄字幕失敗：{result.stderr[:400]}")

        # exit 0 不代表字燒上去了。
        #
        #    容器裡一個字型都沒有時，libass 渲染不出任何字元，
        #    但 ffmpeg **照樣回 0、照樣產出檔案** ——
        #    結果是一支編碼完成、畫面上一個字都沒有的影片，
        #    而後端從頭到尾認為自己成功了。
        #
        #    所以成功路徑也要讀 stderr。這條在字型齊全時永遠不會觸發；
        #    它存在的意義是「下次 base image 換掉、字型沒跟上」的時候，
        #    失敗要當場說出來，而不是變成一支沒有字的成品。
        # ffmpeg 說了什麼一律記下來。
        #
        #    "fontselect" 不能當失敗訊號（libass 成功時也印那個字）；
        #    訊息只塞進例外 → HTTPException 的 detail 也不夠，
        #    因為 detail 不會進 log，出問題時只看得到一個沒有內容的 500。
        #
        #    **先讓它可觀測，再談要不要擋。** 這行無條件記錄。
        if result.stderr:
            logger.warning("燒字幕 ffmpeg 訊息（rc=%d）：%s", result.returncode, result.stderr[:600])

        # 字型問題**不拋錯**，改成降級並回報。
        #
        #    若拋 AssembleError，會把一個「影片還在、只是沒字」的輕微問題，
        #    升級成「整個燒字幕功能 500、什麼都拿不到」——
        #    **比它要解決的問題更嚴重**。
        #
        #    跟 ClickHouse 連不上時的處理同一個原則：**降級但不靜默**。
        #    影片照給，caller 拿得到一個明確的警告。
        err = (result.stderr or "").lower()
        font_broken = "failed to find" in err or "no usable font" in err
        if font_broken:
            logger.error(
                "🔴 字幕字型缺失，燒出來的影片會沒有字：%s", result.stderr[:400]
            )

        return out.read_bytes(), font_broken
