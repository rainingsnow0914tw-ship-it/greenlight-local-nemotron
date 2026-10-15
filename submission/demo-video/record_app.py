"""錄評審影片的 app 畫面：真的打開打包好的 Greenlight Local EXE，一列旁白錄一段。

沿用 nebius-director/video/record_demo.py 的做法（Playwright 錄真的操作），差別：
- 每一列（demo-video-script.md 的九列裡需要錄 app 的那六列）各錄一支，剪接時不用找時間點；
  導航（切英文、點進專案）錄在每支的開頭，ready.json 記下「從第幾秒開始是要用的畫面」，剪接時從那裡切。
- 只看不改：診斷修法只把滑鼠停在「Apply」上，不按（按了會改到測試專案的資料）。

先開 EXE：GREENLIGHT_HOME=%TEMP%\\gl_lang GreenlightLocal.exe --server --url-file %TEMP%\\gl_url.txt
用法：.venv-rec/Scripts/python record_app.py [列號 ...]   （不給就六列全錄）
輸出：錄影/<列號>.webm＋錄影/ready.json
"""
import json
import os
import pathlib
import shutil
import sys
import time

from playwright.sync_api import sync_playwright

URL = pathlib.Path(os.environ["TEMP"], "gl_url.txt").read_text(encoding="utf-8").strip()
OUT = pathlib.Path(__file__).resolve().parent / "錄影"
OUT.mkdir(exist_ok=True)
W, H = 1920, 1080         # 視窗＝錄影大小；頁面用 CSS zoom 放大 1.5 倍（版面等於 1280x720、字清楚）
ZOOM = 1.5               # Playwright 的錄影不會照 device_scale_factor 縮放：視窗 1280、錄 1920 只會在旁邊補灰邊（實測）
ROW_ZOOM = {7: 1.15}        # 台詞表欄位多：1.5 倍時一句就佔滿整個畫面、日文每幾個字就折行（抽格）
# 每列要的長度（秒）＝旁白時間表的列長，多錄 3 秒給剪接留餘裕
_T = json.loads((pathlib.Path(__file__).resolve().parent / "旁白" / "starts.json").read_text(encoding="utf-8"))
_S = _T["starts"] + [_T["total"]]
NEED = {r: round(_S[r] - _S[r - 1], 2) for r in (2, 3, 4, 5, 7, 8)}   # 每列要錄的長度＝旁白那一列的長度


def scroll_to(page, text, block="start", wait=1800):
    """平滑捲到某個標題（只用畫面上的英文字當錨點，不靠 class 名）。"""
    page.get_by_text(text, exact=False).first.evaluate(
        f"e => e.scrollIntoView({{behavior: 'smooth', block: '{block}'}})")
    page.wait_for_timeout(wait)


def glide(page, total_px, seconds):
    """慢慢往下捲：每 50ms 捲一點，總共捲 total_px，用掉 seconds 秒。"""
    # 照牆上時間捲（每次 wheel 本身也花時間，照步數算會拖長；第 3 列試錄 18 秒拖成 32 秒）
    t0, done = time.time(), 0.0
    while (el := time.time() - t0) < seconds:
        target = total_px * el / seconds
        page.mouse.wheel(0, target - done)
        done = target
        page.wait_for_timeout(40)


def home(page):
    page.goto(URL, wait_until="networkidle")
    page.get_by_role("button", name="EN", exact=True).click()
    page.wait_for_timeout(800)


def project(page):
    home(page)
    page.get_by_text("第10章 場景61").first.click()
    page.wait_for_timeout(1500)


def segment(page):
    project(page)
    page.get_by_role("button").filter(has_text="洗手台").first.click()
    # 等段落頁真的載完（診斷區塊最後才出來）再開始錄：第 5 列一次找不到「possible fixes」逾時
    page.get_by_text("Post-render checks (measured locally)").first.wait_for(state="attached", timeout=60000)
    page.wait_for_timeout(1500)


# ── 每一列：(準備＝不入鏡的導航, 入鏡動作) ─────────────────────────────
def row2_prep(page):
    home(page)


