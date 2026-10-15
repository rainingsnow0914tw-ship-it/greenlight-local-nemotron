"""驗證 Veo 3.1 的 Extend（續拍）到底怎麼呼叫、實際輸出多長。約 $0.35。

為什麼這支很重要：
    「一段 15 秒」＝ 8 秒生成 + 一次續拍 7 秒。
    整個分鏡設計（`Shot.extensions`）、成本估算（`cost_engine`）、
    成功率修正（`per_extension_penalty`）**全部建立在續拍可行這個假設上**，
    但到今天為止我們一次都沒真的呼叫過。

    如果續拍的形狀跟假設不同，或根本不支援上傳的影片，
    那 15 秒長鏡的功能要重新設計。

省錢的做法（這支腳本的核心設計）：
    **錯誤的 payload 回 4xx 不計費**，所以形狀探索完全免費。
    腳本先依序試幾種候選形狀，只有在拿到 200（真的開始生成）之後
    才會產生費用。全部形狀都失敗的話，這一趟是 $0。

    基礎片段直接用 outputs/verify_9x16.mp4（上次驗證比例時生的 4 秒），
    不重生 —— 省掉 $0.20。

計費怎麼看：
    API 不會告訴你這一次花多少。續拍是按「新增的秒數」還是「輸出總秒數」
    計費，只能事後對 Cloud Billing 的帳單。這支腳本會把實際輸出秒數印出來，
    對帳時拿這個數字去比。

用法：
    python scripts/verify_extend.py
"""

from __future__ import annotations

import base64
import json
import os
import struct
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

env_boot.load()

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "greenlight-2026")
LOCATION = "us-central1"
MODEL = "veo-3.1-lite-generate-001"

BASE_VIDEO = _ROOT / "outputs" / "verify_9x16.mp4"
OUT_PATH = _ROOT / "outputs" / "verify_extend.mp4"

PROMPT = "The camera holds steady as the candle continues to burn, wax slowly dripping"


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


def mp4_info(data: bytes) -> dict[str, Any]:
    """從 MP4 位元組解析寬高與長度。

    寬高在 tkhd 的最後 8 bytes（16.16 定點數）。
    長度在 mvhd：timescale 與 duration，秒數 = duration / timescale。

    自己解析而不用 ffmpeg —— 零依賴，不用先裝東西。
    """
    info: dict[str, Any] = {}

    def walk(buf: bytes, start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            size = struct.unpack(">I", buf[pos : pos + 4])[0]
            box_type = buf[pos + 4 : pos + 8]

            if size == 0:
                size = end - pos
            elif size == 1:
                if pos + 16 > end:
                    return
                size = struct.unpack(">Q", buf[pos + 8 : pos + 16])[0]

            if size < 8 or pos + size > end:
                return

            if box_type == b"tkhd" and "width" not in info:
                tail = buf[pos + size - 8 : pos + size]
                w = struct.unpack(">I", tail[0:4])[0] >> 16
                h = struct.unpack(">I", tail[4:8])[0] >> 16
                if w and h:
                    info["width"], info["height"] = w, h

            if box_type == b"mvhd" and "seconds" not in info:
                version = buf[pos + 8]
                # version 0: timescale 在 offset 20、duration 在 24（都是 4 bytes）
                # version 1: 時間欄位是 8 bytes，timescale 在 28、duration 在 32（8 bytes）
                if version == 0:
                    timescale = struct.unpack(">I", buf[pos + 20 : pos + 24])[0]
                    duration = struct.unpack(">I", buf[pos + 24 : pos + 28])[0]
                else:
                    timescale = struct.unpack(">I", buf[pos + 28 : pos + 32])[0]
                    duration = struct.unpack(">Q", buf[pos + 32 : pos + 40])[0]
                if timescale:
                    info["seconds"] = round(duration / timescale, 2)

            if box_type in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                walk(buf, pos + 8, pos + size)

            pos += size

    walk(data, 0, len(data))
    return info


# ---------------------------------------------------------------------------
# 候選 payload 形狀
# ---------------------------------------------------------------------------
#
# 官方文件對 Veo 3.1 的 extend 講得不清楚，所以用實測反推。
#    依「最可能」到「最不可能」排序，第一個回 200 的就是答案。
#    4xx 不計費，所以排錯順序不影響成本 —— 只影響花多少時間。


def _candidates(video_b64: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "A. instances[].video + parameters.durationSeconds",
            {
                "instances": [
                    {
                        "prompt": PROMPT,
                        "video": {
                            "bytesBase64Encoded": video_b64,
                            "mimeType": "video/mp4",
                        },
                    }
                ],
                "parameters": {
                    "durationSeconds": 7,
                    "sampleCount": 1,
                    "generateAudio": True,
                },
            },
        ),
        (
            "B. 同 A，但不帶 mimeType",
            {
                "instances": [
                    {
                        "prompt": PROMPT,
                        "video": {"bytesBase64Encoded": video_b64},
                    }
                ],
                "parameters": {"durationSeconds": 7, "sampleCount": 1},
            },
        ),
        (
            "C. parameters.extendVideo 旗標",
            {
                "instances": [
                    {
                        "prompt": PROMPT,
                        "video": {
                            "bytesBase64Encoded": video_b64,
                            "mimeType": "video/mp4",
                        },
                    }
                ],
                "parameters": {
                    "durationSeconds": 7,
                    "sampleCount": 1,
                    "extendVideo": True,
                },
            },
        ),
        (
            "D. instances[].inputVideo",
            {
                "instances": [
                    {
                        "prompt": PROMPT,
                        "inputVideo": {
                            "bytesBase64Encoded": video_b64,
                            "mimeType": "video/mp4",
                        },
                    }
                ],
                "parameters": {"durationSeconds": 7, "sampleCount": 1},
            },
        ),
    ]


