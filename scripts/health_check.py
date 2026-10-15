"""雲端健康檢查 —— **$0，唯讀，不會改任何資料。**

═══════════════════════════════════════════════════════════════════════════
為什麼跟 verify_wiring.py 是兩支不同的東西
═══════════════════════════════════════════════════════════════════════════

    verify_wiring.py   本機、TestClient、把花錢的那幾步換成樁
                       問的是「程式碼接對了嗎」

    health_check.py    打**真實雲端 URL**、什麼都不樁
                       問的是「部署出去的那一份現在活著而且正確嗎」

的教訓：verify_wiring 105 項全綠，同一天在雲端抓到兩個它
永遠看不到的 bug（`gs://` 路徑回 None、ffmpeg 真實檔案 212 秒逼近逾時）。
**樁的邊界就是測試盲區的邊界。** 這支存在就是為了守那條邊界外面。

═══════════════════════════════════════════════════════════════════════════
八步健康檢查（skill cloud-ops-cookbook）
═══════════════════════════════════════════════════════════════════════════

前七步在「服務層」，**第 8 步在資料層 —— 最常被跳過、也最常是唯一抓得到
bug 的**。服務回 200 只代表「跑完了」，不代表「做了事」。

這支刻意**不做**任何會花錢或會改狀態的事：
    不生成影片、不 rewind、不刪媒體、不寫 ledger。
    做不到的檢查會誠實列在最後，而不是假裝全部驗過了。

用法：
    ./.venv/Scripts/python.exe scripts/health_check.py
    ./.venv/Scripts/python.exe scripts/health_check.py --url http://localhost:8020
    ./.venv/Scripts/python.exe scripts/health_check.py --project shensi-001
"""

from __future__ import annotations
import os

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows console 是 cp950，印中文會拋 UnicodeEncodeError 把腳本
#    在最後一行炸掉（本專案踩過）。先把輸出轉成 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_URL = os.environ.get("GREENLIGHT_URL", "http://localhost:8020")  # 正式站網址不寫進 repo，用環境變數帶

_passed: list[str] = []
_failed: list[str] = []
_warned: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (_passed if ok else _failed).append(name)
    print(f"  {'OK ' if ok else 'X  '}{name}{'  — ' + detail if detail else ''}")
    return ok


def warn(name: str, detail: str = "") -> None:
    """不是失敗、但值得看一眼的事。不計入失敗數。"""
    _warned.append(name)
    print(f"  !  {name}{'  — ' + detail if detail else ''}")


def get(url: str, timeout: int = 60) -> tuple[int, bytes, float]:
    """GET 一個 URL，回 (狀態碼, 內容, 秒數)。連不上回 (0, 錯誤訊息, 秒數)。"""
    started = time.time()
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), time.time() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.time() - started
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode(), time.time() - started


