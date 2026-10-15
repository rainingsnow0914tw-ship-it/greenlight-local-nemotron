"""製片方案層的資料契約 —— 這是整個產品的核心輸出。

設計理由：
    使用者要的是：用什麼工具、720p／1080p／2k／4k、預算多少、成功率多少，
    看完再選定方案。

    所以這一層不是給一個答案，是給**一組可以比較的方案**。
    使用者看到的應該像租車網站的方案比較表：

        Lite 720p    │ $9   │ 成功率 41% │ 預期重拍 14 次 │ 實際總價 $13
        Fast 720p    │ $18  │ 成功率 58% │ 預期重拍 8 次  │ 實際總價 $23
        Standard 1080│ $72  │ 成功率 71% │ 預期重拍 5 次  │ 實際總價 $86  超預算

    「預期總價」比「標價」重要得多 —— 便宜檔位的重拍次數多，
    算下來未必真的省。這個洞察是本產品相對於「自己去 Veo 網頁按一按」
    的核心價值，也是 ClickHouse 歷史資料唯一能回答的問題。

誠實原則：每個估算值都必須帶 `confidence`，說明這個數字是
   先驗推估還是實測統計。不准把先驗值當實測值呈現。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .shot import ShotList


class QualityTier(str, Enum):
    """畫質檔位。值必須與 pricing.yaml veo.tiers 的鍵一致。

    命名同時編碼了「模型檔位」與「解析度」兩個維度，因為 Veo 的定價
    是這兩者的組合，不是各自獨立的。例如 Standard 檔的 720p 跟 1080p
    同價（都是 $0.40/s），所以 Standard 檔沒有理由選 720p ——
    這種非直覺的定價結構，是必須在 UI 上主動告訴使用者的事。
    """

    # Greenlight Local：使用者自己的顯示卡跑 MiniMax H3（本機 ComfyUI）
    H3_LOCAL_4STEP = "h3_local_4step"
    # Omni Flash 主引擎檔位（Interactions API、固定 10 秒）
    OMNI_FLASH_720P = "omni_flash_720p"
    LITE_720P = "lite_720p"
    FAST_720P = "fast_720p"
    FAST_4K = "fast_4k"
    STANDARD_720P = "standard_720p"
    STANDARD_1080P = "standard_1080p"
    STANDARD_4K = "standard_4k"


class EstimateConfidence(str, Enum):
    """估算的可信度來源。

    這是誠實原則的實作 —— UI 必須根據這個欄位顯示不同的措辭：
        PRIOR_ONLY  → 「先驗估計（尚無實測資料）」
        BLENDED     → 「基於 N 次實測校準」
        MEASURED    → 「基於 N 次實測」
    """

    PRIOR_ONLY = "prior_only"
    BLENDED = "blended"
    MEASURED = "measured"


class BudgetVerdict(str, Enum):
    """這個方案相對於預算護欄的判定。閾值來自 pricing.yaml guardrails。"""

    OK = "ok"
    WARN = "warn"
    BLOCK = "block"


class CostBreakdown(BaseModel):
    """成本拆解。

    分項列出的理由：使用者看到總價 $23 沒有感覺，
    看到「影片 $18 + 重拍 $4 + 角色圖 $0.6 + 分析 $0.1」才知道錢花在哪、
    以及哪一項有壓縮空間。
    """

    video_base_usd: float = Field(description="不重試的情況下，生成所有鏡頭的成本")
    retry_expected_usd: float = Field(description="依成功率推估的重試成本")
    character_images_usd: float = Field(default=0.0)
    llm_analysis_usd: float = Field(default=0.0, description="劇本解析與規劃的 Gemini 成本")

    @property
    def total_usd(self) -> float:
        return round(
            self.video_base_usd
            + self.retry_expected_usd
            + self.character_images_usd
            + self.llm_analysis_usd,
            2,
        )


class ShotEstimate(BaseModel):
    """單一鏡頭的成功率預估。

    保留到鏡頭粒度的理由：整片平均成功率 58% 這個數字沒有行動價值，
    但「第 7 鏡只有 22%，因為畫面裡有五個人在跑」是可以行動的 ——
    使用者可以改拍法、拆鏡、或接受它會重拍很多次。

    UI 上要把最低分的幾個鏡頭挑出來當「風險鏡頭」提示。
    """

    shot_id: str
    success_rate: float = Field(ge=0.0, le=1.0)
    expected_attempts: float = Field(
        description="預期生成次數（含重試）。1.0 表示一次就過。"
    )
    confidence: EstimateConfidence
    sample_size: int = Field(default=0, description="ClickHouse 裡類似鏡頭的實測筆數")
    risk_factors: list[str] = Field(
        default_factory=list,
        description="拉低成功率的主因，人話呈現，例如「畫面內有文字」",
    )


class PlanVariant(BaseModel):
    """一個可選方案 —— 對應一個畫質檔位。"""

    tier: QualityTier
    tier_label: str = Field(description="給人看的檔位名稱，例如 'Fast · 720p'")

    total_seconds: int
    clip_count: int

    cost: CostBreakdown
    overall_success_rate: float = Field(
        ge=0.0, le=1.0, description="所有鏡頭成功率的加權平均（以鏡頭長度加權）"
    )
    confidence: EstimateConfidence
    shot_estimates: list[ShotEstimate] = Field(default_factory=list)

    budget_verdict: BudgetVerdict = BudgetVerdict.OK
    rationale: str = Field(
        default="",
        description="這個方案適合誰、什麼時候選它。人話一到兩句。",
    )

    @property
    def riskiest_shots(self) -> list[ShotEstimate]:
        """成功率最低的三個鏡頭。UI 的「風險鏡頭」區塊用這個。"""
        return sorted(self.shot_estimates, key=lambda e: e.success_rate)[:3]


class ProductionPlan(BaseModel):
    """完整的製片方案 —— 使用者在花任何錢之前看到的東西。

    這整份 plan 的產生成本趨近於零（只有 Gemini 文字推理，約 $0.16），
       但它決定了後面 $9 到 $108 的支出。這就是「規劃階段全開放、
       生成階段設閘門」這個產品設計的根據。
    """

    plan_id: str
    screenplay_title: str
    shot_list: ShotList

    variants: list[PlanVariant] = Field(default_factory=list)
    recommended_tier: QualityTier | None = Field(
        default=None,
        description=(
            "系統的建議檔位。不給「都可以你決定」—— 一定要有明確推薦，"
            "並在 recommendation_reason 說明理由。"
        ),
    )
    recommendation_reason: str = ""

    test_shot_required: bool = Field(
        default=False,
        description=(
            "總預估超過 pricing.yaml guardrails.test_shot_required_above_usd 時為真。"
            "為真時，UI 必須先跑試拍一鏡才允許批次生成。"
        ),
    )
    test_shot_candidate: str | None = Field(
        default=None,
        description=(
            "建議拿哪一鏡試拍。挑選邏輯見 services/cost_engine.py："
            "選有主角、有參考圖、風險中等的鏡頭 —— 最有代表性，"
            "而不是最簡單的那一鏡（那個過了不代表什麼）。"
        ),
    )

    def variant(self, tier: QualityTier) -> PlanVariant | None:
        """依檔位取出方案。找不到回 None，讓呼叫端決定怎麼處理。"""
        return next((v for v in self.variants if v.tier == tier), None)
