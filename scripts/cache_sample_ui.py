"""範例畫面上 AI 寫的說明（段落摘要、製作人意見、解析備註…）先翻好英日文，評審沒金鑰也看得到。

AiText 是開頁面時才去 /api/ui/translate 翻：範例是唯讀、評審不一定有金鑰，沒翻過的就整句中文。
這支照使用者的方式把三個範例的專案頁＋段落頁用英文、日文各開一次，讓 app 自己翻、存進那個後端的快取，
最後把快取併回 sample/cache_ui_translate.json。台詞表「為什麼這樣說」另有 cache_sample_why.py。

先開一個有金鑰的後端（GREENLIGHT_HOME 放 settings.json），再跑：
  submission/demo-video/.venv-rec/Scripts/python scripts/cache_sample_ui.py <後端網址> <那個 GREENLIGHT_HOME>
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
URL, HOME = sys.argv[1], Path(sys.argv[2])
OUT = Path(__file__).resolve().parent.parent / "sample" / "cache_ui_translate.json"
LANG_BTN = {"en": "EN", "ja": "日本語"}
SAMPLES = ("Sample 1", "Sample 2", "Sample 3")
SAMPLE_JA = ("サンプル 1", "サンプル 2", "サンプル 3")


def settle(page, seconds=90):
    """等翻譯請求都回來：快取檔大小連續 8 秒沒變就算好。"""
    f = HOME / "cache" / "ui_translate.json"
    last, still, t0 = -1, 0, time.time()
    while time.time() - t0 < seconds and still < 8:
        size = f.stat().st_size if f.exists() else 0
        still = still + 1 if size == last else 0
        last = size
        page.wait_for_timeout(1000)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    for lang, btn in LANG_BTN.items():
        for name in (SAMPLES if lang == "en" else SAMPLE_JA):
            pg.goto(URL, wait_until="networkidle")
            pg.get_by_role("button", name=btn, exact=True).click()
            pg.wait_for_timeout(800)
            pg.get_by_text(name).first.click()
            pg.wait_for_timeout(2000)
            pg.mouse.wheel(0, 20000)
            settle(pg)
            # 段落按鈕：專案頁上唯一帶「$金額」的按鈕
            seg = pg.get_by_role("button").filter(has_text="$").first
            seg.click()
            pg.wait_for_timeout(3000)
            for _ in range(30):                      # 一路捲到底，讓每一區都掛上去
                pg.mouse.wheel(0, 1500)
                pg.wait_for_timeout(300)
            settle(pg)
            print(lang, name, "ok", flush=True)
    b.close()

cache = json.loads((HOME / "cache" / "ui_translate.json").read_text(encoding="utf-8"))
old = json.loads(OUT.read_text(encoding="utf-8"))
OUT.write_text(json.dumps({**old, **cache}, ensure_ascii=False), encoding="utf-8")
print(f"sample cache {len(old)} → {len({**old, **cache})}")
