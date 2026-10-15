"""驗證 9:16 是不是真的輸出直式影片。約 $0.20。

為什麼要單獨寫一支：
    `verify_apis.py --check veo` 只確認 API 回 200 —— 那不叫驗證。
    有開發者回報 Veo 會強制輸出 16:9 即使要求 9:16，
    如果屬實，整個直式功能要重新設計（分鏡邏輯、成功率修正、UI 預覽框）。

    **API 回 200 不等於它照做了。** 要真的把檔案存下來量寬高。

尺寸怎麼量：
    不依賴 ffmpeg —— 直接解析 MP4 的 tkhd box。
    MP4 是 box 結構：moov → trak → tkhd，tkhd 最後 8 bytes 是
    width/height，格式是 16.16 定點數（高 16 位是整數部分）。

    自己解析的好處是零依賴，不用先裝 ffmpeg。

用法：
    python scripts/verify_aspect_ratio.py
"""

from __future__ import annotations

import base64
import struct
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

import os  # noqa: E402

import requests  # noqa: E402

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "greenlight-2026")
LOCATION = "us-central1"
MODEL = "veo-3.1-lite-generate-001"


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


def mp4_dimensions(data: bytes) -> tuple[int, int] | None:
    """從 MP4 位元組解析出影片寬高。

    走訪 box 結構找 moov → trak → tkhd。
    tkhd 的版本 0 佈局：
        4 bytes  size
        4 bytes  'tkhd'
        1 byte   version
        3 bytes  flags
        ... （version 0 是 4+4+4 = 12 bytes 的時間欄位，version 1 是 8+8+4=20）
        ... 中間還有 reserved / layer / volume / matrix 共 60 bytes
        4 bytes  width   ← 16.16 定點數
        4 bytes  height  ← 16.16 定點數

    最穩的做法：找到 tkhd box 之後，直接取它**最後 8 bytes**。
    不管 version 0 還是 1，width/height 永遠在 box 尾端。
    """

    def walk(buf: bytes, start: int, end: int) -> tuple[int, int] | None:
        pos = start
        while pos + 8 <= end:
            size = struct.unpack(">I", buf[pos : pos + 4])[0]
            box_type = buf[pos + 4 : pos + 8]

            if size == 0:      # 延伸到檔尾
                size = end - pos
            elif size == 1:    # 64-bit size，在 box type 之後
                if pos + 16 > end:
                    return None
                size = struct.unpack(">Q", buf[pos + 8 : pos + 16])[0]

            if size < 8 or pos + size > end:
                return None

            if box_type == b"tkhd":
                tail = buf[pos + size - 8 : pos + size]
                w = struct.unpack(">I", tail[0:4])[0] >> 16
                h = struct.unpack(">I", tail[4:8])[0] >> 16
                # 有些軌道（音訊）的 tkhd 寬高是 0，要跳過繼續找
                if w and h:
                    return w, h

            # moov / trak / mdia 是容器，要往裡面走
            if box_type in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
                found = walk(buf, pos + 8, pos + size)
                if found:
                    return found

            pos += size
        return None

    return walk(data, 0, len(data))


def generate(ratio: str) -> bytes | None:
    """生一段 4 秒影片，回傳 mp4 位元組。"""
    headers = _headers()
    payload = {
        "instances": [{"prompt": "A single tall candle burning steadily, static shot"}],
        "parameters": {
            "durationSeconds": 4,
            "aspectRatio": ratio,
            "sampleCount": 1,
            "generateAudio": True,
        },
    }

    resp = requests.post(
        _endpoint("predictLongRunning"), headers=headers, json=payload, timeout=90
    )
    if resp.status_code != 200:
        print(f"  送出失敗 HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    op = resp.json().get("name")
    print(f"  operation: {op.split('/')[-1][:24]}…")

    deadline = time.time() + 420
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
            print(f"  生成失敗：{body['error']}")
            return None

        # 回應結構在不同版本間有差異，兩種都試
        response = body.get("response", {})
        videos = response.get("videos") or response.get("predictions") or []
        if not videos:
            print(f"  回應裡沒有影片：{list(response.keys())}")
            return None

        item = videos[0]
        b64 = (
            item.get("bytesBase64Encoded")
            or item.get("video")
            or item.get("gcsUri")
            or ""
        )
        if b64.startswith("gs://"):
            print(f"  影片存在 GCS：{b64}（此腳本只處理 inline base64）")
            return None
        return base64.b64decode(b64)

    print("  逾時")
    return None


def main() -> int:
    out_dir = _ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)

    print("驗證 aspectRatio=9:16 是否真的輸出直式")
    print(f"專案 {PROJECT_ID} · 模型 {MODEL} · 4 秒 · 約 $0.20\n")

    data = generate("9:16")
    if not data:
        print("\n生成失敗，無法驗證。")
        return 1

    path = out_dir / "verify_9x16.mp4"
    path.write_bytes(data)
    print(f"\n  已存檔：{path}（{len(data) / 1024:.0f} KB）")

    dims = mp4_dimensions(data)
    if not dims:
        print("  無法解析尺寸 —— 請自己用播放器開來看。")
        return 1

    w, h = dims
    print(f"  實際尺寸：{w} × {h}")
    print()

    if h > w:
        print(f"  ✓ 通過 —— 直式影片，比例約 {w}:{h}（{w / h:.3f}）")
        print("    9:16 參數有效，config 的直式設定可以照用。")
        return 0

    print(f"  ✗ 未通過 —— 輸出是橫式 {w}×{h}，9:16 參數被忽略了")
    print()
    print("  影響：")
    print("    · pricing.yaml 的 aspect_ratios 9:16 區塊要標記為不可用")
    print("    · UI 的直式選項要移除，或改成「生成後裁切」的替代方案")
    print("    · 直式的成功率修正（遠景 -0.12、多人 ×1.4）用不上了")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
