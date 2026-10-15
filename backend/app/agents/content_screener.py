# -*- coding: utf-8 -*-
"""內容安全預審 agent。

劇本上傳解析之後就先掃一遍：哪些內容可能違反平台或模型的規定、
生成時會被擋，並建議哪裡可以調整 —— 降低使用者撞牆的門檻。

兩個入口，對應兩個時機：

    screen_script()      解析完就掃全劇本 —— 早期預警，一次看到全片地雷
    explain_rejection()  真的被擋時 —— 指出兇手是哪一句、給建議

═══════════════════════════════════════════════════════════════════════════
為什麼不能做成違禁詞表
═══════════════════════════════════════════════════════════════════════════

A/B 實測：同一句台詞放在完整提示詞裡整鏡被拒；
放在簡化版提示詞裡 200 通過，還真的生出影片。

安全判定看的是整段語境，不是關鍵字比對。所以這裡交給模型判語境，
config 只提供類別與判準。也因為如此，這一關不能承諾通過 ——
UI 只寫「掃出 N 處高風險」，不寫「已通過安全檢查」。

═══════════════════════════════════════════════════════════════════════════
為什麼 API 幫不上忙
═══════════════════════════════════════════════════════════════════════════

被擋時 API 只回一句「This input contains content that violates Google's
Responsible AI practices」，不會說是哪一句。而且沒有便宜的探針可用：

  · 毒藥 payload（故意寫錯 enum）→ schema 驗證先跑，看不到安全審查
  · 純文字輸出 → Omni 看到像影片提示詞的文字就自己生影片（$1 一次，
    實測踩到）

所以歸因只能靠讀。好在有一個零成本的事後確認：
無聲重拍成功 = 兇手就是台詞（那 $1 本來就要花）。
"""
from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..services import rates
from ..services import api_retry, config_loader


class Finding(BaseModel):
    """一處可能過不了審查的內容。"""

    category_id: str = Field(
        description=(
            "content_policy.yaml 裡的類別 id，例如 manipulation_coercion。"
            "只能用提供的清單裡有的 id。"
        )
    )
    # 只回行號不回原文（沿用劇本解析那條教訓）：
    #    讓模型把 40K token 的劇本抄回來 = 白花 $0.48。原文由 code 切。
    source_line_start: int = Field(description="這一處在劇本原文的起始行號（從 1 起算）")
    source_line_end: int = Field(description="結束行號。單行就跟起始行相同。")
    is_dialogue: bool = Field(
        description="這一處是角色台詞（會進音軌）還是敘述／場景描述。"
    )
    why: str = Field(description="為什麼這一處可能過不了。一到兩句中文，講具體的，不要照抄類別說明。")
    suggestion: str = Field(
        description=(
            "給編劇的具體建議：這一段可以怎麼調整。中文。"
            "🔴 建議要保住原本的戲劇效果 —— 不是叫他刪掉，是給一個等效的寫法。"
        )
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="有多確定這一處會被擋。過濾器是黑盒，不確定就誠實標 low。"
    )


class ScreenResult(BaseModel):
    """整份劇本掃過一遍的結果。"""

    findings: list[Finding]
    summary: str = Field(
        description=(
            "一段中文總結：整體風險程度、最需要注意的是什麼。"
            "如果因為數量上限沒有全部列出，要在這裡說明還有大約幾處。"
        )
    )


class RejectionAnalysis(BaseModel):
    """一鏡被安全審查擋下之後的歸因。"""

    likely_cause: Literal["dialogue", "visual_description", "both", "unclear"] = Field(
        description="最可能的兇手是哪一部分。判斷不出來就誠實回 unclear。"
    )
    excerpt: str = Field(
        description="兇手那一句／那一段的原文摘錄。輸入很短，這裡可以直接抄。"
    )
    category_id: str = Field(description="對應的政策類別 id；對不上就填 unknown。")
    explanation: str = Field(description="用中文講給編劇聽：為什麼這一段會被擋。兩三句。")
    suggestion: str = Field(description="中文的具體建議：可以怎麼調整。")


def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    內容預審：實測 62.8 秒。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.LONG_MS, action="內容安全預審")


def _model() -> str:
    pricing = config_loader.pricing().get("gemini", {})
    key = (
        config_loader.content_policy()
        .get("screening", {})
        .get("model_assignment", "script_screening")
    )
    tier_key = pricing.get("assignments", {}).get(key, "pro")
    tiers = pricing.get("tiers", {})
    return tiers.get(tier_key, {}).get("model_id", "gemini-3.1-pro-preview")


