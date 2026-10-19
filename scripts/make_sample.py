"""從做好的專案產生「內建範例」（打包進安裝檔，第一次開 app 時放進專案清單）。

評審多半沒有 24GB 顯示卡、也沒有 Nebius 金鑰：打開一個空的 app 看不出它在做什麼。
內建範例讓人不用跑任何東西就能看完整的過程與結果：導演回合、關卡、製作人意見、試拍查證與診斷、
學語言的台詞表與學習筆記、成片（有讀音的雙語字幕）。

三個範例各走一條路：
  1 全自動：人物卡、空景都由 app 的模型生（真人、橫式）
  2 全部自備：使用者上傳自己的人物卡、空景、首幀，原圖照用（動畫、直式）
  3 只給人物卡：沒有空景，場景用文字描述（動畫、直式）—— 看得出「沒空景＝每鏡場景不一樣」
每一例的標題與說明（三語，分開寫、不用機器翻）寫進專案的 sample_info，app 的專案頁與清單照這個顯示。

只帶專案資料與它引用到的媒體檔 —— 設定檔（裡面有金鑰）絕對不帶；帶之前檢查沒有個人路徑、沒有金鑰。

用法：
  python scripts/make_sample.py add <來源資料夾> <專案 id> <範例代號 1|2|3> [--cache <有翻譯快取的資料夾>]
      加入或換掉一個範例（其他範例不動）
  python scripts/make_sample.py label
      只把 SAMPLES 裡的標題與說明重新寫進 sample/ 裡已有的範例（改了說明文字就跑這個）
  python scripts/make_sample.py refs <來源資料夾> <專案 id> <範例代號 1|2|3>
      只補參考圖（人物卡、臉、空景），專案存檔不動
輸出：sample/（projects/、media/、cache_ui_translate.json），PyInstaller spec 會把它打包進去
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "sample"
SECRET = re.compile(r"nvapi-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}")
PERSONAL = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+|/home/|AppData", re.I)

# 三個範例的唯一出處：專案代號、標題、說明（README 的表格也照這份寫）
SAMPLES = {
    "1": {
        "id": "p-sample-scene61",
        "title": {"zh": "範例 1 — 全自動：第10章 場景61（真人・橫式）",
                  "en": "Sample 1 — Fully automatic: Chapter 10, Scene 61 (live action, landscape)",
                  "ja": "サンプル 1 — 全自動：第10章 シーン61（実写・横長）"},
        "note": {"zh": "示範「什麼都不用準備」：只給劇本，人物卡、空景都由 app 的本機模型生，19 鏡全部收下。"
                       "日文原聲、日中／日英學習字幕。看點：導演回合與關卡、製作人意見、每鏡的查證與診斷。",
                 "en": "Shows the “bring nothing” path: just a script — the app's local models made the character cards and empty sets; "
                       "all 19 shots accepted. Japanese voices with Japanese–Chinese / Japanese–English study subtitles. "
                       "Look at the director rounds and gates, the producer's notes, and each shot's checks and diagnosis.",
                 "ja": "「何も用意しない」道の例：台本だけ。人物カードも背景もアプリのローカルモデルが生成し、19 カットすべて採用。"
                       "日本語音声、日中／日英の学習字幕。見どころ：監督のラウンドとチェック、プロデューサーの意見、各カットの検証と診断。"},
    },
    "2": {
        "id": "p-sample-bts01",
        "title": {"zh": "範例 2 — 全部自備：幕後短片第1集（動畫・直式）",
                  "en": "Sample 2 — Bring your own images: Behind the Scenes ep. 1 (anime, vertical)",
                  "ja": "サンプル 2 — 画像をすべて持ち込み：舞台裏ショート第1話（アニメ・縦長）"},
        "note": {"zh": "示範「用自己畫的圖」：人物卡、空景、幾鏡的首幀都是使用者上傳，原圖照用、模型不重畫。"
                       "23 鏡、9:16 直式，日文原聲、日中／日英學習字幕。適合已經有自己畫風、只想要動起來的人。",
                 "en": "Shows the “use my own art” path: the character cards, empty sets and several first frames were uploaded "
                       "and used as-is — the model does not redraw them. 23 shots, 9:16 vertical, Japanese voices with "
                       "Japanese–Chinese / Japanese–English study subtitles. For people who already have their own style and just want it to move.",
                 "ja": "「自分の絵を使う」道の例：人物カード・背景・いくつかの最初のコマはユーザーがアップロードし、そのまま使用（モデルは描き直さない）。"
                       "23 カット、9:16 縦長、日本語音声、日中／日英の学習字幕。自分の絵柄があって、動かしたい人向け。"},
    },
    "3": {
        "id": "p-sample-bts02",
        "title": {"zh": "範例 3 — 只給人物卡、沒有空景：幕後短片第2集（動畫・直式）",
                  "en": "Sample 3 — Character cards only, no empty sets: Behind the Scenes ep. 2 (anime, vertical)",
                  "ja": "サンプル 3 — 人物カードだけ、背景なし：舞台裏ショート第2話（アニメ・縦長）"},
        "note": {"zh": "示範「只準備人物卡」：三個角色用自己的卡（原圖照用），空景一張都不給，場景只靠文字描述。"
                       "結果：人物每鏡都一致，但場景每鏡都不一樣 —— 想要場景連貫，就上傳或生空景。23 鏡、日中／日英學習字幕。",
                 "en": "Shows the “character cards only” path: the three characters use their own cards (as-is) and no empty set is given — "
                       "the places are described in text only. Result: the characters stay consistent, but the place changes from shot to shot. "
                       "For a consistent place, upload or generate empty sets. 23 shots, Japanese–Chinese / Japanese–English study subtitles.",
                 "ja": "「人物カードだけ用意」の例：3 人は自分のカードをそのまま使用、背景は一枚も渡さず、場所は文章だけで指定。"
                       "結果：人物はどのカットでも一致するが、場所はカットごとに変わる —— 場所をそろえたいなら背景をアップロードか生成を。"
                       "23 カット、日中／日英の学習字幕。"},
    },
}


def _check(text: str) -> None:
    if SECRET.search(text):
        raise SystemExit("專案資料裡有像金鑰的字串 —— 不產生範例")
    bad = PERSONAL.findall(text)
    if bad:
        raise SystemExit(f"專案資料裡有個人路徑 {set(bad)} —— 不產生範例")


def _label(data: dict, key: str) -> dict:
    info = SAMPLES[key]
    data["title"] = info["title"]["zh"]
    data["sample_info"] = {"title": info["title"], "note": info["note"]}
    return data


def add(source: Path, project_id: str, key: str, cache: Path | None) -> None:
    sid = SAMPLES[key]["id"]
    text = (source / "projects" / f"{project_id}.json").read_text(encoding="utf-8").replace(project_id, sid)
    data = _label(json.loads(text), key)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    _check(text)
    (OUT / "projects").mkdir(parents=True, exist_ok=True)
    (OUT / "projects" / f"{sid}.json").write_text(text, encoding="utf-8")

    media_src, media_out = source / "media" / project_id, OUT / "media" / sid
    shutil.rmtree(media_out, ignore_errors=True)   # 只換這一例的媒體，別的範例不動
    media_out.mkdir(parents=True)
    names = sorted(set(re.findall(rf"local://{sid}/([^\"\s]+)", text)))
    total, missing = 0, []
    for n in names:
        f = media_src / n
        if f.exists():
            shutil.copy2(f, media_out / n)
            total += f.stat().st_size
        else:
            missing.append(n)
    total += _copy_refs(media_src, media_out)
    if cache:
        out = OUT / "cache_ui_translate.json"
        cur = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        new = json.loads((cache / "cache" / "ui_translate.json").read_text(encoding="utf-8"))
        out.write_text(json.dumps({**new, **cur}, ensure_ascii=False), encoding="utf-8")
    print(f"sample {key} ({sid}): {len(names) - len(missing)} media files, {total / 1e6:.1f} MB"
          + (f", missing {missing}" if missing else ""))


def _copy_refs(media_src: Path, media_out: Path) -> int:
    """參考圖（ref_card_／ref_face_／ref_plate_…）：app 用檔名找（media_store.find），存檔裡沒有 local:// 網址，
    只照網址複製就會漏掉 —— 三個範例的人物卡與空景全部是「Not yet」（評審看到的選角、空景是空的）。"""
    size = 0
    for f in sorted(media_src.glob("ref_*")):
        shutil.copy2(f, media_out / f.name)
        size += f.stat().st_size
    return size


def refs(source: Path, project_id: str, key: str) -> None:
    sid = SAMPLES[key]["id"]
    media_out = OUT / "media" / sid
    if not media_out.exists():
        raise SystemExit(f"sample {key} ({sid}) not in sample/ yet — run add first")
    size = _copy_refs(source / "media" / project_id, media_out)
    print(f"sample {key} ({sid}): {len(list(media_out.glob('ref_*')))} reference images, {size / 1e6:.1f} MB")


def label() -> None:
    for key, info in SAMPLES.items():
        pj = OUT / "projects" / f"{info['id']}.json"
        if not pj.exists():
            print(f"sample {key} ({info['id']}): not in sample/ yet — run add first")
            continue
        text = json.dumps(_label(json.loads(pj.read_text(encoding="utf-8")), key), ensure_ascii=False, indent=1)
        _check(text)
        pj.write_text(text, encoding="utf-8")
        print(f"sample {key} ({info['id']}): labelled")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("source", type=Path, help="來源資料夾（GREENLIGHT_HOME）")
    a.add_argument("project_id")
    a.add_argument("key", choices=sorted(SAMPLES))
    a.add_argument("--cache", type=Path, help="另一個資料夾的 cache/ui_translate.json（預先翻好的英日文）")
    sub.add_parser("label")
    r = sub.add_parser("refs")
    r.add_argument("source", type=Path, help="來源資料夾（GREENLIGHT_HOME）")
    r.add_argument("project_id")
    r.add_argument("key", choices=sorted(SAMPLES))
    args = ap.parse_args()
    if args.cmd == "add":
        add(args.source, args.project_id, args.key, args.cache)
    elif args.cmd == "refs":
        refs(args.source, args.project_id, args.key)
    else:
        label()


if __name__ == "__main__":
    main()
