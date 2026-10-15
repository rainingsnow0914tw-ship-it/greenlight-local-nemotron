"""驗證 Veo 3.1 的 referenceImages（角色參考圖）到底怎麼呼叫。約 $0.20。

為什麼這支很重要：
    疑問：引導圖有沒有真的被用上、鎖定角色功能有沒有啟動？
    —— 實際沒有。選定的角色圖從來沒有送進 Veo，一致性只靠文字標籤。

    先前只實證過一件事：referenceImages **與 image（首幀）同傳
    會 400**。它單獨用 referenceImages 行不行、形狀長怎樣，先前沒驗過
    （它最後放棄改用首幀）。我們要走的路不同：
        首幀    = 接戲用（上一鏡末幀），會把整張圖畫進鏡頭開頭
        參考圖  = 鎖身份用，構圖與背景仍由提示詞決定
    選角圖現在帶場景背景（黃昏街景），只能走參考圖這條，不能當首幀。

省錢的做法（跟 verify_extend.py 同一套）：
    **錯誤的 payload 回 4xx 不計費**，形狀探索免費。
    只有第一個回 200 的形狀會真的生成（4 秒 Lite ≈ $0.20）。

驗證重點（生完人工看影片）：
    1. 角色長相有沒有跟參考圖一致（鎖身份成功？）
    2. 背景是不是提示詞指定的雨夜巷子，**而不是**參考圖的背景
       （如果背景被抄過來，代表它的行為更像首幀，這條路要重想）

用法：
    python scripts/verify_reference_image.py <參考圖路徑.png>
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

import requests  # noqa: E402

from app.services import env_boot  # noqa: E402
from app.services.video_gen import probe  # noqa: E402

env_boot.load()

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "greenlight-2026")
LOCATION = "us-central1"
# 實測：Lite 回「The request is not supported by this model」——
#    referenceImages 是 API 認得的欄位（B/C 形狀的錯誤是欄位驗證），
#    但 **Lite 檔不支援**。用環境變數換檔位探測。
MODEL = os.getenv("VERIFY_MODEL", "veo-3.1-lite-generate-001")

OUT_PATH = _ROOT / "outputs" / "verify_reference.mp4"

# 場景刻意跟任何選角圖的背景都不同（雨夜巷子）——
#    這樣才分得出「鎖身份」與「抄整張圖」。
PROMPT = (
    "The same person as in the reference image walks slowly through a narrow "
    "rainy alley at night, neon signs reflecting in puddles, medium shot, "
    "steady camera. The character keeps the exact same face, hairstyle and "
    "outfit as the reference image."
)


def _headers() -> dict[str, str]:
    """x-goog-user-project 不可省 —— 少了會回誤導性的 404。"""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
        "x-goog-user-project": PROJECT_ID,
    }


def _endpoint(action: str) -> str:
    return (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}"
        f"/locations/{LOCATION}/publishers/google/models/{MODEL}:{action}"
    )


# ---------------------------------------------------------------------------
# 候選 payload 形狀（4xx 不計費，第一個 200 的就是答案）
# ---------------------------------------------------------------------------


def _candidates(img_b64: str, mime: str) -> list[tuple[str, dict[str, Any]]]:
    # 實測：reference_to_video 只支援 8 秒
    #    （4 秒被拒：「supported durations are [8]」）。
    params = {
        "durationSeconds": 8,
        "aspectRatio": "16:9",
        "sampleCount": 1,
        "generateAudio": True,
    }
    ref_nested = {
        "image": {"bytesBase64Encoded": img_b64, "mimeType": mime},
        "referenceType": "asset",
    }
    return [
        (
            'A. instances[].referenceImages[{image, referenceType:"asset"}]',
            {
                "instances": [{"prompt": PROMPT, "referenceImages": [ref_nested]}],
                "parameters": params,
            },
        ),
        (
            "B. 同 A 但不帶 referenceType",
            {
                "instances": [
                    {
                        "prompt": PROMPT,
                        "referenceImages": [
                            {"image": {"bytesBase64Encoded": img_b64, "mimeType": mime}}
                        ],
                    }
                ],
                "parameters": params,
            },
        ),
        (
            "C. 扁平形狀（bytesBase64Encoded 直接在 referenceImages[] 裡）",
            {
                "instances": [
                    {
                        "prompt": PROMPT,
                        "referenceImages": [
                            {
                                "bytesBase64Encoded": img_b64,
                                "mimeType": mime,
                                "referenceType": "asset",
                            }
                        ],
                    }
                ],
                "parameters": params,
            },
        ),
    ]


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/verify_reference_image.py <參考圖路徑>")
        return 2
    img_path = Path(sys.argv[1])
    if not img_path.exists():
        print(f"找不到參考圖：{img_path}")
        return 2

    data = img_path.read_bytes()
    mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
    img_b64 = base64.b64encode(data).decode()
    print(f"參考圖：{img_path.name}（{len(data) / 1024:.0f} KB, {mime}）")

    headers = _headers()
    operation = None
    for label, payload in _candidates(img_b64, mime):
        print(f"\n→ 試 {label}")
        res = requests.post(
            _endpoint("predictLongRunning"),
            headers=headers,
            json=payload,
            timeout=120,
        )
        if res.status_code == 200:
            operation = res.json().get("name")
            print(f"  ✓ 接受（開始計費，4 秒 Lite ≈ $0.20）")
            break
        detail = ""
        try:
            detail = res.json().get("error", {}).get("message", "")[:200]
        except Exception:  # noqa: BLE001
            detail = res.text[:200]
        print(f"  ✗ HTTP {res.status_code}（$0）：{detail}")

    if not operation:
        print("\n全部形狀都被拒絕，這一趟 $0。referenceImages 可能不支援此模型。")
        return 1

    # ---- 輪詢 ----
    poll_url = _endpoint("fetchPredictOperation")
    started = time.monotonic()
    while True:
        time.sleep(15)
        res = requests.post(
            poll_url,
            headers=_headers(),
            json={"operationName": operation},
            timeout=120,
        )
        body = res.json()
        if body.get("done"):
            break
        elapsed = int(time.monotonic() - started)
        print(f"  …生成中（{elapsed}s）")
        if elapsed > 600:
            print("逾時 10 分鐘，放棄。operation：", operation)
            return 1

    if "error" in body:
        print("生成失敗：", json.dumps(body["error"], ensure_ascii=False)[:300])
        return 1

    videos = body.get("response", {}).get("videos", [])
    if not videos:
        print("回應沒有影片：", json.dumps(body, ensure_ascii=False)[:300])
        return 1

    video_bytes = base64.b64decode(videos[0]["bytesBase64Encoded"])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(video_bytes)
    info = probe(video_bytes)
    print(
        f"\n✓ 完成：{OUT_PATH}\n"
        f"  {info.get('width')}×{info.get('height')} · {info.get('seconds')}s · "
        f"{len(video_bytes) / 1024 / 1024:.1f} MB"
    )
    print(
        "\n人工驗證兩件事：\n"
        "  1. 角色長相跟參考圖一致嗎？（鎖身份）\n"
        "  2. 背景是雨夜巷子、不是參考圖的背景嗎？（沒有抄整張圖）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