def _usage(model: str, meta: Any) -> dict[str, Any]:
    """把 API 的 usage_metadata 換算成含金額的用量。

    費率查不到時用 pro 的保守值 —— 寧可高估花費也不要讓帳本少記，
       $100 硬預算下低報比高報危險。
    """
    prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0
    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    in_rate = float(cfg.get("input_per_1m_usd", 1.25))
    out_rate = float(cfg.get("output_per_1m_usd", 10.0))
    return {
        "model": model,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(
            (prompt_tokens * in_rate + output_tokens * out_rate) / 1_000_000, 5
        ),
    }


def _policy_brief() -> str:
    """把 config 的類別表壓成給模型看的簡報。

    為什麼要有這個函數？
    政策類別要能被校準（改 config 不改 code），所以 system prompt
    不能把類別寫死。這裡每次從 config 現組。

    只送 id / severity / what 三個欄位 —— `advice` 是給人看的三語文案，
       送進去只會佔 token 又干擾判斷（模型該自己針對這一段給建議，
       不是把罐頭建議抄一遍）。
    """
    cats = config_loader.content_policy().get("categories", [])
    lines = []
    for c in cats:
        lines.append(
            f"- {c.get('id')} (severity: {c.get('severity')}): "
            f"{' '.join(str(c.get('what', '')).split())}"
        )
    return "\n".join(lines)


_SCREEN_SYSTEM = """You are a content-safety reader for a screenplay-to-video tool.

The writer is about to spend real money generating video from this screenplay.
Google's video models apply a Responsible AI filter to every generation
request. When it fires, the whole shot is rejected with no explanation of
which part caused it. Your job is to warn the writer BEFORE that happens.

Report passages that a strict automated safety filter would plausibly reject.

Categories to consider:
{policy}

How to judge:
- Judge in CONTEXT, not by keywords. The same sentence can pass in one
  passage and fail in another. A villain's line inside a scene that clearly
  condemns it is lower risk than the same line stated as fact.
- Dialogue matters more than narration: dialogue goes into the audio track
  of the generated video, narration only shapes the visual description.
- Dark themes are normal in drama. Do NOT flag a passage merely for being
  sad, tense, morally complex, or featuring an unpleasant character.
  Flag it when an automated filter would likely refuse to render it.
- Be honest about uncertainty. The filter is a black box. Use confidence
  "low" freely.

How to advise:
- Never tell the writer to delete the story beat. Give an EQUIVALENT way to
  write it that keeps the dramatic effect.
- Remember this tool can also mute a line and show it as a subtitle instead,
  so the script never has to change. Mention that option when the passage is
  dialogue and the visual itself is fine.

Report at most {max_findings} passages, highest risk first. If there are
more, say so in the summary."""


def screen_script(
    script_text: str,
    *,
    title: str = "",
) -> tuple[ScreenResult, list[str], dict[str, Any]]:
    """掃一份劇本，標出可能過不了內容審查的段落。

    為什麼要有這個函數？
    在這之前，內容問題只有在**生成當下**才會被發現 —— 實際使用時是拍到
    第 29 場、花了 $3 之後才撞上第一次。被擋本身不花錢（400 不計費），
    但每撞一次就要停下來決策，那是節奏成本。

    設計來源：產品需求「丟劇本之後就要審查機制」。

    這一關**不保證通過**。過濾器是黑盒、會變、對語境的判斷我們模擬不了。
       呼叫端的文案只能說「掃出 N 處高風險」。
    掃描結果不修改劇本原文 —— 改不改是使用者的事。

    Args:
        script_text: 劇本全文。行號以這份文字為準（從 1 起算）。
        title: 劇本標題，只用來讓模型知道作品脈絡。

    Returns:
        (結果, 警告清單, 用量) —— 警告是 code 層發現的問題
        （例如行號超出範圍），不是內容風險本身。
    """
    from google.genai import types

    client = _client()
    model = _model()
    cfg = config_loader.content_policy().get("screening", {})
    max_findings = int(cfg.get("max_findings", 12))

    lines = script_text.splitlines()
    numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines, start=1))

    system = _SCREEN_SYSTEM.format(policy=_policy_brief(), max_findings=max_findings)

    # 一定要串流：非串流在長劇本上會回
    #    `Server disconnected without sending a response` —— 看起來像網路
    #    問題，其實是生成時間超過連線容忍度。
    chunks = []
    stream = api_retry.governed(
        model,
        lambda: client.models.generate_content_stream(
            model=model,
            contents=(
                f"Screenplay title: {title or '(untitled)'}\n\n"
                f"Each line is prefixed with its line number.\n\n{numbered}"
            ),
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=ScreenResult,
                temperature=0.2,
                max_output_tokens=8_192,
            ),
        ),
        label="content_screener",
    )
    usage: dict[str, Any] = {}
    for chunk in stream:
        if chunk.text:
            chunks.append(chunk.text)
        if getattr(chunk, "usage_metadata", None):
            usage = _usage(model, chunk.usage_metadata)

    raw = "".join(chunks)
    if not raw.strip():
        raise RuntimeError("內容預審沒有回傳任何結果")

    result = ScreenResult.model_validate_json(raw)

    # code 層校正：行號要落在劇本範圍內，類別 id 要真的存在
    warnings: list[str] = []
    valid_ids = {
        str(c.get("id")) for c in config_loader.content_policy().get("categories", [])
    }
    kept: list[Finding] = []
    for f in result.findings:
        if f.category_id not in valid_ids:
            warnings.append(f"預審回報了不存在的類別「{f.category_id}」，已略過該筆")
            continue
        if not (1 <= f.source_line_start <= len(lines)):
            warnings.append(
                f"預審回報的行號 {f.source_line_start} 超出劇本範圍，已略過該筆"
            )
            continue
        f.source_line_end = max(f.source_line_start, min(f.source_line_end, len(lines)))
        kept.append(f)
    result.findings = kept

    return result, warnings, usage


