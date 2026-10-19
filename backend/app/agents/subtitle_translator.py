"""字幕翻譯 —— 讓同一批影片出多語版本。

產品需求：字幕要能在 UI 選擇生成三語版本。

為什麼翻字幕不等於翻台詞：
    影片裡的**配音**是角色真的說出口的話，那由 `film_language` 決定，
    翻譯它等於改掉作品內容（角色突然改講英文）。
    字幕是**給看不懂那個語言的觀眾**的輔助 —— 翻譯它不改變任何東西。

    這就是外掛字幕的價值：影片只生一次（12 鏡 $5.41），
    字幕想出幾種語言都行。

## 翻譯字幕跟翻譯文章不同

    字幕有硬性限制：一句話停留兩三秒，觀眾要在瞄一眼的時間內讀完。
    所以要求的是「短、口語、能被瞄一眼讀完」，不是「忠實完整」。
    這些規則寫在 system instruction 裡。

成本：一章 12 句約 $0.002（Flash）。
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from ..services import rates
from ..services import api_retry, config_loader

_LANG_NAMES = {
    "zh": "Traditional Chinese",
    "en": "English",
    "ja": "Japanese",
}


class _Line(BaseModel):
    index: int = Field(description="字幕編號，照抄輸入的編號")
    text: str = Field(description="翻譯後的字幕文字")


class _Result(BaseModel):
    lines: list[_Line]


def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    字幕翻譯。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.QUICK_MS, action="翻譯字幕")


def _model() -> str:
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("translation", "flash")
    tiers = pricing.get("tiers", {})
    return tiers.get(tier_key, {}).get("model_id", "gemini-3.6-flash")


def _system(target: str) -> str:
    name = _LANG_NAMES.get(target, target)
    return f"""You translate film subtitles into {name}.

Subtitles are not prose. Each line is on screen for two or three seconds and
the viewer is also watching the picture, so:

- Keep it SHORT. Cut anything the picture already tells the viewer.
- Use natural spoken register, not literary or formal writing.
- One line per subtitle. Do not split a line into two sentences.
- Preserve the speaker's tone — hesitant stays hesitant, blunt stays blunt.
- Keep bracketed stage markers like （旁白）as an equivalent marker in the
  target language, or drop them if that language's subtitle convention omits them.
- Never add information that is not in the source line.

Return the same number of lines, keeping each index unchanged."""


def translate_cues(
    texts: list[tuple[int, str]],
    target_lang: str,
    glossary_text: str = "",
) -> tuple[dict[int, str], dict[str, Any]]:
    """翻譯一批字幕。回傳 ({編號: 譯文}, 用量)。

    整批一次翻而不是逐句 —— 逐句翻譯的譯者看不到上下文，
    同一個稱謂會前後不一致（一句叫「你」下一句叫「您」）。
    """
    from google.genai import types

    if not texts:
        return {}, {"model": "", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    # 名字表：沒有它，日文原聲的「つきね」會原樣留在中文字幕、英文拼成 Tsukune
    #（範例專案實證）。提示詞先講，譯完再用名字表把漏網的換掉。
    from ..services.ui_translate import fix_names, name_hints

    system = _system(target_lang)
    names = name_hints(glossary_text, target_lang)
    if names and target_lang != "ja":
        # 日文原聲的台詞裡名字寫成讀音，要讓譯者認得出那是誰
        readings = []
        for ln in glossary_text.splitlines():
            if "=" in ln:
                name, rest = (x.strip() for x in ln.split("=", 1))
                reading = rest.partition("/")[0].strip()
                if reading and reading != name:
                    readings.append(f"{reading} = {name}")
        if readings:
            names += "\n" + "\n".join(readings)
    if names:
        system += f"\n\nCharacter names — write them exactly like this:\n{names}"

    client = _client()
    model = _model()
    body = "\n".join(f"[{i}] {t}" for i, t in texts)

    response = api_retry.governed(
        model,
        lambda: client.models.generate_content(
            model=model,
            contents=f"Translate these subtitle lines:\n\n{body}",
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=_Result,
                temperature=0.3,
                max_output_tokens=4_096,
            ),
        ),
        label=f"subtitle:{target_lang}",
    )

    if not response.text:
        raise RuntimeError("字幕翻譯沒有回傳任何內容")

    result = _Result.model_validate_json(response.text)
    mapping = {
        line.index: fix_names(line.text.strip(), glossary_text, target_lang)
        for line in result.lines if line.text.strip()
    }

    meta = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0
    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    cost = (
        prompt_tokens * float(cfg.get("input_per_1m_usd", 0.3))
        + output_tokens * float(cfg.get("output_per_1m_usd", 2.5))
    ) / 1_000_000

    return mapping, {
        "model": model,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 5),
    }
