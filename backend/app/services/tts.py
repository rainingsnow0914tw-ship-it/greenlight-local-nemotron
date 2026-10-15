# -*- coding: utf-8 -*-
"""旁白配音 —— Gemini TTS。

選定的架構（三層修法的第三層）：
    影片模型只生**畫面與環境音**，旁白由 TTS 另外合成，最後混音。

為什麼要把旁白從影片模型拿出來（一個改動解掉三件事）：

    1. **對嘴** —— 影片裡根本沒有人聲，畫面中的人不可能被要求去對嘴。
       這比在提示詞裡求它「嘴巴閉著」可靠得多。
    2. **音色漂移** —— 影片模型每一鏡獨立生成，音色帶隨機性；
       同一段旁白在第 1 鏡和第 8 鏡會是兩個人的聲音。TTS 是同一個 voice。
    3. **內容審查** —— 台詞不進影片模型，就不會被它的安全過濾器擋下。
       跟字幕是同一個道理：我們自己疊上去的東西，審查管不到。

時間軸策略：**先念再拍**
    先合成旁白、量出它幾秒，再決定那一鏡的影片要生幾秒
    （Omni 的 duration 3–10 秒可調，過）。
    選 A 不選「旁白跨鏡連續播」的理由：每一鏡自成單位，
    跟現有的逐鏡確認流程相容 —— 一段旁白橫跨三鏡的話，
    「第二鏡重拍要不要重念」會變成沒有好答案的問題。

節奏：要有快慢抑揚頓挫，不是定速朗讀。
    Gemini TTS **不吃 SSML**，它讀的是自然語言的表演指示，
    所以 casting.yaml 的 `narrator.tts.direction` 寫的是
    「導演對配音員說的話」。實測有效：同一段文字，
    平穩念 5.88 秒、加了指示變 9.00 秒（scripts/verify_tts.py）。
"""
from __future__ import annotations

import base64
import os
import struct
from dataclasses import dataclass
from typing import Any

import requests

from . import api_retry, config_loader


class TTSError(RuntimeError):
    """旁白合成失敗。"""


@dataclass
class Narration:
    """一段合成好的旁白。

    Attributes:
        wav: 完整的 wav 位元組（已補檔頭，可直接存檔或餵 ffmpeg）
        seconds: 實際長度。「先念再拍」策略就靠這個決定影片要生幾秒。
        voice: 用了哪個聲音（存進紀錄，重生時要一致）
        cost_usd: 這次合成花了多少
    """

    wav: bytes
    seconds: float
    voice: str
    cost_usd: float


def _tts_config() -> dict[str, Any]:
    return config_loader.casting().get("narrator", {}).get("tts", {})


def _endpoint(model: str) -> str:
    project = os.getenv("GCP_PROJECT_ID")
    if not project:
        raise TTSError("請設定 GCP_PROJECT_ID")
    return (
        f"https://aiplatform.googleapis.com/v1beta1/projects/{project}"
        f"/locations/global/publishers/google/models/{model}:generateContent"
    )


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
        # REST 打 Vertex 一定要帶這個，否則錯誤訊息極度誤導
        #    （有些 endpoint 回 404「Publisher model was not found」，
        #     會讓人以為 model ID 打錯，實際上 ID 完全正確）。
        "x-goog-user-project": os.getenv("GCP_PROJECT_ID", ""),
    }


def _to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """替裸 PCM 補上 wav 檔頭。

    為什麼需要：Gemini TTS 回的是 `audio/l16`（裸 PCM），沒有檔頭 ——
    直接存成 .wav 的話播放器認不得，ffmpeg 也要額外參數才讀得動。
    補 44 bytes 的標準檔頭最省事，之後所有工具都當它是普通 wav。
    """
    n = len(pcm)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + n)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", n)
        + pcm
    )


