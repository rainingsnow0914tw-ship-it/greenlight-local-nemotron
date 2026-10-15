"""看自己生出來的影片，檢查畫面有沒有違反分鏡表的硬事實。

為什麼需要：

    「第六分鏡有問題，也是之前踩過的坑，它補了一個男生的角色頭在右下，
      之前是補一個男生在左下。」

    當時 `exclude_unspecified_props` 規則**明明有套用**，模型照樣加人 ——
    提示詞層的服從永遠是機率性的。改了兩層（規則 + code 組裝的硬邊界）
    之後，剩下的殘餘風險靠這裡收：**生完讓 Gemini 看一次片**，
    對照分鏡表的結構化事實（畫面該有幾個人、該不該有文字），
    對不上就在鏡頭卡標警告。

    這跟 voice_profiler 是同一個哲學：
    沒有獨立的檢核 agent，生成品質問題就沒有人抓。
    影片模型不會回頭看自己生的東西 —— 要有東西替它看。

檢核不裁決（跟整個產品的閘門哲學一致）：
    發現問題只**標出來**，通過與否永遠是人按的。
    模型看片也會看錯（把雕像當人、把倒影當第二個人），
    自動打回會把誤判變成白花的錢。

紀錄三層（要留 log、可翻查、可被後續規劃引用）：
    1. 鏡頭紀錄 `clip.audit` —— 每一鏡的檢核結果永久存在專案裡，隨時翻
    2. 專案決策日誌 —— 有發現就記一筆，時間軸上查得到
    3. 學習迴路 —— 重拍時檢核發現寫進 ClickHouse 的 rejection_reason，
       提煉層看得到「這種鏡頭常亂補人」的模式

成本：一段 8 秒影片約 $0.002（Flash）。
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from ..services import rates
from ..services import api_retry, config_loader


class FrameAudit(BaseModel):
    """Gemini 看完一段影片回報的畫面事實。"""

    people_count: int = Field(
        description=(
            "整段影片中**同一時間**畫面上最多出現幾個人形 ——"
            "含臉、頭、剪影、背對鏡頭的人、邊角的局部人影。"
            "抽象形體（光帶、幾何體、數據流）不算人形。"
        )
    )
    has_readable_text: bool = Field(
        description=(
            "畫面上有沒有出現**可讀的文字**（任何語言的字、字幕、招牌字）。"
            "模糊的發光色塊、抽象符號不算。"
        )
    )
    note: str = Field(
        default="",
        description="給人看的一句中文說明，例如「右下角出現一個背對鏡頭的男性頭部」。",
    )


def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    看片檢核：實測 $0.002/鏡、秒級。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.QUICK_MS, action="看片檢核")


def _model() -> str:
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("frame_audit", "flash")
    tiers = pricing.get("tiers", {})
    return tiers.get(tier_key, {}).get("model_id", "gemini-3.6-flash")


_SYSTEM = """You inspect a generated video clip and report OBSERVABLE facts only.

Count people conservatively but honestly:
- A "person" is any human figure: full body, face, head, silhouette,
  a partial figure at a frame edge, or someone turned away from camera.
- Report the MAXIMUM number of distinct people visible at the same moment.
- Abstract entities (light ribbons, geometric shapes, data streams,
  glowing interfaces) are NOT people, even if they behave like characters.
- Statues, posters and reflections: count them only if they could be
  mistaken for a real person in the scene; mention them in the note.

Readable text means actual legible words or characters in any language —
subtitles, signs, screen text. Blurred glows and abstract glyphs do not count.

Report only what is in the frames. Do not guess what should be there."""


def audit_clip(
    video: bytes,
    *,
    expected_people: int | None,
    allow_text: bool,
    mime_type: str = "video/mp4",
) -> tuple[FrameAudit, list[str], dict[str, Any]]:
    """看一段影片，回傳（原始觀察、與分鏡表對照後的發現、用量）。

    Args:
        expected_people: 這一鏡應該有幾個「人形」角色（非人角色不算）。
            None = 跳過人數檢查（例如 crowd 人群戲，本來就該有背景人物）。
        allow_text: 這一鏡有沒有要求畫面文字（text_in_frame 標記）。
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
                        text="Report the observable facts of this clip."
                    ),
                ],
            ),
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                response_mime_type="application/json",
                response_schema=FrameAudit,
                # 這是清點既有畫面，不是創作 —— 溫度壓到最低
                temperature=0.1,
                max_output_tokens=1_024,
            ),
        ),
        label="frame_auditor",
    )

    if not response.text:
        raise RuntimeError("看片檢核沒有回傳任何內容")

    audit = FrameAudit.model_validate_json(response.text)

    # ---- 與分鏡表的結構化事實對照 ----
    findings: list[str] = []
    if expected_people is not None:
        if audit.people_count > expected_people:
            where = f"（{audit.note}）" if audit.note else ""
            findings.append(
                f"畫面最多出現 {audit.people_count} 個人形，"
                + (
                    "但這一鏡應該完全沒有人"
                    if expected_people == 0
                    else f"但這一鏡應該只有 {expected_people} 位角色"
                )
                + where
            )
        elif audit.people_count < expected_people:
            findings.append(
                f"這一鏡應該有 {expected_people} 位角色，"
                f"畫面只看到 {audit.people_count} 個 —— 角色可能沒有生出來"
            )
    if audit.has_readable_text and not allow_text:
        findings.append("畫面出現可讀的文字，這一鏡沒有要求任何文字")

    meta = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0
    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    cost = (
        prompt_tokens * float(cfg.get("input_per_1m_usd", 0.3))
        + output_tokens * float(cfg.get("output_per_1m_usd", 2.5))
    ) / 1_000_000

    return audit, findings, {
        "model": model,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 5),
    }
