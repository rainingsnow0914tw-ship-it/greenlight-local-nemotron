"""成功率預估 —— 本產品的核心差異化功能。

設計理由：
    使用者要的是「成功率是多少」。這個問題聽起來簡單，但誠實地回答它需要兩件事：

      1. **冷啟動時有東西可講** —— 第一個使用者進來時 ClickHouse 是空的。
         這時候用 priors.yaml 的先驗值，並且**明說這是先驗**。

      2. **有資料時要用資料** —— 每跑一次真實生成就往 ClickHouse 寫一筆，
         下次遇到類似的鏡頭就查歷史。用 Bayesian 的混合方式：
         實測樣本越多，先驗權重越低。

    這正是 ClickHouse 在本專案的角色。它不是「拿來裝資料的地方」，
    是「讓這個功能能夠隨使用變準的必要條件」。沒有它，成功率永遠只是猜測。

誠實原則：每個回傳的 ShotEstimate 都帶 confidence 與 sample_size。
   UI 必須據此顯示「先驗估計」或「基於 N 次實測」。不准混為一談。
"""

from __future__ import annotations

from ..schemas.plan import EstimateConfidence, QualityTier, ShotEstimate
from ..schemas.shot import Shot
from . import config_loader, constraint_engine

# 風險因子轉成人話。給 UI 直接顯示用 —— 使用者看到 "content_flags.crowd"
# 是沒有意義的，看到「群眾場面（模型在多人畫面容易崩壞）」才知道要怎麼改。
# 風險說明的 key。實際文字（三語）在 config/priors.yaml 的 risk_labels ——
# 這裡只回 key，翻譯在 API 邊界才發生（services/i18n.py）。
#    之前這裡是一份寫死的中文 dict，於是日文介面下
#    整塊風險說明仍然是中文，而那正是產品最有價值的資訊。
#    資料層講 key、呈現層講語言，是這個專案處理三語的一貫做法。


