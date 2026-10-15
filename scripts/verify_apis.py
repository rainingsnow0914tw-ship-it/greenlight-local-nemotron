"""驗證所有「待實測」的假設 —— 這是第一次真的花錢。

═══════════════════════════════════════════════════════════════════════════
為什麼要先跑這支，而不是直接開始寫 agent
═══════════════════════════════════════════════════════════════════════════

    整個 config 裡有一堆註記著 待實測 的假設：
      - Veo 3.1 各檔位的 model ID 到底叫什麼
      - Extend 怎麼呼叫、按新增秒數還是總秒數計費
      - 9:16 是不是真的會輸出直式（有開發者回報會被強制轉 16:9）
      - Gemini 3.1 Pro 是不是真的只在 global endpoint

    這些猜錯的話，agent 寫完會整批重來。花 $1 先問清楚，
    比寫完三天的 code 才發現 model ID 不存在划算得多。

═══════════════════════════════════════════════════════════════════════════
成本
═══════════════════════════════════════════════════════════════════════════

    Gemini 探測      約 $0.01
    Imagen 一張      約 $0.02
    Veo Lite 4 秒    約 $0.20   × 最多 3 次 = $0.60
    ─────────────────────────────────
    全部跑完         約 $0.65（上限，實際通常更少）

    預算是 $100 硬上限，這支腳本佔 0.65%。值得。

用法：
    python scripts/verify_apis.py --check gemini,imagen        # 便宜的先跑
    python scripts/verify_apis.py --check veo --yes            # 會花錢，要加 --yes
    python scripts/verify_apis.py --check all --yes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GLOBAL_LOCATION = "global"

_results: list[dict] = []


def record(name: str, assumption: str, actual: str, ok: bool, note: str = "") -> None:
    """記一筆驗證結果。

    刻意把「假設」跟「實測」並排 —— 這樣報告可以直接拿來改 config，
    不需要回頭翻是哪個欄位寫錯了。
    """
    _results.append(
        {"name": name, "assumption": assumption, "actual": actual, "ok": ok, "note": note}
    )
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}")
    print(f"      假設：{assumption}")
    print(f"      實測：{actual}")
    if note:
        print(f"      註：{note}")
    print()


# ---------------------------------------------------------------------------
# 共用
# ---------------------------------------------------------------------------

def _token() -> str:
    """取 ADC 的存取權杖。

    走 ADC 不用 API 金鑰 —— 這是專案規矩，
       也是 Cloud Run 上唯一乾淨的做法（服務帳號自動帶入）。
    """
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _headers() -> dict[str, str]:
    """REST 呼叫的標準 header。

    `x-goog-user-project` 是必要的，不是可選的。

    用 ADC（使用者憑證）走 REST 時，Vertex AI 無法自己判斷該計費給哪個專案，
    會回一個**極度誤導的錯誤**：
        - 有些 endpoint 回 403 PERMISSION_DENIED
        - 有些 endpoint 回 404「model was not found」

    那個 404 會讓人以為 model ID 打錯，跑去查文件、改名稱、猜版本號 ——
    實際上 model ID 完全正確，只是少了這個 header。
    （實際踩到，浪費了幾輪來回。）

    注意：`gcloud auth application-default set-quota-project` 只會寫進 ADC 檔，
    Python 的 requests 直接打 REST 時**不會自動帶上**，一定要手動加。
    用官方 SDK 的話 SDK 會處理，但我們有些呼叫（Veo 的 predictLongRunning）
    走 REST，所以這個 header 要自己管。
    """
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "x-goog-user-project": PROJECT_ID,
    }


def _endpoint(model: str, action: str, location: str = LOCATION) -> str:
    host = f"{location}-aiplatform.googleapis.com" if location != "global" else "aiplatform.googleapis.com"
    return (
        f"https://{host}/v1/projects/{PROJECT_ID}/locations/{location}"
        f"/publishers/google/models/{model}:{action}"
    )


# ---------------------------------------------------------------------------
# 檢查項
# ---------------------------------------------------------------------------

def check_gemini() -> None:
    """驗證 Gemini model ID 與 endpoint。

    重點：Gemini 3.1 Pro 只在 global endpoint 提供（先前實證），
    在 us-central1 打會 404。這件事一定要確認 —— 如果搞錯，
    劇本解析整條路都不通。
    """
    import requests

    print("【Gemini】")
    headers = _headers()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with exactly: OK"}]}],
        "generationConfig": {"maxOutputTokens": 10, "temperature": 0},
    }

    # 從 config 讀該測哪些 model，不要在這裡寫死
    from app.services import config_loader

    tiers = config_loader.pricing().get("gemini", {}).get("tiers", {})

    for tier_key, cfg in tiers.items():
        model_id = cfg.get("model_id")
        needs_global = bool(cfg.get("requires_global_endpoint"))
        location = GLOBAL_LOCATION if needs_global else LOCATION

        try:
            resp = requests.post(
                _endpoint(model_id, "generateContent", location),
                headers=headers,
                json=payload,
                timeout=60,
            )
            if resp.status_code == 200:
                record(
                    f"gemini.{tier_key}",
                    f"{model_id} @ {location}",
                    "可用",
                    True,
                )
            else:
                record(
                    f"gemini.{tier_key}",
                    f"{model_id} @ {location}",
                    f"HTTP {resp.status_code}: {resp.text[:160]}",
                    False,
                    "改 pricing.yaml 的 model_id 或 requires_global_endpoint",
                )
        except Exception as exc:  # noqa: BLE001
            record(f"gemini.{tier_key}", f"{model_id} @ {location}", f"例外：{exc}", False)


def check_image() -> None:
    """驗證圖片生成 model ID 與 API 形狀。一張約 $0.02。

    的重大變更：Imagen 已下架，圖片生成併進 Gemini 家族。
       API 形狀從 `:predict` + instances/parameters
       改成 `:generateContent` + responseModalities，回傳 inlineData base64。
       換 model id 是不夠的，整個呼叫方式都不同。
    """
    import requests

    print("【圖片生成】")
    headers = _headers()

    from app.services import config_loader

    image_cfg = config_loader.pricing().get("imagen", {})
    tiers = image_cfg.get("tiers", {})
    location = GLOBAL_LOCATION if image_cfg.get("requires_global_endpoint") else LOCATION

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "A grey ceramic cup on a white table, studio lighting"}],
            }
        ],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }

    # 只測 fast（候選圖用的檔位）。其他檔位等真的要用再測，省錢。
    cfg = tiers.get("fast", {})
    model_id = cfg.get("model_id")

    try:
        resp = requests.post(
            _endpoint(model_id, "generateContent", location),
            headers=headers,
            json=payload,
            timeout=180,
        )
        if resp.status_code == 200:
            parts = (
                resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            )
            images = [p for p in parts if "inlineData" in p]
            if images:
                data = images[0]["inlineData"]
                size_kb = len(data.get("data", "")) * 3 // 4 // 1024
                record(
                    "image.fast",
                    f"{model_id} @ {location}（generateContent）",
                    f"可用，回傳 {data.get('mimeType')} 約 {size_kb} KB",
                    True,
                )
            else:
                record(
                    "image.fast",
                    f"{model_id} @ {location}",
                    "HTTP 200 但沒有回傳圖片",
                    False,
                    "檢查 generationConfig.responseModalities 是否為 ['IMAGE']",
                )
        else:
            record(
                "image.fast",
                f"{model_id} @ {location}",
                f"HTTP {resp.status_code}: {resp.text[:160]}",
                False,
                "Imagen 已下架。可用清單見 docs/api_verification.md",
            )
    except Exception as exc:  # noqa: BLE001
        record("image.fast", model_id, f"例外：{exc}", False)


def _veo_generate(model_id: str, prompt: str, duration: int, ratio: str) -> dict:
    """送一個 Veo 生成請求並等它完成。回傳 {ok, detail, operation}。

    Veo 是長時間操作：predictLongRunning 送出、fetchPredictOperation 輪詢。
    這個 endpoint 名稱不是標準的 operations API（先前踩過這個坑）。
    """
    import requests

    headers = _headers()
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "durationSeconds": duration,
            "aspectRatio": ratio,
            "sampleCount": 1,
            "generateAudio": True,
        },
    }

    resp = requests.post(
        _endpoint(model_id, "predictLongRunning"),
        headers=headers,
        json=payload,
        timeout=90,
    )
    if resp.status_code != 200:
        return {"ok": False, "detail": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    op_name = resp.json().get("name", "")
    if not op_name:
        return {"ok": False, "detail": "沒有回傳 operation name"}

    # 輪詢。Veo 一段 4-8 秒的片通常要 40-180 秒。
    deadline = time.time() + 420
    while time.time() < deadline:
        time.sleep(12)
        poll = requests.post(
            _endpoint(model_id, "fetchPredictOperation"),
            headers=headers,
            json={"operationName": op_name},
            timeout=60,
        )
        if poll.status_code != 200:
            return {"ok": False, "detail": f"輪詢 HTTP {poll.status_code}: {poll.text[:200]}"}
        body = poll.json()
        if body.get("done"):
            if "error" in body:
                return {"ok": False, "detail": f"生成失敗：{body['error']}"}
            return {"ok": True, "detail": "完成", "response": body.get("response", {})}

    return {"ok": False, "detail": "逾時（超過 7 分鐘）"}


def check_veo() -> None:
    """驗證 Veo 的 model ID、長度限制、比例。

    這是最花錢的一段。刻意只用 Lite 檔 + 4 秒（最短），
       每次約 $0.20。三次驗證合計約 $0.60。
    """
    print("【Veo】")

    from app.services import config_loader

    tiers = config_loader.pricing().get("veo", {}).get("tiers", {})
    lite = tiers.get("lite_720p", {})
    lite_model = lite.get("model_id")

    # --- 1. Lite 檔的 model ID 對不對，順便驗 4 秒是合法長度 ---
    result = _veo_generate(
        lite_model, "a calm empty beach at sunrise, static shot", 4, "16:9"
    )
    record(
        "veo.lite_720p.model_id",
        f"{lite_model}，durationSeconds=4 合法",
        result["detail"],
        result["ok"],
        "" if result["ok"] else "model_id 錯了。試試 gcloud ai models list 找正確名稱",
    )

    if not result["ok"]:
        print("  Lite 檔不通，跳過後續 Veo 測試以免浪費錢。\n")
        return

    # --- 2. 9:16 是不是真的輸出直式 ---
    #
    # 有開發者回報 API 會強制輸出 16:9 即使要求 9:16。
    # 這件事一定要自己確認 —— 如果是真的，整個直式功能都要重新設計。
    result = _veo_generate(
        lite_model, "a single tall candle burning, static shot", 4, "9:16"
    )
    record(
        "veo.aspect_ratio.9:16",
        "aspectRatio=9:16 會輸出直式影片",
        result["detail"],
        result["ok"],
        "🔴 請下載輸出檔用播放器確認實際尺寸 —— API 回 200 不代表比例正確",
    )

    # --- 3. 5 秒應該被拒絕（驗證 4/6/8 的限制是真的）---
    #
    # 這一項預期會失敗。失敗才是對的 —— 代表限制確實存在。
    # 如果它成功了，代表 config 裡的 allowed_seconds 寫太嚴，可以放寬。
    result = _veo_generate(lite_model, "a still glass of water", 5, "16:9")
    record(
        "veo.duration.5s_rejected",
        "durationSeconds=5 應該被拒絕（只接受 4/6/8）",
        "被拒絕（符合預期）" if not result["ok"] else f"竟然成功了：{result['detail']}",
        not result["ok"],
        "如果成功了，pricing.yaml 的 allowed_seconds 可以放寬" if result["ok"] else "",
    )


def write_report() -> Path:
    """把結果寫成 markdown，可以直接拿去更新 config。"""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# API 驗證報告",
        "",
        f"- 執行時間：{stamp}",
        f"- 專案：`{PROJECT_ID}`",
        f"- 區域：`{LOCATION}`（Pro 走 global）",
        "",
        "| 檢查項 | 假設 | 實測 | 結果 |",
        "|---|---|---|---|",
    ]
    for r in _results:
        mark = "✓" if r["ok"] else "✗"
        lines.append(
            f"| `{r['name']}` | {r['assumption']} | {r['actual'][:80]} | {mark} |"
        )

    failed = [r for r in _results if not r["ok"]]
    if failed:
        lines += ["", "## 需要修正", ""]
        for r in failed:
            lines.append(f"### `{r['name']}`")
            lines.append(f"- 假設：{r['assumption']}")
            lines.append(f"- 實測：{r['actual']}")
            if r["note"]:
                lines.append(f"- 怎麼修：{r['note']}")
            lines.append("")

    path = _ROOT / "docs" / "api_verification.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", default="gemini", help="gemini,image,veo 或 all（逗號分隔）"
    )
    parser.add_argument("--yes", action="store_true", help="確認要花錢跑 veo")
    args = parser.parse_args()

    if not PROJECT_ID:
        print("請先設定 GCP_PROJECT_ID 環境變數：")
        print('  $env:GCP_PROJECT_ID = "your-project-id"')
        return 1

    checks = (
        ["gemini", "image", "veo"]
        if args.check == "all"
        else [c.strip() for c in args.check.split(",")]
    )

    if "veo" in checks and not args.yes:
        print("Veo 測試會花約 $0.60（3 次 Lite 4 秒生成）。確定的話加 --yes。")
        return 1

    print(f"專案：{PROJECT_ID} · 區域：{LOCATION}\n")

    if "gemini" in checks:
        check_gemini()
    if "image" in checks or "imagen" in checks:
        check_image()
    if "veo" in checks:
        check_veo()

    path = write_report()
    passed = sum(1 for r in _results if r["ok"])
    print(f"通過 {passed}/{len(_results)} 項。報告：{path}")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
