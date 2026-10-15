"""從一個做好的專案產生「內建範例」（打包進安裝檔，第一次開 app 時放進專案清單）。

評審多半沒有 24GB 顯示卡、也沒有 Nebius 金鑰：打開一個空的 app 看不出它在做什麼。
內建範例讓人不用跑任何東西就能看完整的過程與結果：導演回合、關卡、製作人意見、試拍查證與診斷、
學語言的台詞表與學習筆記、成片（有讀音的雙語字幕）。

只帶專案資料與它引用到的媒體檔 —— 設定檔（裡面有金鑰）絕對不帶；帶之前檢查沒有個人路徑、沒有金鑰。

用法：python scripts/make_sample.py <來源資料夾> <專案 id> [--cache <有翻譯快取的資料夾>]
輸出：sample/（projects/、media/、cache_ui_translate.json），PyInstaller spec 會把它打包進去
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

SAMPLE_ID = "p-sample-scene61"
SAMPLE_TITLE_PREFIX = "Sample — "
OUT = Path(__file__).resolve().parents[1] / "sample"
SECRET = re.compile(r"nvapi-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}")
PERSONAL = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+|/home/|AppData", re.I)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path, help="來源資料夾（GREENLIGHT_HOME）")
    ap.add_argument("project_id")
    ap.add_argument("--cache", type=Path, help="另一個資料夾的 cache/ui_translate.json（預先翻好的英日文）")
    a = ap.parse_args()

    src = (a.source / "projects" / f"{a.project_id}.json").read_text(encoding="utf-8")
    text = src.replace(a.project_id, SAMPLE_ID)
    data = json.loads(text)
    if not data.get("title", "").startswith(SAMPLE_TITLE_PREFIX):
        data["title"] = SAMPLE_TITLE_PREFIX + data.get("title", "")
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if SECRET.search(text):
        raise SystemExit("專案資料裡有像金鑰的字串 —— 不產生範例")
    bad = PERSONAL.findall(text)
    if bad:
        raise SystemExit(f"專案資料裡有個人路徑 {set(bad)} —— 不產生範例")

    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "projects").mkdir(parents=True)
    (OUT / "projects" / f"{SAMPLE_ID}.json").write_text(text, encoding="utf-8")

    media_src = a.source / "media" / a.project_id
    media_out = OUT / "media" / SAMPLE_ID
    media_out.mkdir(parents=True)
    names = sorted(set(re.findall(rf"local://{SAMPLE_ID}/([^\"\s]+)", text)))
    total, missing = 0, []
    for n in names:
        f = media_src / n
        if f.exists():
            shutil.copy2(f, media_out / n)
            total += f.stat().st_size
        else:
            missing.append(n)

    if a.cache:
        cache = json.loads((a.cache / "cache" / "ui_translate.json").read_text(encoding="utf-8"))
        (OUT / "cache_ui_translate.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"sample: {len(names) - len(missing)} media files, {total / 1e6:.1f} MB"
          + (f", missing {missing}" if missing else "") + (f", {len(cache)} cached translations" if a.cache else ""))


if __name__ == "__main__":
    main()
