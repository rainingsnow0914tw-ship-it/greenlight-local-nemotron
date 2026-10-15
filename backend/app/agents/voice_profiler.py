"""聽自己生出來的聲音，把音色描述回寫成角色的固定屬性。

為什麼需要：

    「只要出現過第一次的音色就鎖定在那個角色裡面…
      它變成了那個音色再出現一次的話，還是用之前第一次生成的那個音色，
      能不能延續下去呢？不能隨機生成，這是固定的喔。」

## 技術現實：Veo 沒有「音色 ID」

    Veo 不像 TTS 可以指定 voice name 或 speaker id。每一鏡都是從
    提示詞的文字描述重新生成配音，所以「把第一次的音色存下來下次直接用」
    在 API 層面**做不到**。

    能做的是逼近：**用實際生成的聲音，回寫出更精確的描述**。

        事先猜（原本）  「這個角色應該是溫柔的年輕女聲」
        聽完回寫（這裡）「二十多歲女聲，氣音偏多，句尾略微下沉，語速偏慢」

    第一鏡的音色仍然是模型隨機決定的 —— 那無法避免。
    但從第二鏡開始，描述的是**真正存在過的那個聲音**，
    而不是一段憑空想像。這是這個系統能給的最強保證。

## 為什麼這件事非 agent 不可

    沒有獨立的檢核 agent，生成品質問題就沒有人抓。

    影片模型不會回頭聽自己生出來的東西，也不會知道「上一鏡的聲音」
    長什麼樣。要讓十二個獨立生成的鏡頭聽起來像同一個人，
    必須有東西在中間**聽過、記住、再講給下一次聽**。
    那就是這支 agent 在做的事。

成本：一段 8 秒影片約 $0.002（Flash，影片 token 便宜）。
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from ..services import rates
from ..services import api_retry, config_loader


class VoiceProfile(BaseModel):
    """從實際音訊聽出來的音色描述。"""

    voice_tags: str = Field(
        description=(
            "英文音色描述，一句話，會被注入之後每一鏡的台詞段。"
            "寫**聽得出來的物理特徵**：年齡帶、性別感、音高、氣音、"
            "共鳴、語速、句尾處理。不要寫情緒或個性 —— 那每一鏡都不同。"
        )
    )
    has_speech: bool = Field(
        description="這段影片裡到底有沒有人聲。沒有的話上面那欄留空。"
    )
    note: str = Field(
        default="",
        description="給人看的一句中文說明，例如「偏低沉的男聲，語速偏慢」。",
    )


def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    音色分析。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.QUICK_MS, action="分析音色")


def _model() -> str:
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("voice_profiling", "flash")
    tiers = pricing.get("tiers", {})
    return tiers.get(tier_key, {}).get("model_id", "gemini-3.6-flash")


_SYSTEM = """You listen to a generated video clip and describe the speaking voice
so that the same voice can be requested again in later clips.

Write ONLY what is audible in the voice itself:
- apparent age range and gender presentation
- pitch (low / mid / high), timbre (breathy, clear, husky, resonant)
- pacing (slow, measured, quick) and how sentences end (falling, soft, clipped)

Do NOT describe:
- emotion or personality — those change from shot to shot
- what is being said, the language, or the content
- music, sound effects, or ambience

The output is a prompt fragment that will be injected into every later shot,
so it must describe a voice that can be reproduced, not a moment.

If there is no speech at all in the clip, set has_speech to false and leave
voice_tags empty. Do not invent a voice that was not there."""


def profile_voice(video: bytes, mime_type: str = "video/mp4") -> tuple[VoiceProfile, dict[str, Any]]:
    """聽一段影片，回傳音色描述與用量。

    直接送整段影片而不是抽音軌：Gemini 吃得下 mp4，
       抽音軌要再開一次 ffmpeg，多一個會失敗的環節換不到什麼好處。
    """
    from google.genai import types

    client = _client()
    model = _model()

    response = api_retry.governed(
        model,
        lambda: client.models.generate_content(
            model=model,
            contents=types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=video, mime_type=mime_type),
                    types.Part.from_text(
                        text="Describe the speaking voice in this clip."
                    ),
                ],
            ),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                response_mime_type="application/json",
                response_schema=VoiceProfile,
                # 這是描述既有事實，不是創作 —— 溫度壓到最低
                temperature=0.1,
                max_output_tokens=1_024,
            ),
        ),
        label="voice_profiler",
    )

    if not response.text:
        raise RuntimeError("音色分析沒有回傳任何內容")

    profile = VoiceProfile.model_validate_json(response.text)

    meta = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0
    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    cost = (
        prompt_tokens * float(cfg.get("input_per_1m_usd", 0.3))
        + output_tokens * float(cfg.get("output_per_1m_usd", 2.5))
    ) / 1_000_000

    return profile, {
        "model": model,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 5),
    }