def _try_shape(label: str, payload: dict[str, Any], headers: dict[str, str]) -> str | None:
    """送出一種形狀。回傳 operation name，失敗回 None。"""
    print(f"\n試 {label}")
    try:
        resp = requests.post(
            _endpoint("predictLongRunning"), headers=headers, json=payload, timeout=180
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  送出時例外：{exc}")
        return None

    if resp.status_code == 200:
        op = resp.json().get("name", "")
        print(f"  ✓ HTTP 200 —— 這個形狀對了。operation {op.split('/')[-1][:28]}…")
        return op

    # 4xx 不計費，所以把錯誤訊息完整印出來 —— 那是下一次該怎麼改的線索
    detail = resp.text[:400].replace("\n", " ")
    print(f"  ✗ HTTP {resp.status_code}: {detail}")
    return None


def _poll(op: str, headers: dict[str, str]) -> dict[str, Any] | None:
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(12)
        poll = requests.post(
            _endpoint("fetchPredictOperation"),
            headers=headers,
            json={"operationName": op},
            timeout=60,
        )
        if poll.status_code != 200:
            print(f"  輪詢失敗 HTTP {poll.status_code}: {poll.text[:200]}")
            return None
        body = poll.json()
        if not body.get("done"):
            print("  生成中…")
            continue
        if "error" in body:
            print(f"  生成失敗：{json.dumps(body['error'], ensure_ascii=False)[:300]}")
            return None
        return body
    print("  逾時")
    return None


def main() -> int:
    if not BASE_VIDEO.exists():
        print(f"找不到基礎片段 {BASE_VIDEO}")
        print("先跑 scripts/verify_aspect_ratio.py 生一支（約 $0.20）。")
        return 1

    base_bytes = BASE_VIDEO.read_bytes()
    base_info = mp4_info(base_bytes)
    print("Veo 3.1 Extend 驗證")
    print(f"專案 {PROJECT_ID} · 模型 {MODEL}")
    print(
        f"基礎片段 {BASE_VIDEO.name}："
        f"{base_info.get('width')}×{base_info.get('height')} · "
        f"{base_info.get('seconds')} 秒 · {len(base_bytes) / 1024:.0f} KB"
    )
    print("\n🔴 形狀探索階段不計費（4xx 不收錢）。只有拿到 200 才會開始扣款。")

    headers = _headers()
    video_b64 = base64.b64encode(base_bytes).decode()

    op = None
    used_shape = ""
    for label, payload in _candidates(video_b64):
        op = _try_shape(label, payload, headers)
        if op:
            used_shape = label
            break

    if not op:
        print("\n" + "=" * 70)
        print("四種形狀全部失敗，這一趟 $0。")
        print("上面每一則錯誤訊息都是線索 —— 特別注意它有沒有點名")
        print("『哪個欄位不認得』或『這個模型不支援這個功能』：")
        print("  · 前者代表形狀要改，續拍本身可行")
        print("  · 後者代表 Lite 檔位不支援續拍，要改用 Fast 或 Standard 重試")
        print("\n影響（如果續拍完全不可行）：")
        print("  · shot_planner 的 extensions 要停用，15 秒長鏡改成拆成兩鏡接首尾幀")
        print("  · priors.yaml 的 per_extension_penalty 用不上")
        print("  · pricing.yaml 對續拍的計費假設要拿掉")
        return 1

    print(f"\n開始輪詢（形狀：{used_shape}）")
    body = _poll(op, headers)
    if not body:
        return 1

    response = body.get("response", {})
    videos = response.get("videos") or response.get("predictions") or []
    if not videos:
        print(f"  回應裡沒有影片。頂層 key：{list(response.keys())}")
        return 1

    item = videos[0]
    print(f"\n  回應項目的 key：{list(item.keys())}")

    b64 = item.get("bytesBase64Encoded") or item.get("video") or ""
    gcs = item.get("gcsUri") or ""
    if gcs and not b64:
        print(f"  🔴 影片走 GCS 不是 inline：{gcs}")
        print("     video_gen 要同時處理 inline base64 與 gcsUri 兩種回應。")
        return 1

    data = base64.b64decode(b64)
    OUT_PATH.write_bytes(data)
    info = mp4_info(data)

    print("\n" + "=" * 70)
    print(f"已存檔：{OUT_PATH}（{len(data) / 1024:.0f} KB）")
    print(f"輸出尺寸：{info.get('width')} × {info.get('height')}")
    print(f"輸出長度：{info.get('seconds')} 秒")
    print()

    base_sec = base_info.get("seconds") or 0
    out_sec = info.get("seconds") or 0

    if out_sec > base_sec + 1:
        print(f"  ✓ 續拍有效 —— {base_sec} 秒 → {out_sec} 秒（+{out_sec - base_sec:.1f}）")
        print()
        print("  🔴 計費要對帳單確認：這一次是按新增的 "
              f"{out_sec - base_sec:.0f} 秒計，還是按輸出總長 {out_sec:.0f} 秒計。")
        print("     Lite 720p $0.05/s → 前者約 "
              f"${(out_sec - base_sec) * 0.05:.2f}、後者約 ${out_sec * 0.05:.2f}。")
        print("     cost_engine 目前保守假設是**按輸出總秒數**。")
        return 0

    print(f"  ✗ 長度沒有增加（{base_sec} → {out_sec}）—— 它可能只是重新生成了一段。")
    print("     這種情況比報錯更危險：API 回 200、檔案也存下來了，")
    print("     但續拍其實沒發生。成本照付、長鏡頭沒拿到。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