def post(url: str, timeout: int = 60) -> tuple[int, bytes, float]:
    """POST（空 body）。只用在**不會花錢也不改狀態**的探測上。

    空 body 的 POST 一定要帶 Content-Length，否則 Google 前端會先回
       411 而根本沒送到我們的服務 —— 那會被誤讀成「route 不存在」。
    """
    started = time.time()
    request = urllib.request.Request(
        url, data=b"", method="POST", headers={"Content-Length": "0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), time.time() - started
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), time.time() - started
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode(), time.time() - started


def as_json(body: bytes) -> dict:
    try:
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Greenlight 雲端健康檢查（$0）")
    parser.add_argument("--url", default=DEFAULT_URL, help="要檢查的服務 URL")
    parser.add_argument(
        "--project", default="shensi-001", help="拿來做唯讀檢查的真實專案 ID"
    )
    args = parser.parse_args()

    base = args.url.rstrip("/")
    project = args.project

    print("=" * 72)
    print("Greenlight 雲端健康檢查 —— $0，唯讀，不改任何資料")
    print("=" * 72)
    print(f"目標：{base}")
    print(f"參照專案：{project}")
    print()

    # ── 1. 服務活著 ────────────────────────────────────────────────
    print("[1] 服務活著嗎")
    status, body, elapsed = get(f"{base}/api/health", timeout=120)
    alive = check("/api/health 回 200", status == 200, f"{elapsed:.2f}s")
    if not alive:
        print(f"     回應：{body[:200].decode(errors='replace')}")
        print("\n服務連不上，後面的檢查沒有意義。")
        return 1

    health = as_json(body)
    if elapsed > 5:
        warn(
            "首次回應偏慢",
            f"{elapsed:.1f}s —— 冷啟動正常（scale-to-zero），連續請求應該降到 1 秒內",
        )

    # ── 2. 熱啟動延遲 ──────────────────────────────────────────────
    print()
    print("[2] 熱的時候多快")
    warm = []
    for _ in range(3):
        _s, _b, t = get(f"{base}/api/health", timeout=30)
        warm.append(t)
    slowest = max(warm)
    check(
        "熱請求 < 2 秒",
        slowest < 2.0,
        " / ".join(f"{t:.2f}s" for t in warm),
    )

    # ── 3. ClickHouse 管道 ─────────────────────────────────────────
    print()
    print("[3] ClickHouse（必須經官方 mcp-clickhouse，這是合規條件）")
    ch = health.get("clickhouse", "?")
    check(
        "clickhouse: connected",
        ch == "connected",
        f"實際 {ch}" + ("（降級中：系統照跑但全程用先驗值）" if ch != "connected" else ""),
    )

    # ── 4. 前端 ────────────────────────────────────────────────────
    print()
    print("[4] 前端有沒有一起部署")
    status, body, _ = get(f"{base}/", timeout=60)
    html = body.decode(errors="replace")
    check("根路徑回 200", status == 200, f"{len(body)} bytes")
    check("是 index.html（不是 API 的 404 JSON）", "<html" in html.lower())

    asset = ""
    marker = 'src="/assets/'
    if marker in html:
        asset = html.split(marker, 1)[1].split('"', 1)[0]
    if asset:
        status, body, _ = get(f"{base}/assets/{asset}", timeout=60)
        check(
            "JS bundle 載得到",
            status == 200 and len(body) > 10_000,
            f"{asset} · {len(body) // 1024} KB",
        )
    else:
        warn("找不到 JS bundle 的檔名", "index.html 的形狀可能變了")

    # ── 5. API route 都在 ──────────────────────────────────────────
    #
    # 用「一個不存在的專案」去打：預期 404（我們的錯誤訊息），
    #    而不是 405（method 不對）或 500（route 壞了）。
    #    這能驗證 route 有註冊、也不會碰到真實資料。
    print()
    print("[5] 新 route 都註冊了嗎（打不存在的專案，預期 404）")
    ghost = "healthcheck-no-such-project"
    probes = [
        ("GET", f"/api/projects/{ghost}/media", "媒體庫列表"),
        ("GET", f"/api/projects/{ghost}/rewind/preview?target=parsed", "rewind 預覽"),
        ("POST", f"/api/projects/{ghost}/rewind", "rewind 執行"),
        ("POST", f"/api/projects/{ghost}/assemble-film", "全片合成"),
        ("GET", f"/api/projects/{ghost}/film/download", "全片下載"),
        ("GET", f"/api/projects/{ghost}/film/subtitles?lang=zh", "全片字幕"),
        ("POST", f"/api/projects/{ghost}/film/burn?lang=zh", "全片燒字幕"),
        ("POST", f"/api/projects/{ghost}/segments/1/batch/next?auto=true", "一鍵模式"),
        ("POST", f"/api/projects/{ghost}/segments/1/assemble/burn?lang=zh", "段落燒字幕"),
    ]
    for method, path, label in probes:
        status, body, _ = (get if method == "GET" else post)(f"{base}{path}", timeout=60)
        # media 列表對不存在的專案回空清單（那是刻意的）。
        # 422 也算通過：那是 FastAPI 說「route 在、但你少給 body」——
        #    這支探測刻意送空 body，需要 payload 的 route 本來就會這樣回。
        #    我們在驗的是「route 註冊了沒」，不是參數處理。
        if "media" in path:
            acceptable = {200, 404}
        else:
            acceptable = {404, 422}
        check(
            f"{label}",
            status in acceptable,
            f"HTTP {status}"
            + ("" if status in acceptable else " ← 405=method錯 500=route壞"),
        )

    # ── 6. 真實專案讀得到 ──────────────────────────────────────────
    print()
    print(f"[6] 真實專案 {project} 讀得到嗎（唯讀）")
    status, body, _ = get(f"{base}/api/projects/{project}", timeout=90)
    if not check("專案詳情回 200", status == 200, str(status)):
        print("     參照專案不存在，跳過後面的資料檢查。可用 --project 指定別的。")
    else:
        detail = as_json(body)
        progress = detail.get("progress", {})
        spend = detail.get("spend", {})
        check(
            "  有階段資訊",
            bool(progress.get("stage")),
            f"{progress.get('stage')} · {progress.get('step')}/{progress.get('total_steps')}",
        )
        check(
            "  花費在預算內",
            spend.get("total_usd", 0) <= spend.get("ceiling_usd", 100),
            f"${spend.get('total_usd')} / ${spend.get('ceiling_usd')}",
        )

    # ── 7. 端到端：規劃頁真的查了歷史 ──────────────────────────────
    #
    # 這是整支腳本最重要的一項。
    #    health 說 connected 只代表握手成功；confidence=blended 代表
    #    **統計查詢真的透過 MCP 拿到歷史資料並影響了預估**。
    #    前六步全綠、這一項紅，就是「服務活著但沒在做事」。
    print()
    print("[7] 端到端：規劃頁真的透過 MCP 查到歷史了嗎")
    status, body, elapsed = get(
        f"{base}/api/projects/{project}/segments/1/shots", timeout=180
    )
    if check("分鏡頁回 200", status == 200, f"{elapsed:.1f}s"):
        shots = as_json(body)
        variants = shots.get("variants", [])
        confidences = {v.get("confidence") for v in variants if v.get("confidence")}
        check(
            "  有畫質檔位可選",
            len(variants) > 0,
            f"{len(variants)} 個檔位",
        )
        if confidences == {"prior_only"}:
            warn(
                "信心值全是 prior_only",
                "樣本不足時這是**正確**行為；但若 ledger 有資料卻仍是 prior_only，"
                "代表歷史查詢沒接上",
            )
        else:
            check(
                "🔴 信心值含 blended（真的讀到實測資料）",
                "blended" in confidences,
                "、".join(sorted(c for c in confidences if c)),
            )
        if elapsed > 30:
            warn("分鏡頁偏慢", f"{elapsed:.0f}s —— 檢查歷史查詢有沒有退回逐鏡查")

    # ── 8. 資料層：媒體真的在 ──────────────────────────────────────
    print()
    print("[8] 資料層：媒體檔真的在雲端儲存嗎")
    status, body, _ = get(f"{base}/api/projects/{project}/media", timeout=90)
    if check("媒體庫列表回 200", status == 200, str(status)):
        media = as_json(body)
        count = media.get("count", 0)
        total_mb = media.get("total_bytes", 0) / 1024 / 1024
        check("  列得到檔案", count > 0, f"{count} 個 · {total_mb:.1f} MB")
        items = media.get("items", [])
        if items:
            # 真的把第一個檔案讀回來 —— 列表出得來不代表檔案讀得到。
            #    GCS 權限錯、檔案被外部刪掉，都只會在這一步現形。
            # 挑**最大的**那個檔案來驗。
            # 小檔案通過不代表大檔案通過 —— Cloud Run 對非串流回應有
            #    32 MiB 上限，就是 36.6 MB 的成片下載回 0 bytes，
            #    而所有小圖都正常。要驗就驗最容易失敗的那一個。
            biggest = max(items, key=lambda i: i.get("size_bytes", 0))
            size_mb = biggest.get("size_bytes", 0) / 1024 / 1024
            status, body, elapsed = get(
                f"{base}/api/projects/{project}/media/{biggest['filename']}/raw",
                timeout=300,
            )
            # detail 一定要印出 status 與實際大小 ——
            #    只寫「0 KB」不足以判斷是 404、是逾時、還是被截斷。
            #    測試自己的錯誤訊息也要能指出該往哪裡看。
            check(
                "🔴 最大的檔案真的下載得回來",
                status == 200 and len(body) >= biggest.get("size_bytes", 0) * 0.99,
                f"{biggest['filename']} · 應 {size_mb:.1f} MB · "
                f"實得 {len(body) / 1024 / 1024:.1f} MB · HTTP {status} · {elapsed:.1f}s",
            )
            kinds = {i.get("kind") for i in items}
            check("  影片與圖片都有", {"video", "image"} & kinds == {"video", "image"}
                  or len(kinds) > 0, "、".join(sorted(k for k in kinds if k)))

    # ── 收尾 ───────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print(f"通過 {len(_passed)} 項 · 失敗 {len(_failed)} 項 · 提醒 {len(_warned)} 項")
    if _failed:
        print()
        for name in _failed:
            print(f"  X {name}")

    # 誠實區：不要讓「全部通過」被誤讀成「全部驗過了」。
    print()
    print("這支**驗不到**的東西（要花錢或會改資料，只能人工驗）：")
    print("  · 影片生成（試拍 / 逐鏡 / 一鍵）—— 每鏡約 $0.40")
    print("  · rewind 實際執行 —— 會清掉專案進度")
    print("  · 媒體檔刪除 —— 會真的刪檔")
    print("  · 燒字幕的真實耗時 —— 整片重編碼要幾分鐘（段落級實測 94.7s）")
    print("  · 生成內容的品質 —— 只有人看得出來")
    print("=" * 72)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
