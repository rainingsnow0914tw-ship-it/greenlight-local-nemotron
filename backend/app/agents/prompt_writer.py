"""把鏡頭描述改寫成英文提示詞，填進 `Shot.visual_prompt`。

為什麼需要這一步：

    `Shot.description` 是中文（給人看的），`Shot.visual_prompt` 是英文
    （給模型的）—— schema 從一開始就分開了這兩欄，但 `visual_prompt`
    一直是 None，所以導出的提示詞長這樣：

        [英文角色標籤][英文畫風][英文鏡別]**[中文畫面描述]**[英文物理約束]

    一段中文夾在一堆英文中間，是服從度最不穩的形態。使用者拿去別的工具
    測，測到的會是「這個工具讀不懂」，而不是「這個鏡頭設計好不好」。

## 只翻畫面描述，不翻台詞

    畫面描述 → 提示詞語言（prompt_language）：這是講給模型聽的
    角色台詞   → 影片語言（film_language）：這是角色真的要說的話

    翻譯台詞等於改變影片內容 —— 中文劇本的角色會突然說英文。
    這兩個維度在三語架構裡本來就是分開的，這裡必須維持。

## 為什麼整段一次翻，不逐鏡翻

    逐鏡翻 12 次要付 12 次系統提示詞的成本，而且模型看不到上下文，
    同一個場景的連續鏡頭會用不一致的詞（一鏡寫 "dim room"、
    下一鏡寫 "dark chamber"）。整段一次給，用詞才會連貫。

成本：一段 12 鏡約 $0.002（Flash）。
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.shot import ShotList
from ..services import rates
from ..services import api_retry, config_loader, prompt_rooms

_LANG_NAMES = {
    "en": "English",
    "zh": "Traditional Chinese",
    "ja": "Japanese",
}


class _Rewritten(BaseModel):
    shot_id: str
    visual_prompt: str = Field(
        description=(
            "改寫後的畫面描述。只描述**看得見的東西**：主體、動作、環境、"
            "光線、構圖。不要包含鏡別、運鏡、畫風、台詞、音效 —— 那些由系統組裝。"
        )
    )


class _Result(BaseModel):
    shots: list[_Rewritten]


def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    提示詞：實測 15.2 秒。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.QUICK_MS, action="產生提示詞")


def _model() -> str:
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("prompt_writing", "flash")
    tiers = pricing.get("tiers", {})
    return tiers.get(tier_key, {}).get("model_id", "gemini-3.6-flash")


def _system(target_lang: str) -> str:
    """組 system instruction。

    走 prompt_rooms 拿該語言的寫作慣例 —— 不同語言描述畫面的方式不同，
    英文重具體名詞與光線術語、日文的「間」「余白」在動畫語境下更精準。
    """
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    base = (
        f"You rewrite shot descriptions into {lang_name} prompts for a video "
        f"generation model.\n\n"
        "Rules:\n"
        "- Describe ONLY what is visible: subject, action, setting, lighting, "
        "composition, mood conveyed through concrete visuals.\n"
        "- Do NOT include shot size, camera movement, art style, dialogue, "
        "sound, or negative instructions. The system assembles those separately; "
        "repeating them causes conflicting instructions.\n"
        "- Do NOT invent objects, characters, or actions that are not in the "
        "source description. Hallucinated props are a known failure mode.\n"
        "- Keep proper nouns and character names out of the prompt — the model "
        "does not know who they are. Refer to characters by visible attributes.\n"
        "- 🔴 NEVER assume a character is human. When a name appears in the "
        "source, use the character notes provided to decide what it looks like. "
        "A character with no physical body (an AI, a system, a voice) must be "
        "described as the visual form given in its notes — never as a person, "
        "and never with human words like 'man', 'woman', 'he', 'she', 'sits', "
        "'stands', 'face', 'hands'.\n"
        "- If a character's notes are not provided, describe it neutrally as "
        "'the figure' or 'the subject' rather than guessing a gender or species.\n"
        "- Keep each prompt under 60 words. Long prompts dilute the tags that "
        "come before them.\n"
        "- Use consistent vocabulary across shots in the same scene. If shot 1 "
        "calls it a 'dim server room', shot 2 must not call it a 'dark chamber'."
    )

    try:
        conventions = prompt_rooms.writing_conventions(target_lang)
        if conventions:
            base += f"\n\nLanguage conventions:\n{conventions}"
    except Exception:  # noqa: BLE001
        # 房間查不到就用基本規則 —— 這一步是加分不是必要，
        # 不該讓「日文房間檔名打錯」導致整個導出功能不能用。
        pass

    return base


def _character_notes(
    shot_list: ShotList,
    casting: Any | None,
    non_human: set[str],
) -> str:
    """把鏡頭表用到的角色整理成給模型的參考。

    為什麼非有不可：
       原本只把中文描述丟給模型翻譯。中文寫「深息獨自坐在幽暗的虛擬空間中」，
       模型翻成 "A young man sits motionless in a dark virtual space" ——
       但深息是**沒有身體的 AI**，使用者選定的形象是一個懸浮多面體。

       於是同一份提示詞裡，角色標籤說「多面體」、畫面描述說「年輕男子」，
       兩個互相打架。中文版看不出這個問題，因為「深息」在中文裡就只是個名字，
       模型不必決定它是什麼；一翻成英文就被迫具體化，錯誤才浮現。

       **翻譯會把原文的模糊變成譯文的斷言** —— 這是跨語言改寫特有的風險。
    """
    names: list[str] = []
    for shot in shot_list.shots:
        for name in shot.characters_in_frame or []:
            if name not in names:
                names.append(name)

    if not names:
        return ""

    lines: list[str] = []
    for name in names:
        if name not in non_human:
            # 人類角色**不給外貌**：
            #    原本這裡輸出 "Looks like: young asian woman, wavy black hair…"，
            #    同時在 system prompt 裡寫「絕不描述角色外貌」——
            #    **給了資料又禁止使用，模型一定會用。**
            #    實測：月音的四鏡描述全部照抄髮型與毛衣，跟系統另外注入的
            #    角色標籤變成同一份提示詞裡的兩套外貌，模型選了描述那套。
            #    要它不寫，就不能把外貌放在它看得到的地方。
            lines.append(f"- {name}: an ordinary human character")
            continue

        # 非人角色相反：**必須**給形態，否則會被寫成人。
        # 「多面體緩緩轉動」跟「他抬起頭」是兩回事，
        # 模型不知道這個角色沒有身體，就會寫成後者。
        session = casting.session_for(name) if casting else None
        chosen = session.selected if session else None
        form = (chosen.appearance_tags or "").strip() if chosen else ""
        if form:
            lines.append(
                f"- {name}: NOT a person — it has no body. It appears as: {form}"
            )
        else:
            lines.append(
                f"- {name}: NOT a person — it has no body and no chosen form yet. "
                f"Refer to it only by what it does, never as a man or woman."
            )

    return (
        "Characters in these shots. This tells you WHAT KIND of thing each one is.\n"
        "Do NOT copy any of this into your descriptions — the system injects "
        "appearance separately. Use it only to avoid writing a bodiless entity "
        "as if it were a person.\n"
        + "\n".join(lines)
    )


def rewrite_shot_list(
    shot_list: ShotList,
    target_lang: str = "en",
    casting: Any | None = None,
    non_human: set[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """把整份鏡頭表的描述改寫成目標語言，就地填進 `visual_prompt`。

    回傳 (改寫成功的鏡頭數, 用量統計)。

    Args:
        casting: 選角關卡，用來查角色選定的外貌
        non_human: 沒有實體身體的角色名 —— 少了這個，AI 角色會被翻成人

    就地修改 shot_list —— 呼叫端負責存檔。
       這樣同一份鏡頭表可以先改寫再存一次，不會產生兩份不同步的資料。
    """
    from google.genai import types

    shots = [s for s in shot_list.shots if s.description]
    if not shots:
        return 0, {"model": "", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    notes = _character_notes(shot_list, casting, non_human or set())

    lines = [
        f"[{s.shot_id}] scene {s.scene_number}, {s.total_seconds}s\n{s.description}"
        for s in shots
    ]
    user_prompt = (
        "Rewrite each of these shot descriptions. Return one entry per shot, "
        "keeping the same shot_id.\n\n"
    )
    if notes:
        user_prompt += notes + "\n\n"
    user_prompt += "\n\n".join(lines)

    client = _client()
    model = _model()

    response = api_retry.call(
        lambda: client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=_system(target_lang),
                response_mime_type="application/json",
                response_schema=_Result,
                # 改寫是轉述不是創作 —— 溫度低一點，避免它自己加戲。
                temperature=0.3,
                max_output_tokens=8_192,
            ),
        ),
        label="prompt_writer",
    )

    if not response.text:
        raise RuntimeError("提示詞改寫模型沒有回傳任何內容")

    result = _Result.model_validate_json(response.text)
    by_id = {r.shot_id: r.visual_prompt for r in result.shots}

    filled = 0
    for shot in shot_list.shots:
        text = by_id.get(shot.shot_id)
        if text and text.strip():
            shot.visual_prompt = text.strip()
            filled += 1

    return filled, _usage(response, model)


def _usage(response: Any, model: str) -> dict[str, Any]:
    meta = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0

    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    in_rate = float(cfg.get("input_per_1m_usd", 0.3))
    out_rate = float(cfg.get("output_per_1m_usd", 2.5))

    return {
        "model": model,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(
            (prompt_tokens * in_rate + output_tokens * out_rate) / 1_000_000, 5
        ),
    }