def excerpt_for(script_text: str, finding: Finding, *, max_chars: int = 240) -> str:
    """從劇本原文切出某一筆發現的原文。

    為什麼要有這個函數？
    agent 只回行號（讓它抄原文等於重複付一次劇本的 token 費）。
    使用者要看到的是那幾行實際寫了什麼，所以由 code 切。

    截斷是為了 UI 卡片好讀，不是資料完整性 —— 使用者點開要能看全文，
       那由前端拿完整段落，不要在這裡把資料弄丟。
    """
    lines = script_text.splitlines()
    start = max(0, finding.source_line_start - 1)
    end = min(len(lines), finding.source_line_end)
    text = "\n".join(lines[start:end]).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


_REJECT_SYSTEM = """A video generation request was rejected by Google's
Responsible AI filter. The API does not say which part triggered it.

You are given the exact prompt that was sent. Identify the most likely cause
and explain it to the screenwriter in Chinese.

Categories:
{policy}

What we know from testing:
- Dialogue is the most common cause. In the one confirmed case, removing the
  dialogue line made the identical request pass immediately.
- The same line can pass in a shorter prompt — the filter reads the whole
  context, not keywords.

The tool can regenerate the shot with the dialogue muted and show that line
as a subtitle instead, leaving the script untouched. If the cause is the
dialogue, say so plainly so the writer knows that button will help."""


def explain_rejection(
    prompt: str,
    *,
    dialogue: str = "",
) -> tuple[RejectionAnalysis, dict[str, Any]]:
    """一鏡被安全審查擋下之後，指出兇手是哪一段。

    為什麼要有這個函數？
    生成失敗時要告訴使用者是哪一句台詞、什麼內容過不了，並給建議；
    API 只回一句籠統的 violates Responsible AI practices，對編劇零指引。

    不能用二分法去試 —— 每次「試通過」就真的生成一支影片（$1）。
       只能靠讀。輸入很短（一份提示詞），一次約 $0.001。
    這是推測不是判決。真正的確認來自無聲重拍成功與否，
       那個確認零額外成本（那 $1 本來就要花）。

    Args:
        prompt: 被擋的那份完整提示詞
        dialogue: 這一鏡的台詞原文（中文）。分開給是因為提示詞裡的台詞
            已經被包進英文句型，原文更容易讀出問題。

    Returns:
        (歸因, 用量)
    """
    from google.genai import types

    client = _client()
    model = _model()

    payload = f"The prompt that was rejected:\n\n{prompt}"
    if dialogue:
        payload += f"\n\nThe dialogue line in this shot (original language):\n{dialogue}"

    response = api_retry.governed(
        model,
        lambda: client.models.generate_content(
            model=model,
            contents=payload,
            config=types.GenerateContentConfig(
                system_instruction=_REJECT_SYSTEM.format(policy=_policy_brief()),
                response_mime_type="application/json",
                response_schema=RejectionAnalysis,
                temperature=0.2,
                max_output_tokens=2_048,
            ),
        ),
        label="rejection_analyst",
    )

    if not response.text:
        raise RuntimeError("被擋原因分析沒有回傳任何內容")

    usage = _usage(model, response.usage_metadata) if response.usage_metadata else {}
    return RejectionAnalysis.model_validate_json(response.text), usage
