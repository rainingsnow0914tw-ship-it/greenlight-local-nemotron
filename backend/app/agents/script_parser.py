"""劇本解析 —— 把非結構化的文字變成場景與角色。

設計理由：
    這是整條鏈路的第一步，也是最關鍵的一步。場景切錯、角色漏抓、
    時間推估歪掉，後面分鏡、預算、成功率全部跟著錯，而且錯得很難發現。
    所以這一步用 Gemini 3.1 Pro，不省那 $0.11（見 pricing.yaml 的說明）。

真實劇本不是標準格式（用一份真實劇本驗證）：
    那份完全沒有 `INT./EXT.` 場景標題行 —— 是小說體，混合系統日誌
    （時間戳、狀態標記）、對話、內心獨白、「用戶視角筆記」。

    **這才是真實創作者手上的東西。** 有好故事的人多半不寫標準劇本格式，
    這正是我們的目標用戶。所以場景切分必須靠語意判斷
    （地點變了、時間跳了、視角換了），不是找 slugline。

成本控制：
    agent 只回報**行號範圍**，不把原文抄一遍。
    一份 40K token 的劇本，讓模型複製貼上回來就是 40K token 的輸出，
    以 Pro 的 $12/1M 計算等於白花 $0.48。原文我們手上就有。
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.screenplay import (
    Character,
    Scene,
    SceneSetting,
    Screenplay,
    TimeOfDay,
)
from ..services import rates
from ..services import config_loader

# ---------------------------------------------------------------------------
# 給模型的輸出結構
#
# 刻意跟 schemas/screenplay.py 的 Scene 不同 —— 這裡是「模型該產出什麼」，
# 那裡是「系統內部怎麼存」。中間隔一層轉換的好處：
#   改 prompt 或換模型時，只動這裡，不會波及整個系統的資料契約。
# ---------------------------------------------------------------------------


class ParsedScene(BaseModel):
    """模型回報的一場戲。"""

    scene_number: int = Field(description="場次編號，從 1 開始連續編")
    source_line_start: int = Field(description="這場戲在原檔的起始行號（1-based）")
    source_line_end: int = Field(description="結束行號（含）")

    setting: str = Field(description="INT / EXT / INT_EXT / UNKNOWN")
    location: str = Field(description="地點。抓不到就寫「未指明」")
    time_of_day: str = Field(description="DAY / NIGHT / DAWN / DUSK / CONTINUOUS / UNKNOWN")

    summary: str = Field(description="這場戲發生什麼事，一到兩句中文")
    characters_present: list[str] = Field(description="在場角色的名字")
    has_dialogue: bool = Field(description="這場戲有沒有對白")

    estimated_screen_seconds: float = Field(
        description=(
            "推估銀幕時間（秒）。依內容判斷，不是用字數換算 —— "
            "一句「三年後」是一個鏡頭幾秒，一段沉默的對峙可能要三十秒。"
        )
    )

    split_reason: str = Field(
        description="為什麼在這裡切一場：地點變/時間跳/視角換/敘事段落結束"
    )


class ParsedCharacter(BaseModel):
    """模型回報的一個角色。"""

    name: str
    visual_description: str = Field(
        description=(
            "劇本裡**直接寫出來**的外貌描述。"
            "🔴 劇本沒寫就回空字串，不要自己編 —— "
            "編出來的外貌會被當成劇本原意，誤導後面的選角。"
        )
    )
    is_non_human: bool = Field(
        description="是不是非人角色（AI、動物、抽象存在）。影響選角時給哪組方向按鈕。"
    )
    is_principal: bool = Field(description="是不是主要角色")
    line_count_estimate: int = Field(description="大約有幾句台詞")
    inference_note: str = Field(
        description="如果外貌是推測的，說明依據什麼推的；直接寫明的話註明在第幾行"
    )


class ParseResult(BaseModel):
    """一次解析的完整結果。"""

    title: str
    language: str = Field(description="劇本語言，例如 zh-TW")
    scenes: list[ParsedScene]
    characters: list[ParsedCharacter]
    warnings: list[str] = Field(
        description=(
            "解析過程中的疑慮，例如「第 7 段無法判斷地點」。"
            "🔴 這些必須顯示給使用者 —— 沉默地猜測是最糟的做法。"
        )
    )


# ---------------------------------------------------------------------------
# 提示詞
# ---------------------------------------------------------------------------

_SYSTEM = """你是一位資深的場記與分場師，專門把各種格式的文字稿整理成可拍攝的場次表。

