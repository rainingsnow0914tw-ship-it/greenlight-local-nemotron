# -*- coding: utf-8 -*-
"""Gemini TTS 實測：API 形狀、節奏控制、時長量測。

選 A（先念再拍），但 TTS 要有快慢抑揚頓挫、不是定速朗讀。

要驗三件事：
  1. `gemini-3.1-flash-tts-preview` 在 Vertex 上的請求形狀
  2. **能不能用自然語言指示節奏**（Gemini TTS 跟傳統 SSML 不同，
     它是用 prompt 講「這裡慢下來、這裡停一拍」）
  3. 回傳音檔的**實際長度** —— 第三層架構選了「先念再拍」，
     所以必須量得出秒數才能決定那一鏡要生幾秒

成本：TTS 按字元計，一段旁白約 $0.00x —— 探測便宜到不用猶豫。
但仍然存檔再說（探測沒存檔白花 $2 的教訓）。
"""
from __future__ import annotations

import os
import base64
import json
import struct
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

OUT = Path(__file__).resolve().parents[1] / "docs" / "tts_probe"
PROJECT = "greenlight-2026"
MODEL = "gemini-3.1-flash-tts-preview"
ENDPOINT = (
    f"https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT}"
    f"/locations/global/publishers/google/models/{MODEL}:generateContent"
)

# 同一段旁白，兩種指示 —— 用來看「節奏指示到底有沒有作用」。
# 定速版與抑揚版如果長度一樣、聽起來一樣，那就是沒吃。
NARRATION = "他不是不會忘記。他只是每一次，都重新選擇記得。"

FLAT = f"用平穩的語速念出這段旁白：{NARRATION}"
DYNAMIC = (
    "你是紀錄片旁白。用沉穩的中年男聲念出下面這段話，"
    "而且要有抑揚頓挫：前半句語速稍快、平述；"
    "在「他只是」之前停頓一拍；"
    "最後一句「都重新選擇記得」明顯放慢，一字一字沉下來收尾。"
    f"\n\n{NARRATION}"
)


def _token() -> str:
    for exe in ("gcloud.cmd", "gcloud"):
        try:
            r = subprocess.run(
                [exe, "auth", "print-access-token", *(["--account", os.environ["GCLOUD_ACCOUNT"]] if os.environ.get("GCLOUD_ACCOUNT") else [])],
                capture_output=True, text=True, check=True,
            )
            return r.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("拿不到 access token")


def _pcm_seconds(raw: bytes, sample_rate: int = 24_000, bits: int = 16, channels: int = 1) -> float:
    """算 PCM 音訊的秒數。

    Gemini TTS 回的是裸 PCM（不是 wav），沒有檔頭可以讀，
    只能用「位元組數 ÷ (取樣率 × 位元組寬 × 聲道)」算。
    取樣率若不是 24k，這個數字就會錯 —— 回應的 mimeType 會寫，
       所以下面會把 mimeType 印出來對照。
    """
    return len(raw) / (sample_rate * (bits // 8) * channels)


def _to_wav(raw: bytes, sample_rate: int = 24_000) -> bytes:
    """替裸 PCM 補上 wav 檔頭，才能用一般播放器聽。"""
    n = len(raw)
    return (
        b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data" + struct.pack("<I", n) + raw
    )


def run(label: str, text: str, voice: str = "Charon") -> None:
    print(f"\n═══ {label}（voice={voice}）═══")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    r = requests.post(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-user-project": PROJECT,
        },
        json=payload,
        timeout=300,
    )
    print("HTTP", r.status_code)
    if r.status_code != 200:
        print(json.dumps(r.json(), ensure_ascii=False)[:400])
        return

    body = r.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    blob = next((p["inlineData"] for p in parts if "inlineData" in p), None)
    if not blob:
        print("回應裡沒有音訊：", json.dumps(body, ensure_ascii=False)[:300])
        return

    raw = base64.b64decode(blob["data"])
    mime = blob.get("mimeType", "")
    rate = 24_000
    if "rate=" in mime:
        try:
            rate = int(mime.split("rate=")[1].split(";")[0])
        except ValueError:
            pass

    secs = _pcm_seconds(raw, sample_rate=rate)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{label}.wav"
    path.write_bytes(_to_wav(raw, rate))

    usage = body.get("usageMetadata", {})
    print(f"  mimeType: {mime}")
    print(f"  音訊長度: {secs:.2f} 秒 · {len(raw)} bytes")
    print(f"  usage: {usage.get('promptTokenCount')} in / {usage.get('candidatesTokenCount')} out")
    print(f"  已存: {path}")


if __name__ == "__main__":
    run("01_平穩定速", FLAT)
    run("02_有抑揚頓挫", DYNAMIC)
    print("\n兩支長度若明顯不同，代表節奏指示有吃 —— 再聽一次確認語氣。")
