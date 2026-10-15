"""成本估算引擎 —— 純數學，不呼叫任何 AI，執行成本為零。

設計理由：
    這個模組刻意不碰任何 API。理由有三個：

      1. **它是決定要不要花錢的那一層。** 一個「為了算出要花多少錢而必須先花錢」
         的估價器是荒謬的。

      2. **它必須在任何 API 額度儲值之前就跑得起來。**
         整條規劃鏈路要能在本機零成本驗證，這是開發順序的前提。

      3. **它必須是可測試的。** 成本算錯會直接害使用者超支，
         這種東西要能寫單元測試逐條驗，不能依賴「跑一次看看對不對」。

    所有價格來自 config/pricing.yaml，本檔案不認識任何具體數字。
"""

from __future__ import annotations

from ..schemas.plan import (
    BudgetVerdict,
    CostBreakdown,
    EstimateConfidence,
    PlanVariant,
    ProductionPlan,
    QualityTier,
    ShotEstimate,
)
from ..schemas.shot import ShotList
from . import rates
from . import config_loader, success_predictor

# 型別別名：查歷史資料的回呼。
# 傳入 (shot_id, tier)，回傳 (實測成功率, 樣本數)。查不到就回 (None, 0)。
#
# 用回呼而不是直接 import ledger 的理由：
#   讓 cost_engine 完全不依賴 ClickHouse。測試時傳一個回傳 (None, 0) 的假函式，
#   整個估價邏輯就能離線跑。這是「一功能一檔」的必然結果 ——
#   算錢的模組不該知道資料庫長什麼樣。
HistoryLookup = "Callable[[Shot, QualityTier], tuple[float | None, int]]"


def _no_history(_shot, _tier) -> tuple[float | None, int]:
    """預設的歷史查詢：什麼都查不到。純先驗模式用這個。"""
    return None, 0


# ---------------------------------------------------------------------------
# 分項成本
# ---------------------------------------------------------------------------

def video_cost(total_seconds: int, tier: QualityTier) -> float:
    """不重試的情況下，生成全部秒數要多少錢。

    這是使用者在別的地方（例如 Veo 官方定價頁）自己也算得出來的數字，
    也是他心裡的「標價」。真正的價值在下面的 retry_cost —— 那個他算不出來。
    """
    cfg = config_loader.veo_tier(tier.value)
    return round(total_seconds * float(cfg["per_second_usd"]), 2)


def retry_cost(estimates: list[ShotEstimate], shot_seconds: dict[str, int],
               tier: QualityTier) -> float:
    """依成功率推估的重試成本。

    每個鏡頭的預期生成次數是 expected_attempts（1.0 表示一次就過），
    所以額外成本就是 (expected_attempts - 1) × 該鏡頭的單次成本。

    這個數字是本產品的核心洞察：
       便宜檔位的標價低，但重試次數高，算下來未必真的省。
       只有把這一項算出來，「Lite vs Fast 到底哪個划算」才是可以回答的問題。
    """
    cfg = config_loader.veo_tier(tier.value)
    per_second = float(cfg["per_second_usd"])

    total = 0.0
    for est in estimates:
        seconds = shot_seconds.get(est.shot_id, 0)
        extra_attempts = max(0.0, est.expected_attempts - 1.0)
        total += extra_attempts * seconds * per_second
    return round(total, 2)


def character_image_cost(principal_count: int, supporting_count: int,
                         candidates_each: int = 3) -> float:
    """角色參考圖成本。

    產品設計：每個角色提供三張候選圖供選擇。
    主要角色選定後會再用 Standard 檔重生一張高品質正式版，配角就用候選圖。

    候選圖一律用 Imagen 4 Fast（$0.02）—— 選擇階段不需要極致畫質，
    能分辨長相差異就夠了。這是把錢花在刀口上。
    """
    pricing = config_loader.pricing()
    tiers = pricing.get("imagen", {}).get("tiers", {})
    fast = float(tiers.get("fast", {}).get("per_image_usd", 0.02))
    standard = float(tiers.get("standard", {}).get("per_image_usd", 0.04))

    total_characters = principal_count + supporting_count
    candidates = total_characters * candidates_each * fast
    finals = principal_count * standard  # 只有主角重生正式版
    return round(candidates + finals, 2)