你會遇到的稿件**多半不是標準劇本格式**。可能是小說、散文、對話紀錄、
帶時間戳的日誌、或這些的混合。不要因為找不到 INT./EXT. 場景標題行就放棄 ——
標題行不存在是常態，不是例外。

分場的判斷依據（依重要性排序）：
1. 地點改變
2. 時間跳躍（明確的時間戳、或「三天後」這類敘述）
3. 視角切換（例如從 A 的內心獨白切到 B 的視角）
4. 敘事段落的自然結束

不要把一整章當成一場。也不要把每一句對白切成一場。
判準是：**這一段能不能用一個連續的場景拍完。**

推估銀幕時間時，依內容判斷不要用字數換算：
- 一句「三年後」是一個鏡頭，幾秒
- 一段沉默的對峙可能要三十秒
- 大量內心獨白通常會壓縮成畫面 + 旁白

🔴 誠實原則：
- 劇本沒寫的外貌不要編。編出來的會被當成原作設定，誤導後面的選角。
- 判斷不確定的地方寫進 warnings，不要沉默地猜。
- 行號要準確 —— 系統會依你給的行號從原檔切出原文。"""


def _user_prompt(numbered_text: str, title_hint: str) -> str:
    return f"""以下是需要整理成場次表的稿件，每行前面標了行號。

檔名提示：{title_hint}

請整理出：
1. 場次表（含行號範圍、地點、時間、在場角色、劇情摘要、推估秒數、分場理由）
2. 角色表（含劇本直接寫明的外貌；沒寫就留空）
3. 你在過程中不確定的地方

