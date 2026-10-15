"""Devpost 截圖：真的打開打包好的 EXE，捲到每個賣點的位置截圖（1920x1080）。

兩個 EXE 同時開（都只讀畫面，不按任何會改資料的按鈕）：
  gl_url.txt     → 測試專案原版（學習者語言：中文優先），截導演、查證、診斷、成片
  gl_url_en.txt  → 同一份專案的副本，學習者語言改英文優先、重跑台詞導演：
                   「為什麼這樣說」和學習筆記是英文，給看英文的評審看文法解說
用法：demo-video/.venv-rec/Scripts/python screenshots.py
"""
import os
import pathlib

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parent / "screenshots"
OUT.mkdir(exist_ok=True)
TMP = pathlib.Path(os.environ["TEMP"])
URL = (TMP / "gl_url.txt").read_text(encoding="utf-8").strip()
URL_EN = (TMP / "gl_url_en.txt").read_text(encoding="utf-8").strip()
W, H = 1920, 1080


def open_segment(page, url):
    page.goto(url, wait_until="networkidle")
    page.get_by_role("button", name="EN", exact=True).click()
    page.wait_for_timeout(600)
    page.get_by_text("第10章 場景61").first.click()
    page.wait_for_timeout(1200)
    page.get_by_role("button").filter(has_text="洗手台").first.click()
    page.get_by_text("Post-render checks (measured locally)").first.wait_for(state="attached", timeout=60000)
    page.wait_for_timeout(1500)


def top(page, text, offset=0, closest=None):
    """把某段標題捲到畫面頂端（offset：再往下捲幾 px）。"""
    sel = f"e => (e.closest('{closest}') || e)" if closest else "e => e"
    page.get_by_text(text, exact=False).first.evaluate(f"x => ({sel})(x).scrollIntoView({{block: 'start'}})")
    if offset:
        page.mouse.wheel(0, offset)
    page.wait_for_timeout(900)


def shot(page, name):
    page.screenshot(path=str(OUT / name))
    print("saved", name)


def session(browser, url, zoom, jobs):
    ctx = browser.new_context(viewport={"width": W, "height": H})
    ctx.add_init_script(f"document.addEventListener('DOMContentLoaded', () => "
                        f"{{ document.documentElement.style.zoom = '{zoom}' }})")
    page = ctx.new_page()
    open_segment(page, url)
    for fn in jobs:
        fn(page)
    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch()
    # 文法解說（英文版副本）：台詞表、學習筆記
    session(b, URL_EN, 1.15, [
        lambda pg: (top(pg, "Why it is said this way", closest="table"), shot(pg, "05_line_director_grammar_notes.png")),
        lambda pg: (top(pg, "Study notes for this part"), shot(pg, "06_study_notes_patterns.png")),
    ])
    # 原版：導演迴圈、查證、診斷、成片
    session(b, URL, 1.25, [
        lambda pg: (top(pg, "Shot list by the director (Nemotron)"), shot(pg, "07_director_loop_gates_producer.png")),
        lambda pg: (top(pg, "Post-render checks (measured locally)"), shot(pg, "08_shot_checks_measured.png")),
        lambda pg: (top(pg, "possible fixes"), shot(pg, "09_diagnosis_fixes_with_costs.png")),
        lambda pg: (top(pg, "Finish: trim"), shot(pg, "10_finishing_pipeline.png")),
    ])
    b.close()