def row2(page):
    # 旁白：「So I started making my own. Drop in a story.」停在拖放區 → 「Get a finished film…」點進專案往下帶
    page.mouse.move(1140, 570)                       # 停在「拖放劇本」區
    page.wait_for_timeout(6000)
    page.get_by_text("第10章 場景61").first.hover()
    page.wait_for_timeout(1200)
    page.get_by_text("第10章 場景61").first.click()
    page.wait_for_timeout(2500)
    glide(page, 2100, 11.5)


def row3(page):
    scroll_to(page, "Shot list by the director (Nemotron)")
    page.wait_for_timeout(2500)
    glide(page, 1350, 12)
    page.wait_for_timeout(2000)


def row4(page):
    scroll_to(page, "Post-render checks (measured locally)", block="start")
    page.wait_for_timeout(5000)
    glide(page, 675, 5)
    page.wait_for_timeout(4000)


def row5(page):
    scroll_to(page, "possible fixes", block="start")
    page.wait_for_timeout(3000)
    glide(page, 1050, 9)
    apply = page.get_by_role("button", name="Apply this fix")
    if apply.count():
        apply.last.scroll_into_view_if_needed()
        apply.last.hover()                          # 只停不按
    page.wait_for_timeout(5000)


def row7(page):
    """學語言：停在台詞表（原句 → 導過的日文＋讀音、語體、中英字幕、為什麼這樣說）→ 字幕預覽 → 學習筆記。
    停在台詞表和學習筆記上，不要只捲過回合紀錄。"""
    scroll_to(page, "Line director (Nemotron · study mode)")
    page.wait_for_timeout(1200)
    page.get_by_text("Why it is said this way").first.evaluate(
        "e => e.closest('table').scrollIntoView({behavior: 'smooth', block: 'start'})")
    page.wait_for_timeout(4000)                     # 第 1～2 句：原句、日文、讀音、語體、中英字幕、為什麼
    glide(page, 1500, 9)                            # 慢慢往下帶過幾句
    scroll_to(page, "Subtitle preview (click a line)", block="start")
    page.wait_for_timeout(3500)
    scroll_to(page, "Study notes for this part", block="start")
    page.wait_for_timeout(3000)
    glide(page, 700, 5)
    page.wait_for_timeout(2000)


def row8(page):
    scroll_to(page, "Finish: trim → upscale → transitions → assemble → subtitles")
    page.wait_for_timeout(5000)
    page.get_by_role("button", name="Settings", exact=True).click()
    page.wait_for_timeout(1500)
    scroll_to(page, "Knowledge (editable any time)")
    page.wait_for_timeout(5500)


ROWS = {2: (row2_prep, row2), 3: (segment, row3), 4: (segment, row4), 5: (segment, row5),
        7: (segment, row7), 8: (segment, row8)}

if __name__ == "__main__":
    want = [int(a) for a in sys.argv[1:]] or list(ROWS)
    ready_file = OUT / "ready.json"
    ready = json.loads(ready_file.read_text(encoding="utf-8")) if ready_file.exists() else {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for r in want:
            prep, act = ROWS[r]
            tmp = OUT / f"_tmp{r}"
            shutil.rmtree(tmp, ignore_errors=True)
            ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(tmp),
                                      record_video_size={"width": W, "height": H})
            ctx.add_init_script(f"document.addEventListener('DOMContentLoaded', () => "
                                f"{{ document.documentElement.style.zoom = '{ROW_ZOOM.get(r, ZOOM)}' }})")
            page = ctx.new_page()
            t0 = time.time()
            prep(page)
            start = round(time.time() - t0, 2)
            act(page)
            took = round(time.time() - t0 - start, 2)
            video = page.video.path()
            ctx.close()
            dst = OUT / f"{r}.webm"
            shutil.move(video, dst)
            shutil.rmtree(tmp, ignore_errors=True)
            ready[str(r)] = {"start": start, "seconds": took, "need": NEED[r]}
            ready_file.write_text(json.dumps(ready, indent=1), encoding="utf-8")
            flag = "OK" if took >= NEED[r] else "X 太短"
            print(f"row {r}: usable from {start}s, {took}s of action (need {NEED[r]}) {flag}", flush=True)
        browser.close()