def _prior_success_rate(
    shot: Shot,
    tier: QualityTier,
    style_id: str | None = None,
    aspect_ratio: str = "16:9",
) -> tuple[float, list[str]]:
    """用 priors.yaml 算這個鏡頭的先驗成功率。

    採加法而非乘法疊加修正項（priors.yaml 已註明理由）：
    多個修正項用乘法會塌到接近 0，跟實務直覺不符 ——
    一個「遠景 + 手持 + 三個人」的鏡頭確實難拍，但不至於是 0% 的難。

    回傳 (成功率, 風險因子人話清單)。
    """
    p = config_loader.priors()
    mods = p.get("modifiers", {})
    rate: float = float(p.get("base_success_rate", 0.35))
    risks: list[str] = []

    # --- 鏡別 ---
    rate += float(mods.get("shot_type", {}).get(shot.shot_type.value, 0.0))

    # --- 運鏡 ---
    rate += float(mods.get("camera_move", {}).get(shot.camera_move.value, 0.0))

    # --- 畫面比例 ---
    #
    # 比例不只是畫框選擇。直式的橫向空間只有橫式的三分之一 ——
    # 遠景與多人場面會被擠爆或裁掉，但特寫在直式反而更自然。
    ar_cfg = config_loader.aspect_ratio(aspect_ratio)
    rate += float(ar_cfg.get("difficulty_modifier", 0.0))

    ar_shot_penalty = float(
        ar_cfg.get("shot_type_penalties", {}).get(shot.shot_type.value, 0.0)
    )
    if ar_shot_penalty:
        rate += ar_shot_penalty
        risks.append(f"aspect_cramped|{ar_cfg.get('label', aspect_ratio)}")

    # --- 人數（用分桶，理由見 Shot.character_count_bucket）---
    #
    # 人數懲罰要乘上比例係數：直式塞三個人比橫式塞三個人難得多。
    bucket = shot.character_count_bucket
    char_penalty = float(mods.get("character_count", {}).get(bucket, 0.0))
    char_penalty *= float(ar_cfg.get("character_count_multiplier", 1.0))
    rate += char_penalty
    if bucket == "4+":
        risks.append("many_characters")

    # --- 台詞 ---
    if shot.dialogue:
        rate += float(mods.get("has_dialogue", 0.0))
        risks.append("has_dialogue")

    # --- 參考圖：本架構的主要優勢，沒有的話要明講 ---
    if shot.reference_image_uri:
        rate += float(mods.get("has_reference_image", 0.0))
    elif shot.characters_in_frame:
        # 只有在畫面裡有人的時候，缺參考圖才算風險。空鏡不需要。
        risks.append("no_reference_image")

    # --- 首尾幀接續 ---
    if shot.first_frame_from:
        rate += float(mods.get("has_first_frame", 0.0))

    # --- 畫質檔位 ---
    rate += float(mods.get("tier", {}).get(tier.value, 0.0))

    # --- 鏡頭長度（用基底長度，不含續拍）---
    rate += float(
        mods.get("duration_seconds", {}).get(str(shot.base_duration_seconds), 0.0)
    )

    # --- 續拍懲罰 ---
    # 每次 Extend 都是在已生成的畫面上往後長，誤差會累積：
    # 角色臉部緩慢變形、光線飄移、背景物件消失。接得越多越明顯。
    if shot.extensions > 0:
        penalty = float(mods.get("per_extension_penalty", -0.07))
        rate += penalty * shot.extensions
        risks.append(f"extension_drift|{shot.extensions}|{shot.total_seconds}")

    # --- 高風險內容標記 ---
    flag_mods = mods.get("content_flags", {})
    for flag in shot.content_flags:
        rate += float(flag_mods.get(flag.value, 0.0))
        if flag.value in config_loader.priors().get("risk_labels", {}):
            risks.append(flag.value)

    # --- 提示詞約束帶來的補救 ---
    #
    # 邊界設得好成功率就越高，重點是物理現象要合乎常理。
    #
    # 這是把那句話變成可計算的一步：constraint_engine 依鏡頭內容挑出該套用的
    # 物理約束（螢幕朝向、手部解剖、流體重力、不要依賴精確數量……），
    # 每條規則帶一個經驗性的提升值，疊加時遞減。
    #
    # 邊界：這裡只算「提示詞工程」的貢獻。鏡頭先天的難度（鏡別、人數、
    # 有沒有參考圖這個素材）已經在上面算過，不重複計算 ——
    # 見 constraints.yaml 檔頭的邊界說明。
    constraint_lift = constraint_engine.lift_for_shot(shot, style_id)
    if constraint_lift > 0:
        rate += constraint_lift

    # --- 風格難度 ---
    #
    # 最反直覺的一項：寫實風最難（-0.10），繪本風最寬容（+0.15）。
    # 恐怖谷 —— 觀眾對真人的破綻極度敏感，同樣的瑕疵放在動畫裡會被當成畫風。
    #
    # 這對預算有限的使用者是關鍵資訊：同一份劇本換個風格，
    # 成功率可能差 20 個百分點、省下一半的重拍錢。
    if style_id:
        preset = config_loader.style_preset(style_id)
        style_mod = float(preset.get("difficulty_modifier", 0.0))
        rate += style_mod
        if style_mod <= -0.05:
            risks.append(f"style_unforgiving|{style_id}")

    # --- 夾在合理範圍。0% 或 100% 都是對使用者說謊 ---
    clamp = p.get("clamp", {})
    lo, hi = float(clamp.get("min", 0.05)), float(clamp.get("max", 0.95))
    rate = max(lo, min(hi, rate))

    return rate, risks


