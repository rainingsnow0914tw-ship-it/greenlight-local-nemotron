# -*- coding: utf-8 -*-
"""Omni 兩個關鍵控制實驗。

實驗一：參數 vs 參考圖方向
    直式參考圖 + generation_config {duration_seconds: 8, aspect_ratio: "16:9"}。
    假設：上一輪生成直式是因為參考圖是 3:4 直式。
    參數贏 → 比例秒數管得住，架構不用動。

實驗二：對話式編輯（Omni 的獨門，也是螢幕問題的出路）
    拿上一輪的 interaction_id 叫它把筆電螢幕轉向鏡頭、其他都不動。
    成功 → 「看片檢核抓問題 → 編輯修問題」的迴路成立。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

import requests  # noqa: E402

from app.services import env_boot  # noqa: E402
from app.services.video_gen import probe  # noqa: E402

env_boot.load()

BASE = "https://aiplatform.googleapis.com/v1beta1/projects/greenlight-2026/locations/global"
MODEL = "gemini-omni-flash-preview"
PREV_ID = "K9h3asSeKNCX7OsPzv7zuQw"


def _headers():
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json; charset=utf-8",
        "x-goog-user-project": "greenlight-2026",
    }


def run(label: str, payload: dict, out_name: str) -> None:
    print(f"\n═══ {label} ═══")
    r = requests.post(f"{BASE}/interactions", headers=_headers(), json=payload, timeout=900)
    print("HTTP", r.status_code)
    body = r.json()
    if r.status_code != 200:
        print(json.dumps(body, ensure_ascii=False)[:300])
        return
    started = time.monotonic()
    while body.get("status") in ("in_progress", "pending", "processing", "running"):
        time.sleep(15)
        body = requests.get(f"{BASE}/interactions/{body['id']}", headers=_headers(), timeout=120).json()
        if time.monotonic() - started > 800:
            print("timeout")
            return
    print("status:", body.get("status"), "| out tokens:", body.get("usage", {}).get("total_output_tokens"))
    for step in body.get("steps", []):
        for c in step.get("content", []) or []:
            if c.get("type") == "video":
                data = base64.b64decode(c["data"])
                out = _ROOT / "outputs" / out_name
                out.write_bytes(data)
                print("saved", out.name, probe(data), len(data) // 1024, "KB")
                print("interaction_id:", body.get("id"))
                return
    print("no video:", json.dumps(body, default=str)[:400])


# 提示詞檔放系統暫存目錄（或用 OMNI_PROMPT_FILE 指定路徑）
prompt = Path(os.getenv("OMNI_PROMPT_FILE", str(Path(tempfile.gettempdir()) / "s002_01_prompt.txt"))).read_text(encoding="utf-8").strip()
img_b64 = base64.b64encode((_ROOT / "outputs" / "luna_ref.png").read_bytes()).decode()

run(
    "實驗一：直式參考圖 + 明確 16:9 / 8 秒參數",
    {
        "model": MODEL,
        "input": [
            {"type": "text", "text": prompt},
            {"type": "image", "data": img_b64, "mime_type": "image/png"},
        ],
        # duration_seconds 實測是未知參數；aspect_ratio 上一輪沒被點名，單獨試
        "generation_config": {"aspect_ratio": "16:9"},
    },
    "verify_omni_16x9.mp4",
)

# previous_interaction_id 在 Vertex 這條路不支援（實測 400）——
# 改用把影片本體餵回去的形狀做編輯
_vid_b64 = base64.b64encode((_ROOT / "outputs" / "verify_omni.mp4").read_bytes()).decode()
run(
    "實驗二：對話式編輯 — 影片餵回去 + 修改指令",
    {
        "model": MODEL,
        "input": [
            {"type": "video", "data": _vid_b64, "mime_type": "video/mp4"},
            {
                "type": "text",
                "text": (
                    "Turn the laptop so its glowing screen directly faces the camera, "
                    "showing soft abstract glowing panels with no readable text. "
                    "Keep everything else exactly the same: the same woman, same "
                    "appearance, same voice, same dialogue, same room, same lighting, "
                    "same camera framing."
                ),
            }
        ],
    },
    "verify_omni_edit.mp4",
)
