# -*- coding: utf-8 -*-
"""內容安全預審的 API。

產品定調：只負責掃、給意見。改不改由使用者決定 ——
不改的話生成影片時可能被平台安全過濾器擋下，警告留著就好。

所以這一關不是閘門 —— 它不擋任何動作，只提供資訊。
`can_advance()` 沒有因此多一條 blocker，這是刻意的。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..services import rates
from ..services import config_loader, i18n, project_store

logger = logging.getLogger(__name__)

from ..services import demo_mode  # noqa: E402

router = APIRouter()


def _load(project_id: str):
    state = project_store.get_store().load(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"找不到專案 {project_id}")
    return state


def _category(category_id: str) -> dict:
    cats = config_loader.content_policy().get("categories", [])
    return next((c for c in cats if str(c.get("id")) == category_id), {})


def _serialize(state) -> dict:
    """把預審結果組成前端要的形狀（含三語）。

    類別標籤與罐頭建議走 i18n.pair，逐筆的 why/suggestion 不走 ——
       那兩個是模型針對這一段寫的中文，沒有對應的英日版本。
       混在一起會讓前端以為每個欄位都有三語，切到日文時拿到 undefined。
       所以分成 `category`（三語）與 `note`（單語）兩區，型別上就講清楚。
    """
    screening = state.content_screening
    if screening is None:
        return {
            "scanned": False,
            "risks": [],
            "summary": "",
            # 掃描要花錢，UI 要先講清楚才有得按（花錢前先報價）
            "estimated_usd": _estimate(state),
        }

    risks = []
    for r in screening.risks:
        cat = _category(r.category_id)
        risks.append(
            {
                "category_id": r.category_id,
                "category": i18n.pair(cat, "label") if cat else {},
                "severity": cat.get("severity", "medium"),
                "general_advice": i18n.pair(cat, "advice") if cat else {},
                "line_start": r.source_line_start,
                "line_end": r.source_line_end,
                "excerpt": r.excerpt,
                "is_dialogue": r.is_dialogue,
                "note": {"why": r.why, "suggestion": r.suggestion},
                "confidence": r.confidence,
                # 台詞類的風險有一條不用改劇本的出路 —— 前端據此顯示那句提示
                "mute_retry_applicable": r.is_dialogue,
            }
        )

    return {
        "scanned": True,
        "scanned_at": screening.scanned_at.isoformat(),
        "summary": screening.summary,
        "risks": risks,
        "warnings": screening.warnings,
        "cost_usd": round(screening.cost_usd, 4),
        "high_count": sum(1 for r in risks if r["severity"] == "high"),
        # 不是「已通過安全檢查」。過濾器是黑盒、同一句話換語境結果就不同，
        #    承諾失效比沒有承諾更傷 —— 這句話直接給前端用，不要另外編。
        "disclaimer": {
            "zh": "這是預警不是保證。內容審查會看整段語境，掃過仍可能被擋；被擋時系統會告訴你是哪一段。",
            "en": "This is an early warning, not a guarantee. The safety filter reads full context, so a scanned script can still be rejected — we will tell you which passage when it happens.",
            "ja": "これは事前警告であり保証ではありません。安全フィルターは文脈全体を見るため、スキャン済みでも拒否されることがあります。その際はどの箇所かをお知らせします。",
        },
    }


def _estimate(state) -> float:
    """掃一次大概多少錢。

    按劇本字數粗估 token（中文約 1 字 1 token，英文約 4 字元 1 token，
    這裡取保守的中文估法）。輸出很短，估 2000 token。

    這是給使用者看的預告不是計費依據 —— 實際金額以 API 回報的
       usage 為準。寧可估高一點：預告 $0.2 實付 $0.1 是驚喜，
       反過來是背叛。
    """
    text = state.script_text or ""
    if not text:
        return 0.0
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("script_screening", "pro")
    cfg = rates.estimate_cfg("內容安全預審", pricing.get("tiers", {}).get(tier_key, {}))   # 依設定頁：Nemotron 就用 Nemotron 的價
    in_rate = float(cfg.get("input_per_1m_usd", 1.25))
    out_rate = float(cfg.get("output_per_1m_usd", 10.0))
    return round((len(text) * in_rate + 2_000 * out_rate) / 1_000_000, 3)


@router.get("/{project_id}/screening")
def get_screening(project_id: str) -> dict:
    """讀預審結果。$0。"""
    return _serialize(_load(project_id))


@router.post("/{project_id}/screening")
def run_screening(project_id: str) -> dict:
    """掃一次劇本。會花錢（依劇本長度，通常 $0.05–0.2）。

    為什麼是 POST 而且不自動跑：
        解析完自動掃會讓使用者在還沒決定要不要做這個專案之前就先付費。
        跟解析、分鏡一樣，按下去才扣款是這個產品的規矩。
    """
    from ..agents import content_screener

    state = _load(project_id)
    if not state.script_text:
        raise HTTPException(status_code=400, detail="這個專案還沒有劇本內容")

    try:
        result, warnings, usage = content_screener.screen_script(
            state.script_text,
            title=state.title,
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("內容預審失敗")
        raise HTTPException(status_code=502, detail=f"內容預審失敗：{exc}") from exc

    from ..schemas.project import ContentRisk, ContentScreening

    risks = [
        ContentRisk(
            category_id=f.category_id,
            source_line_start=f.source_line_start,
            source_line_end=f.source_line_end,
            # 原文由 code 從劇本切（agent 只回行號，不讓它抄 40K token 回來）
            excerpt=content_screener.excerpt_for(state.script_text, f),
            is_dialogue=f.is_dialogue,
            why=f.why,
            suggestion=f.suggestion,
            confidence=f.confidence,
        )
        for f in result.findings
    ]
    cost = float(usage.get("cost_usd", 0.0))

    # 交易式寫入（避免併發 lost update）：
    #    貴的工作在交易外面做完，交易裡只做「重讀 → 併上去 → 存」。
    def _apply(fresh):
        fresh.content_screening = ContentScreening(
            summary=result.summary,
            risks=risks,
            warnings=warnings,
            cost_usd=cost,
        )
        fresh.spend.llm_usd += cost
        fresh.record_decision(
            choice=f"內容預審：掃出 {len(risks)} 處風險",
            rationale=f"${cost}",
        )
        return fresh

    state = project_store.get_store().mutate(project_id, _apply)
    return _serialize(state)
