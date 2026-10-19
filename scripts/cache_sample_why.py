"""範例的「為什麼這樣說」先翻好英日文，存進 sample/cache_ui_translate.json。

範例是唯讀、評審也不一定有金鑰：台詞表的說明走 AiText 翻譯（跟介面語言），
沒先翻好的話英文介面會整欄中文。這支用 app 自己的 ui_translate（同一個快取鍵、同一份名字表）翻一次。

用法（要有 Nebius 金鑰的設定資料夾）：
  GREENLIGHT_HOME=<有 settings.json 的資料夾> PYTHONPATH=backend python scripts/cache_sample_why.py
只用到 GREENLIGHT_HOME 的 settings.json；翻譯在暫存資料夾做，不碰那個資料夾的快取。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample"
CACHE = SAMPLE / "cache_ui_translate.json"

src_home = Path(os.environ["GREENLIGHT_HOME"])
tmp = Path(tempfile.mkdtemp(prefix="gl_why_"))
shutil.copy(src_home / "settings.json", tmp / "settings.json")
(tmp / "cache").mkdir()
shutil.copy(CACHE, tmp / "cache" / "ui_translate.json")
os.environ["GREENLIGHT_HOME"] = str(tmp)

from app.services import env_boot  # noqa: E402

env_boot.load()
from app.services import ui_translate  # noqa: E402

before = len(json.loads(CACHE.read_text(encoding="utf-8")))
for f in sorted((SAMPLE / "projects").glob("p-sample-*.json")):
    st = json.loads(f.read_text(encoding="utf-8"))
    whys = sorted({ln.get("why") for seg in st.get("segments", []) for ln in (seg.get("lines") or []) if ln.get("why")})
    for lang in ("en", "ja", "zh"):
        out = []
        for i in range(0, len(whys), 60):
            out += ui_translate.translate(whys[i:i + 60], lang, st.get("name_glossary") or "")
        left = sum(1 for a, b in zip(whys, out) if a == b and ui_translate.detect(a) != lang)
        print(f"{f.stem} {lang}: {len(whys)} 句，沒翻成 {left}")
merged = json.loads((tmp / "cache" / "ui_translate.json").read_text(encoding="utf-8"))
CACHE.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
print(f"快取 {before} → {len(merged)} 條")
shutil.rmtree(tmp, ignore_errors=True)