def llm_analysis_cost(screenplay_tokens: int, shot_count: int) -> float:
    """劇本解析 + 分鏡規劃 + 提示詞撰寫的 Gemini 成本。

    這一項幾乎可以忽略（一份 90 頁劇本約 $0.16），但還是列出來，
    因為使用者有權看到完整的錢流向，而且「規劃階段幾乎免費」這件事
    本身就是產品設計的說明 —— 它解釋了為什麼規劃階段可以隨便試。

    估算方式刻意粗略：輸入是劇本 token 數，輸出估為每個鏡頭 400 token
    （鏡頭描述 + 提示詞）。精算沒有意義，數量級對就夠了。
    """
    pricing = config_loader.pricing()
    flash = rates.estimate_cfg("分鏡", pricing.get("gemini", {}).get("tiers", {}).get("flash", {}))   # 依設定頁：Nemotron 就用 Nemotron 的價
    in_rate = float(flash.get("input_per_1m_usd", 1.5))
    out_rate = float(flash.get("output_per_1m_usd", 9.0))

    # 劇本會被讀兩次：一次解析場景、一次規劃鏡頭
    input_tokens = screenplay_tokens * 2
    output_tokens = shot_count * 400

    cost = (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
    return round(cost, 4)


# ---------------------------------------------------------------------------
# 方案組裝
# ---------------------------------------------------------------------------

def _budget_verdict(total_usd: float) -> BudgetVerdict:
    """對照 pricing.yaml 的護欄，判定這個方案的預算風險等級。"""
    g = config_loader.guardrails()
    if total_usd >= float(g.get("block_threshold_usd", 40.0)):
        return BudgetVerdict.BLOCK
    if total_usd >= float(g.get("warn_threshold_usd", 15.0)):
        return BudgetVerdict.WARN
    return BudgetVerdict.OK


def _weighted_success_rate(estimates: list[ShotEstimate],
                           shot_seconds: dict[str, int]) -> float:
    """整片成功率 —— 用鏡頭長度加權，不是簡單平均。

    理由：一個 8 秒的難鏡頭失敗，損失比 4 秒的空鏡失敗大一倍。
    簡單平均會低估長鏡頭的權重，讓整體數字偏樂觀。
    """
    total_seconds = sum(shot_seconds.get(e.shot_id, 0) for e in estimates)
    if total_seconds == 0:
        return 0.0
    weighted = sum(
        e.success_rate * shot_seconds.get(e.shot_id, 0) for e in estimates
    )
    return round(weighted / total_seconds, 3)


def _aggregate_confidence(estimates: list[ShotEstimate]) -> EstimateConfidence:
    """整份方案的可信度取最保守的那一個。

    只要有一個鏡頭是純先驗，整份方案就不該宣稱「基於實測」——
    這是誠實原則的實作。寧可低報可信度，不可高報。
    """
    if any(e.confidence == EstimateConfidence.PRIOR_ONLY for e in estimates):
        return EstimateConfidence.PRIOR_ONLY
    if any(e.confidence == EstimateConfidence.BLENDED for e in estimates):
        return EstimateConfidence.BLENDED
    return EstimateConfidence.MEASURED


def _rationale_for(tier: QualityTier, variant_cost: float,
                   success_rate: float) -> str:
    """給這個方案寫一句人話說明。

    不用 AI 生成的理由：這是固定的產品文案，每次都該一樣。
    讓 LLM 每次重寫只會造成不一致，還要多花錢。
    """
    base = config_loader.veo_tier(tier.value).get("use_case", "")
    return f"{base} 預期總花費約 ${variant_cost}，整片一次過關率約 {round(success_rate * 100)}%。"


def estimate_variant(
    shot_list: ShotList,
    tier: QualityTier,
    principal_count: int = 0,
    supporting_count: int = 0,
    screenplay_tokens: int = 0,
    history_lookup=None,
) -> PlanVariant:
    """算出某一個畫質檔位的完整方案。

    這是使用者比較表上的一列。
    """
    lookup = history_lookup or _no_history
    # 用 total_seconds（含續拍），不是 base_duration_seconds ——
    # 計費是按輸出的總秒數算，一個 15 秒的鏡頭要付 15 秒的錢。
    shot_seconds = {s.shot_id: s.total_seconds for s in shot_list.shots}

    estimates: list[ShotEstimate] = []
    for shot in shot_list.shots:
        observed_rate, sample_size = lookup(shot, tier)
        estimates.append(
            success_predictor.predict_shot(
                shot,
                tier,
                style_id=shot_list.style_id,
                aspect_ratio=shot_list.aspect_ratio,
                observed_rate=observed_rate,
                sample_size=sample_size,
            )
        )

    breakdown = CostBreakdown(
        video_base_usd=video_cost(shot_list.total_seconds, tier),
        retry_expected_usd=retry_cost(estimates, shot_seconds, tier),
        character_images_usd=character_image_cost(principal_count, supporting_count),
        llm_analysis_usd=llm_analysis_cost(screenplay_tokens, shot_list.clip_count),
    )

    overall = _weighted_success_rate(estimates, shot_seconds)
    cfg = config_loader.veo_tier(tier.value)

    return PlanVariant(
        tier=tier,
        tier_label=str(cfg.get("label", tier.value)),
        total_seconds=shot_list.total_seconds,
        clip_count=shot_list.clip_count,
        cost=breakdown,
        overall_success_rate=overall,
        confidence=_aggregate_confidence(estimates),
        shot_estimates=estimates,
        budget_verdict=_budget_verdict(breakdown.total_usd),
        rationale=_rationale_for(tier, breakdown.total_usd, overall),
    )


def _pick_test_shot(variant: PlanVariant, shot_list: ShotList) -> str | None:
    """挑一個鏡頭來試拍。

    預設就挑第一鏡（理由見下方註釋）。下面的候選池邏輯只是退路：

      挑最簡單的那一鏡沒有意義 —— 空鏡當然會過，過了也不能說明什麼。
      挑最難的那一鏡也不對 —— 那一鏡失敗是預期內的，不代表整套參數有問題。

      退路裡挑**最有代表性**的：畫面裡要有角色（角色一致性是最需要
      驗證的東西），成功率落在有角色鏡頭的中位數附近。
      只按成功率取中位數會挑中沒有角色的空鏡（城市天際線之類），
      空鏡本來就好生，拍過了也證明不了角色會不會崩，
      所以先過濾出有角色的鏡頭，再取中位數；全都沒角色才退回全體。

    另外一個約束：**只從「不需要接幀」的鏡頭裡挑**。
        routes/video.py 的 _ensure_clips 規定「需要接首尾幀的鏡頭不准併入批次」
        （因為試拍是硬切生的，條件跟批次不同）。如果試拍挑到需要接幀的鏡頭，
        那一鏡註定要重生一次 —— 兩條規則各自都對，湊在一起就保證浪費。
        不需要接幀 = 它是某一場的第一鏡 = 前面沒有東西可接，
        所以它生出來永遠併得回批次，零浪費。
    """
    if not variant.shot_estimates:
        return None

    shots_by_id = {s.shot_id: s for s in shot_list.shots}

    def has_character(est) -> bool:
        shot = shots_by_id.get(est.shot_id)
        return bool(shot and shot.characters_in_frame)

    def is_reusable(est) -> bool:
        """生出來之後能不能直接當成批次的一鏡（不必重生）。"""
        shot = shots_by_id.get(est.shot_id)
        return bool(shot and not shot.first_frame_from)

    # 預設就挑第一鏡：
    #
    #     若挑「難度中位數」，使用者看到的會是「按了試拍，它跑去拍第二場的鏡頭」。
    #     挑第一鏡的理由全部成立：
    #       · 第一鏡按定義沒有前一鏡可接 → 不需要接幀 → 生出來零浪費
    #       · 第一鏡是觀眾第一眼看到的 → 風格對不對，看它最準
    #       · **可預測** → 使用者不必猜系統的挑選邏輯
    #
    #     「中位數最有代表性」聽起來聰明，但代價是行為不可預測，
    #     而使用者對「試拍」的直覺就是「拍第一個」。
    #     可預測性在這裡比理論上的資訊量值錢。
    #
    #     想試別鏡的人仍然可以指定 shot_id（routes 支援）。
    #
    # 下面兩個 helper 保留：候選池邏輯在「第一鏡資料異常」時還是退路。
    first = shot_list.shots[0] if shot_list.shots else None
    if first is not None:
        return first.shot_id

    reusable = [e for e in variant.shot_estimates if is_reusable(e)]
    for pool in (
        [e for e in reusable if has_character(e)],
        reusable,
        [e for e in variant.shot_estimates if has_character(e)],
        variant.shot_estimates,
    ):
        if pool:
            ranked = sorted(pool, key=lambda e: e.success_rate)
            return ranked[len(ranked) // 2].shot_id

    return None


def _recommend(variants: list[PlanVariant]) -> tuple[QualityTier | None, str]:
    """選出建議檔位。

    規則（依序）：
      1. 排除 BLOCK（超過護欄上限的，不推薦）
      2. 在 OK 等級裡選成功率最高的 —— 錢在安全範圍內，就買最好的品質
      3. 全部都是 WARN 的話，選預期總價最低的 —— 錢緊的時候先顧預算

    一定要給明確推薦，不給「都可以你決定」。
       使用者來這裡就是因為他不知道該選什麼。
    """
    if not variants:
        return None, ""

    affordable = [v for v in variants if v.budget_verdict != BudgetVerdict.BLOCK]
    if not affordable:
        cheapest = min(variants, key=lambda v: v.cost.total_usd)
        # 回 key + 參數，不回中文字串。
        #    翻譯在 API 邊界才發生，見 services/risk_text.py 的說明。
        return (cheapest.tier, f"all_over_budget|{cheapest.tier_label}")

    safe = [v for v in affordable if v.budget_verdict == BudgetVerdict.OK]
    pool = safe or affordable

    best = max(pool, key=lambda v: v.overall_success_rate)
    if safe:
        reason = (
            f"best_within_budget|{best.tier_label}|{best.cost.total_usd}"
            f"|{round(best.overall_success_rate * 100)}"
        )
    else:
        best = min(pool, key=lambda v: v.cost.total_usd)
        reason = f"all_in_warning_zone|{best.tier_label}|{best.cost.total_usd}"
    return best.tier, reason


def build_plan(
    plan_id: str,
    shot_list: ShotList,
    principal_count: int = 0,
    supporting_count: int = 0,
    screenplay_tokens: int = 0,
    history_lookup=None,
    tiers: list[QualityTier] | None = None,
) -> ProductionPlan:
    """產出完整的製片方案 —— 使用者在花任何錢之前看到的那一份。

    預設跑全部六個畫質檔位，讓使用者攤開來比。
    這整個函式的執行成本是零（不呼叫任何 API），
    所以可以隨便重算 —— 使用者改了鏡頭表就即時重算，不必擔心費用。
    """
    tier_list = tiers or list(QualityTier)

    variants = [
        estimate_variant(
            shot_list,
            tier,
            principal_count=principal_count,
            supporting_count=supporting_count,
            screenplay_tokens=screenplay_tokens,
            history_lookup=history_lookup,
        )
        for tier in tier_list
    ]

    recommended, reason = _recommend(variants)

    # 是否強制先試拍：用推薦檔位的總價來判斷
    g = config_loader.guardrails()
    threshold = float(g.get("test_shot_required_above_usd", 5.0))
    rec_variant = next((v for v in variants if v.tier == recommended), None)
    needs_test = bool(rec_variant and rec_variant.cost.total_usd > threshold)

    return ProductionPlan(
        plan_id=plan_id,
        screenplay_title=shot_list.screenplay_title,
        shot_list=shot_list,
        variants=variants,
        recommended_tier=recommended,
        recommendation_reason=reason,
        test_shot_required=needs_test,
        test_shot_candidate=(
            _pick_test_shot(rec_variant, shot_list) if rec_variant else None
        ),
    )