稿件：
────────────────────────────────────────
{numbered_text}
────────────────────────────────────────"""


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    劇本解析：實測 67.8 秒，原本給它 900 秒。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.LONG_MS, action="解析劇本")


def _model_for(task: str) -> str:
    """依 pricing.yaml 的 assignments 決定用哪顆模型。

    不在 code 裡寫死 model id —— 模型會改版、會退役
    （Gemini 2.5 全系列 退役），改 config 不改 code。
    """
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get(task, "pro")
    return pricing.get("tiers", {}).get(tier_key, {}).get("model_id", "gemini-3.1-pro-preview")


def _number_lines(text: str) -> str:
    """給每行加上行號。

    模型要靠行號回報場次範圍，所以行號必須在輸入裡明確可見 ——
    不能指望它自己數。
    """
    return "\n".join(f"{i}\t{line}" for i, line in enumerate(text.splitlines(), 1))


_SETTING_MAP = {
    "INT": SceneSetting.INT,
    "EXT": SceneSetting.EXT,
    "INT_EXT": SceneSetting.INT_EXT,
    "INT/EXT": SceneSetting.INT_EXT,
}

_TIME_MAP = {
    "DAY": TimeOfDay.DAY,
    "NIGHT": TimeOfDay.NIGHT,
    "DAWN": TimeOfDay.DAWN,
    "DUSK": TimeOfDay.DUSK,
    "CONTINUOUS": TimeOfDay.CONTINUOUS,
}


def parse(text: str, title_hint: str = "") -> tuple[Screenplay, dict[str, Any]]:
    """解析一份劇本。回傳 (Screenplay, 用量統計)。

    用量統計包含 token 數與估算成本 —— 這是 $100 硬預算下的基本透明度，
    每一次呼叫都要能說出花了多少。
    """
    from google.genai import types

    client = _client()
    model = _model_for("script_parsing")
    lines = text.splitlines()

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        response_mime_type="application/json",
        response_schema=ParseResult,
        temperature=0.2,  # 解析是事實性工作，不要創意
        max_output_tokens=32_768,
    )

    # 用串流不用一次性回應：
    #    長劇本的結構化輸出可能跑好幾分鐘。非串流模式下，
    #    連線會在伺服器還在生成時就被切斷，錯誤訊息是
    #    `Server disconnected without sending a response` —— 看起來像網路問題，
    #    實際上是「等太久」。
    #    串流模式下持續有資料流動，連線不會閒置到被切。
    chunks: list[str] = []
    last_chunk = None
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=_user_prompt(_number_lines(text), title_hint),
        config=config,
    ):
        last_chunk = chunk
        if chunk.text:
            chunks.append(chunk.text)

    raw_json = "".join(chunks)
    if not raw_json.strip():
        raise RuntimeError("模型沒有回傳任何內容")

    parsed = ParseResult.model_validate_json(raw_json)
    response = last_chunk  # 用量統計在最後一個 chunk 上

    # ---- 轉成系統內部的 Screenplay ----
    scenes: list[Scene] = []
    for ps in parsed.scenes:
        # 依行號從原檔切出原文 —— 這一段不經過模型，所以不花錢也不會被改寫
        start = max(1, ps.source_line_start)
        end = min(len(lines), ps.source_line_end)
        raw = "\n".join(lines[start - 1 : end]) if start <= end else ""

        scenes.append(
            Scene(
                scene_number=ps.scene_number,
                setting=_SETTING_MAP.get(ps.setting.upper(), SceneSetting.UNKNOWN),
                location=ps.location or None,
                time_of_day=_TIME_MAP.get(ps.time_of_day.upper(), TimeOfDay.UNKNOWN),
                slugline=None,  # 小說體沒有 slugline，這是常態
                action=ps.summary,
                characters_present=ps.characters_present,
                estimated_screen_seconds=ps.estimated_screen_seconds,
                source_line_start=start,
                source_line_end=end,
                raw_text=raw,
            )
        )

    characters = [
        Character(
            name=pc.name,
            visual_description=pc.visual_description or None,
            first_appearance_scene=next(
                (s.scene_number for s in scenes if pc.name in s.characters_present),
                None,
            ),
            line_count=pc.line_count_estimate,
            is_principal=pc.is_principal,
        )
        for pc in parsed.characters
    ]

    screenplay = Screenplay(
        title=parsed.title or title_hint,
        source_format="text",
        scenes=scenes,
        characters=characters,
        total_estimated_seconds=sum(s.estimated_screen_seconds for s in scenes),
        language=parsed.language or "zh-TW",
        parse_warnings=parsed.warnings,
    )

    usage = _usage_stats(response, model)

    # 把非人角色的判斷帶出去 —— Character schema 沒有這個欄位
    # （它是選角階段才需要的資訊），用附加資料傳遞比硬塞進 schema 乾淨。
    usage["non_human_characters"] = [
        pc.name for pc in parsed.characters if pc.is_non_human
    ]
    usage["character_notes"] = {
        pc.name: pc.inference_note for pc in parsed.characters if pc.inference_note
    }

    return screenplay, usage


def _usage_stats(response: Any, model: str) -> dict[str, Any]:
    """算這次呼叫花了多少。

    $100 硬預算下，每一次 API 呼叫都要能說出花了多少。
       沒有這個，「還剩多少可以用」就只能用猜的。
    """
    meta = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0

    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    in_rate = float(cfg.get("input_per_1m_usd", 2.0))
    out_rate = float(cfg.get("output_per_1m_usd", 12.0))

    cost = (prompt_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate

    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 4),
    }
