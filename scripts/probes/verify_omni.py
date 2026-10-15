"""Gemini Omni Flash 首次實測：同一鏡、同提示詞、同參考圖，跟 Veo 對照。

為什麼：
    「錢差不多的話，應該我們用新的比較好控制吧。
      veo 生出來的話，很多違反物理現象，這個頻率比較高耶。」

    價格確認同為 $0.10/秒（720p），所以決策變數只剩**服從度與品質**。
    行銷文案跟體感都不算證據 —— 同一鏡並排比才算。

測試鏡頭：p-9685e93c03 的 s002_01
    Luna＋螢幕入鏡，Veo（Fast、帶參考圖、8 秒鏈頭）生出來螢幕反向、
    畫面多出人 —— 正是最常見的兩類問題。給 Omni 一模一樣的輸入。

端點（官方模型卡）：
    POST https://aiplatform.googleapis.com/v1beta1/projects/{p}/locations/global/interactions
    body: {"model": "gemini-omni-flash-preview", "input": [...]}
    ADC bearer、GCP 計費 → 專案額度蓋得到、專案規矩（不用 AI Studio 金鑰）合規。

省錢：形狀錯的 payload 回 4xx 不計費，照慣例先排候選形狀。

用法：
    python scripts/probes/verify_omni.py <提示詞檔.txt> <參考圖.png>
"""

from __future__ import annotations

import base64
import json
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

PROJECT_ID = "greenlight-2026"
MODEL = "gemini-omni-flash-preview"
BASE = f"https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/global"
OUT_PATH = _ROOT / "outputs" / "verify_omni.mp4"


def _headers() -> dict[str, str]:
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json; charset=utf-8",
        "x-goog-user-project": PROJECT_ID,
    }


def _candidates(prompt: str, img_b64: str, mime: str) -> list[tuple[str, dict[str, Any]]]:
    text_part = {"type": "text", "text": prompt}
    return [
        (
            "A. input[text, image{data,mime_type}] + generation_config.task",
            {
                "model": MODEL,
                "input": [
                    text_part,
                    {"type": "image", "data": img_b64, "mime_type": mime},
                ],
                "generation_config": {
                    "task": "reference_to_video",
                    "duration_seconds": 8,
                    "aspect_ratio": "16:9",
                },
            },
        ),
        (
            "B. image 巢狀 bytesBase64Encoded（Veo 慣例）",
            {
                "model": MODEL,
                "input": [
                    text_part,
                    {
                        "type": "image",
                        "image": {"bytesBase64Encoded": img_b64, "mimeType": mime},
                    },
                ],
                "generation_config": {
                    "task": "reference_to_video",
                    "duration_seconds": 8,
                    "aspect_ratio": "16:9",
                },
            },
        ),
        (
            "C. 只有文字＋圖，不帶 generation_config（讓模型自己判斷任務）",
            {
                "model": MODEL,
                "input": [
                    text_part,
                    {"type": "image", "data": img_b64, "mime_type": mime},
                ],
            },
        ),
        (
            "D. 純文字（最保守，確認基本通路）",
            {"model": MODEL, "input": [text_part]},
        ),
    ]


def _find_video(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """遞迴找回應裡的影片（base64 或 URI）。回 (路徑, 種類) 列表。"""
    hits: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, str):
                if k in ("data", "bytesBase64Encoded", "bytes_base64_encoded") and len(v) > 10000:
                    hits.append((p, "base64"))
                elif isinstance(v, str) and v.startswith(("gs://", "https://")) and (
                    "video" in k.lower() or "uri" in k.lower() or v.endswith(".mp4")
                ):
                    hits.append((p, "uri"))
            else:
                hits.extend(_find_video(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_find_video(v, f"{path}[{i}]"))
    return hits


def _get_by_path(obj: Any, path: str) -> Any:
    import re

    for token in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if token.startswith("["):
            obj = obj[int(token[1:-1])]
        else:
            obj = obj[token]
    return obj


def main() -> int:
    if len(sys.argv) < 3:
        print("用法：python scripts/probes/verify_omni.py <提示詞檔.txt> <參考圖.png>")
        return 2
    prompt = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    img_path = Path(sys.argv[2])
    img_b64 = base64.b64encode(img_path.read_bytes()).decode()
    mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
    print(f"提示詞 {len(prompt)} 字 · 參考圖 {img_path.name}")

    headers = _headers()
    body: dict | None = None
    for label, payload in _candidates(prompt, img_b64, mime):
        print(f"\n→ 試 {label}")
        res = requests.post(f"{BASE}/interactions", headers=headers, json=payload, timeout=900)
        if res.status_code == 200:
            body = res.json()
            print("  ✓ 接受（開始計費）")
            break
        detail = ""
        try:
            detail = res.json().get("error", {}).get("message", "")[:180]
        except Exception:  # noqa: BLE001
            detail = res.text[:180]
        print(f"  ✗ HTTP {res.status_code}（$0）：{detail}")

    if body is None:
        print("\n全部形狀被拒，這一趟 $0。")
        return 1

    # 同步或輪詢都處理：官方範例直接回 status=completed，但保險起見。
    interaction_id = body.get("id", "")
    started = time.monotonic()
    while body.get("status") in ("in_progress", "pending", "processing", "running"):
        elapsed = int(time.monotonic() - started)
        print(f"  …生成中（{elapsed}s）")
        if elapsed > 900:
            print("逾時。interaction:", interaction_id)
            return 1
        time.sleep(15)
        res = requests.get(f"{BASE}/interactions/{interaction_id}", headers=_headers(), timeout=120)
        body = res.json()

    print("status:", body.get("status"), "| usage:", json.dumps(body.get("usage", {}))[:200])

    hits = _find_video(body)
    if not hits:
        # 沒找到影片就把回應結構印出來給人看（截斷 base64 以免洗版）
        print("回應裡找不到影片欄位。結構：")
        print(json.dumps(body, ensure_ascii=False, default=str)[:1500])
        return 1

    path, kind = hits[0]
    print(f"影片欄位：{path}（{kind}）")
    if kind == "base64":
        data = base64.b64decode(_get_by_path(body, path))
    else:
        uri = _get_by_path(body, path)
        print("下載：", uri[:100])
        data = requests.get(uri, timeout=300).content

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(data)
    info = probe(data)
    print(
        f"\n✓ {OUT_PATH}\n  {info.get('width')}×{info.get('height')} · "
        f"{info.get('seconds')}s · {len(data) / 1024 / 1024:.1f} MB"
    )
    print("interaction_id（之後測對話式編輯要用）:", interaction_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