def synthesize(text: str, *, voice: str | None = None, direction: str | None = None) -> Narration:
    """把一段旁白文字合成成語音。

    為什麼要有這個函數？
    見模組檔頭：旁白留在影片模型裡會同時帶來對嘴、音色漂移、
    內容審查三個問題，拿出來一次解決。

    設計來源：旁白要有快慢抑揚頓挫、不是定速朗讀，
    以及同日向平台 AI 查證的架構建議。

    回傳的 `seconds` 是**算出來的不是猜的** —— PCM 沒有檔頭，
       長度只能用「位元組數 ÷ (取樣率 × 位元組寬 × 聲道)」推。
       取樣率從回應的 mimeType 讀（`audio/l16; rate=24000`），
       不要寫死 24000：換模型或換設定時那個數字會變，
       而算錯的秒數會讓「先念再拍」決定出錯誤的影片長度。

    Args:
        text: 要念的旁白原文（劇本裡的那句，不要翻譯）
        voice: prebuilt voice 名稱。None 用 config 的預設。
        direction: 表演指示。None 用 config 的預設（紀錄片旁白語氣）。

    Returns:
        Narration：wav 位元組、實際秒數、用的聲音、花費

    Raises:
        TTSError: 合成失敗或回應裡沒有音訊
    """
    from . import demo_mode

    demo_mode.guard("合成旁白配音", model="Gemini Flash TTS")

    cfg = _tts_config()
    model = cfg.get("model_id", "gemini-3.1-flash-tts-preview")
    voice = voice or cfg.get("voice", "Charon")
    direction = direction if direction is not None else cfg.get("direction", "")

    # 表演指示放在原文**前面** —— Gemini TTS 把整段 prompt 讀成
    # 「導演的話 + 要念的內容」，指示放後面它可能當成台詞的一部分念出來。
    prompt = f"{' '.join(str(direction).split())}\n\n{text}" if direction else text

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }

    def _do() -> dict[str, Any]:
        r = requests.post(_endpoint(model), headers=_headers(), json=payload, timeout=300)
        if r.status_code != 200:
            raise TTSError(f"HTTP {r.status_code} 旁白合成失敗：{r.text[:300]}")
        return r.json()

    body = api_retry.governed(model, _do, label="tts")

    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    blob = next((p["inlineData"] for p in parts if "inlineData" in p), None)
    if not blob or not blob.get("data"):
        raise TTSError("旁白合成沒有回傳音訊")

    pcm = base64.b64decode(blob["data"])
    mime = str(blob.get("mimeType", ""))
    rate = 24_000
    if "rate=" in mime:
        try:
            rate = int(mime.split("rate=")[1].split(";")[0])
        except ValueError:
            pass

    seconds = len(pcm) / (rate * 2)  # 16-bit 單聲道 → 每秒 rate × 2 bytes

    usage = body.get("usageMetadata", {})
    tiers = config_loader.pricing().get("gemini", {}).get("tiers", {})
    tcfg = next((c for c in tiers.values() if c.get("model_id") == model), {})
    cost = (
        usage.get("promptTokenCount", 0) * float(tcfg.get("input_per_1m_usd", 0.3))
        + usage.get("candidatesTokenCount", 0) * float(tcfg.get("output_per_1m_usd", 2.5))
    ) / 1_000_000

    return Narration(
        wav=_to_wav(pcm, rate),
        seconds=round(seconds, 2),
        voice=str(voice),
        cost_usd=round(cost, 5),
    )


def clip_seconds_for(narration_seconds: float) -> int:
    """旁白這麼長，那一鏡的影片該生幾秒。

    為什麼要有這個函數？
    「先念再拍」的核心一步。Omni 的 duration 只能是整數秒且限 3–10
    ，所以要把 TTS 的秒數對到那個範圍。

    **無條件進位**不是四捨五入：影片比旁白短會把話切斷，
       比旁白長只是結尾多一點畫面。兩種誤差的代價差很多。
    旁白超過 10 秒時只能截到 10 —— 那時 UI 要警告使用者
       「這段旁白比一鏡能拍的還長」，讓他自己決定拆句還是接受。
       這裡不做決定，只回上限。
    """
    import math

    limits = config_loader.veo_tier(config_loader.default_veo_tier())
    lo, hi = 3, 10
    fixed = limits.get("fixed_seconds")
    if fixed:
        hi = int(fixed)
    return max(lo, min(hi, math.ceil(narration_seconds)))