def _blend_with_history(
    prior_rate: float, observed_rate: float | None, sample_size: int
) -> tuple[float, EstimateConfidence]:
    """把先驗與 ClickHouse 實測資料混合。

    公式：weight_prior = prior_strength / (prior_strength + n)

    prior_strength=20 的意思是「這組先驗大約等同 20 次觀測的說服力」。
    所以：
        n=0   → 100% 先驗
        n=20  → 先驗與實測各半
        n=100 → 先驗只剩 17%

    這比「超過 N 筆就完全信實測」的硬切換好，因為它避免了
    第 N 筆資料進來的瞬間，估計值跳一大段的突兀感。
    """
    p = config_loader.priors()
    blending = p.get("blending", {})
    strength = float(blending.get("prior_strength", 20))
    min_measured = int(blending.get("min_samples_to_report_as_measured", 5))

    if observed_rate is None or sample_size <= 0:
        return prior_rate, EstimateConfidence.PRIOR_ONLY

    w_prior = strength / (strength + sample_size)
    blended = w_prior * prior_rate + (1.0 - w_prior) * observed_rate

    confidence = (
        EstimateConfidence.MEASURED
        if sample_size >= min_measured * 4
        else EstimateConfidence.BLENDED
        if sample_size >= min_measured
        else EstimateConfidence.PRIOR_ONLY
    )
    return blended, confidence


def _expected_attempts(success_rate: float) -> tuple[float, float]:
    """算預期生成次數與「重試用盡仍失敗」的機率。

    模型：最多嘗試 max_retries + 1 次。每次重試時人會微調提示詞，
    所以成功率會小幅提升（retry_success_boost）—— 這是把「人為介入的價值」
    放進模型裡，而不是假設重試是完全獨立的隨機事件。

    期望次數 = Σ P(至少需要第 k 次嘗試)
             = 1 + (1-p₁) + (1-p₁)(1-p₂) + ...

    回傳 (期望嘗試次數, 最終仍失敗的機率)。

    第二個回傳值很重要：使用者有權知道「就算重拍兩次，還是有 18% 的鏡頭
       拿不到能用的畫面」。只講期望次數會讓人誤以為一定拍得到。
    """
    p = config_loader.priors()
    retry = p.get("retry_model", {})
    max_retries = int(retry.get("max_retries", 2))
    boost = float(retry.get("retry_success_boost", 0.08))
    clamp_max = float(p.get("clamp", {}).get("max", 0.95))

    expected = 0.0
    cumulative_failure = 1.0  # P(前 k-1 次全都失敗)

    for attempt in range(max_retries + 1):
        # 這一次嘗試會發生的機率 = 前面全失敗的機率
        expected += cumulative_failure
        rate_this_try = min(clamp_max, success_rate + boost * attempt)
        cumulative_failure *= 1.0 - rate_this_try

    return round(expected, 2), round(cumulative_failure, 4)


def predict_shot(
    shot: Shot,
    tier: QualityTier,
    style_id: str | None = None,
    aspect_ratio: str = "16:9",
    observed_rate: float | None = None,
    sample_size: int = 0,
) -> ShotEstimate:
    """預估單一鏡頭的成功率。

    `observed_rate` / `sample_size` 由 ledger.query_similar_shots() 提供
    （查 ClickHouse 裡特徵相近的歷史紀錄）。傳 None 就是純先驗模式 ——
    這讓整個預測層在沒有資料庫的情況下也能單獨測試，不需要起 ClickHouse。

    這個可測性是刻意設計的：本專案在實際儲值之前，
    整條規劃鏈路都必須能在本機零成本跑通。
    """
    prior_rate, risks = _prior_success_rate(shot, tier, style_id, aspect_ratio)
    rate, confidence = _blend_with_history(prior_rate, observed_rate, sample_size)
    attempts, _residual_failure = _expected_attempts(rate)

    return ShotEstimate(
        shot_id=shot.shot_id,
        success_rate=round(rate, 3),
        expected_attempts=attempts,
        confidence=confidence,
        sample_size=sample_size,
        risk_factors=risks,
    )


def residual_failure_probability(success_rate: float) -> float:
    """對外暴露「重試用盡仍失敗」的機率，給 UI 顯示誠實警語用。"""
    _, residual = _expected_attempts(success_rate)
    return residual
