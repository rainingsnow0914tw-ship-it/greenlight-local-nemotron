"""專案狀態機 —— 讓一個橫跨數天的流程 hold 得住。

設計理由：
    「我們需要幫他建立一個小小的記憶庫？因為它是一個連續的任務嘛，要 hold 得住。」

    這個流程不是一次坐下來做完的：
        抽角色可能來回十輪、3 分鐘的片跑一次要半小時、
        中間關掉瀏覽器隔天再回來是常態。

    沒有持久狀態，這個產品根本不能用 —— 使用者每次回來都要從上傳劇本開始。

這裡定義的是「狀態的形狀」與「階段之間的規則」，
   實際存哪裡由 services/project_store.py 負責。分開的理由是
   儲存後端可能會換（Firestore / GCS / 本機 JSON），但狀態機的規則不該跟著換。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from .casting import CastingGate
from .plan import ProductionPlan, QualityTier
from .segment import Segment
from .screenplay import Screenplay
from .shot import ShotList


class Stage(str, Enum):
    """專案階段。順序就是流程順序。

    每個階段對應 PROJECT_GENESIS 第 7 節的一個步驟。
    階段之間的閘門規則見 ProjectState.can_advance()。
    """

    CREATED = "created"                # 剛建立，還沒上傳劇本
    SCRIPT_UPLOADED = "script_uploaded"
    PARSED = "parsed"                  # 劇本解析完，場景與角色抓出來了
    SHOTS_PLANNED = "shots_planned"    # 鏡頭表出來了
    LOOK_CHOSEN = "look_chosen"        # 🛑 風格 + 比例選定
    TIER_CHOSEN = "tier_chosen"        # 🛑 畫質方案選定
    CAST_LOCKED = "cast_locked"        # 🛑 所有角色選定
    PROMPTS_READY = "prompts_ready"    # 🛑 全部提示詞出完，可導出
    TEST_SHOT_PASSED = "test_shot_passed"  # 🛑 試拍一鏡通過
    GENERATING = "generating"          # 批次生成中
    ASSEMBLED = "assembled"            # 接片 + 字幕完成
    DELIVERED = "delivered"

    @property
    def order(self) -> int:
        return _STAGE_ORDER[self]


_STAGE_ORDER: dict[Stage, int] = {
    stage: index
    for index, stage in enumerate(
        [
            Stage.CREATED,
            Stage.SCRIPT_UPLOADED,
            Stage.PARSED,
            Stage.SHOTS_PLANNED,
            Stage.LOOK_CHOSEN,
            Stage.TIER_CHOSEN,
            Stage.CAST_LOCKED,
            Stage.PROMPTS_READY,
            Stage.TEST_SHOT_PASSED,
            Stage.GENERATING,
            Stage.ASSEMBLED,
            Stage.DELIVERED,
        ]
    )
}

# 每個階段給人看的名字與說明。UI 的進度列直接用這個。
STAGE_LABELS: dict[Stage, tuple[str, str]] = {
    Stage.CREATED: ("建立專案", "還沒上傳劇本"),
    Stage.SCRIPT_UPLOADED: ("讀本", "劇本已上傳，正在解析"),
    Stage.PARSED: ("讀本完成", "場景與角色已抓出"),
    Stage.SHOTS_PLANNED: ("分鏡", "鏡頭表已產生"),
    Stage.LOOK_CHOSEN: ("定調", "風格與畫面比例已選定"),
    Stage.TIER_CHOSEN: ("定案", "畫質與預算方案已選定"),
    Stage.CAST_LOCKED: ("選角", "所有角色已選定"),
    Stage.PROMPTS_READY: ("提示詞", "全部提示詞已產生，可導出"),
    Stage.TEST_SHOT_PASSED: ("試拍", "試拍一鏡已通過"),
    Stage.GENERATING: ("開拍", "批次生成中"),
    Stage.ASSEMBLED: ("成片", "接片與字幕完成"),
    Stage.DELIVERED: ("交付", "完成"),
}


class SpendLedger(BaseModel):
    """這個專案花了多少錢，分項記。

    使用者有權隨時知道「到目前為止花了多少」。
       這是 $100 硬預算下最基本的透明度 ——
       系統要能在任何時刻回答「我還剩多少可以用」。
    """

    llm_usd: float = 0.0
    images_usd: float = 0.0
    video_usd: float = 0.0

    @property
    def total_usd(self) -> float:
        return round(self.llm_usd + self.images_usd + self.video_usd, 3)


class ContentRisk(BaseModel):
    """劇本裡一處可能過不了內容審查的段落。

    這是**預警不是判決**。過濾器是黑盒，同一句話換個語境結果就不同
       。所以每一筆都帶 confidence，
       而且 UI 不可以說「已通過安全檢查」。
    """

    category_id: str = Field(description="content_policy.yaml 的類別 id")
    source_line_start: int
    source_line_end: int
    excerpt: str = Field(description="劇本原文摘錄（由 code 從原檔切，不是模型抄的）")
    is_dialogue: bool = Field(
        default=False,
        description=(
            "是台詞還是敘述。台詞會進音軌，而且真的被擋時有「無聲重拍」"
            "這條出路（字幕照原文）—— 所以這個欄位決定我們能給什麼建議。"
        ),
    )
    why: str = Field(default="", description="為什麼可能過不了（中文）")
    suggestion: str = Field(default="", description="建議怎麼調整（中文，保住戲劇效果）")
    confidence: str = Field(default="medium", description="high / medium / low")


class ContentScreening(BaseModel):
    """整份劇本的內容預審結果。

    為什麼要有這個：
        在這之前內容問題只有在**生成當下**才會發現 —— 實際使用時是拍到第 29 場、
        花了 $3 之後才撞上第一次。被擋本身不計費，但每撞一次就要停下來
        決策，那是節奏成本。整份劇本的地雷應該在上傳後一次看完。

    設計來源：產品需求「丟劇本之後就要審查機制」。

    掃出來只是意見，**不擋流程**：改不改由使用者決定，
       不改的話生成時會失敗，留著警告就好。
    """

    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str = ""
    risks: list[ContentRisk] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="code 層發現的問題（行號超範圍等），不是內容風險本身",
    )
    cost_usd: float = 0.0


class DecisionRecord(BaseModel):
    """一筆決策紀錄。

    為什麼要記：
        使用者隔三天回來，會忘記「當初為什麼選日式動畫」。
        更重要的是 —— 他做第二支片的時候，這些紀錄就是偏好的來源。

        這也是 demo 影片的好素材：系統能回答「你在這個專案做過哪些決定」。
    """

    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: Stage
    choice: str = Field(description="選了什麼，人話")
    rationale: str = Field(default="", description="系統當時給的建議理由")
    followed_recommendation: bool = Field(
        default=True, description="是否採納了系統建議。不採納的比例是重要訊號。"
    )


class ProjectState(BaseModel):
    """一個專案的完整狀態 —— 這就是「記憶庫」的本體。

    設計原則：**單一文件包含全部狀態**。
        不拆成多個表格再 join，因為這個資料的存取模式是
        「一次讀全部、改一小塊、寫回去」，不是關聯查詢。
        一份 JSON 文件最簡單、最不會出錯，也最容易在換儲存後端時搬家。

        真實大小估算：一份 90 頁劇本 + 60 個鏡頭 + 5 個角色 × 10 輪候選
        大約 300–500 KB。Firestore 單文件上限 1 MB，夠用但要注意 ——
        劇本原文如果很長，應該存 GCS 只在這裡放 URI。
    """

    project_id: str
    title: str = "未命名專案"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    stage: Stage = Stage.CREATED

    # 內建範例的標示（scripts/make_sample.py 寫入）：{"title": {zh,en,ja}, "note": {zh,en,ja}}
    #    這一例示範什麼、走哪條路 —— 三語分開寫，不用機器翻（三例要在 app 裡講清楚、三語乾淨）
    sample_info: dict | None = None

    # ---- 各階段的產物 ----
    script_text: str = Field(
        default="",
        description=(
            "劇本原文。章節偵測與原文對照都需要它，所以存在專案裡 —— "
            "不能依賴使用者的本機檔案路徑（上雲之後那個路徑不存在）。"
            "⚠️ 很長的劇本要注意 Firestore 單文件 1MB 上限，"
            "超過的話改存 GCS 這裡只放 URI。"
        ),
    )
    screenplay: Screenplay | None = None

    non_human_characters: list[str] = Field(
        default_factory=list,
        description=(
            "沒有實體身體的角色（AI、系統、擬人化的抽象存在）。"
            "🔴 這是選角關卡必要的資訊：對一個沒有身體的 AI 問"
            "「要更年輕還是更成熟」毫無意義，該問的是「更具象還是更抽象」。"
            "判斷來自劇本解析階段，Character schema 沒有這個欄位"
            "（它是選角才需要的資訊），所以存在專案層。"
            "舊版解析的專案這欄是空的 —— 那時 route 有算出來卻沒存，"
            "重新載入就丟了。舊專案要靠人工指定或重新解析。"
        ),
    )

    segments: list[Segment] = Field(
        default_factory=list,
        description=(
            "分段結果。空的代表還沒採用任何分段方案 —— "
            "第一次要分鏡時會自動用章節方案初始化（章節偵測是零成本的）。"
            "🔴 每一段有自己的 shot_list 與進度，所以必須持久化，"
            "不能每次從章節偵測重算。"
        ),
    )

    shot_list: ShotList | None = Field(
        default=None,
        description="整片的鏡頭表。分段之後改用 segments[].shot_list，這欄留給不分段的短片。",
    )
    plan: ProductionPlan | None = None
    chosen_tier: QualityTier | None = None
    casting: CastingGate | None = None

    # ---- 定調（整片一致，不逐段設定）----
    style_id: str = Field(default="photoreal", description="影片風格")
    format_id: str = Field(
        default="drama",
        description=(
            "敘事形式：用什麼方式講這個故事（劇情／紀錄片／訪談／導覽）。"
            "跟 style_id 是兩個維度 —— style 管「看起來像什麼」，"
            "format 管「用什麼方式講」。一部紀錄片可以是實拍寫實也可以是手繪繪本。"
            "預設 drama 讓既有專案行為不變。"
        ),
    )
    aspect_ratio: str = Field(default="16:9", description="畫面比例")
    prompt_language: str = Field(
        default="en",
        description=(
            "寫畫面描述用哪種語言。預設 en（產品決定統一英文）："
            "生成模型對英文服從度最穩，出錯率低。使用者仍可在定調時換。"
        ),
    )
    film_language: str = Field(default="zh", description="角色台詞與字幕的語言")
    # ---- 學語言模式（外語原聲＋雙語字幕＋讀音，台詞由 AI 導演照情境寫）----
    study_mode: bool = Field(
        default=False,
        description="學語言模式：台詞導演把劇本台詞改寫成原聲語言的口語、標讀音、寫雙語字幕與學習筆記（language/）。")
    learner_languages: list[str] = Field(
        default_factory=lambda: ["zh", "en"],
        description="學習者的母語＝字幕下行的語言（可多個，一個語言燒一版）；第一個也是學習筆記的解說語言。")
    name_glossary: str = Field(
        default="",
        description="名字表，一行一個「名字=原聲怎麼唸」（深息=しんそく、Lisa=リサ）。不在畫面上的角色（只被提到的 AI、寵物）"
                    "解析不到，要靠這張表；第三世 README 的名字翻譯表就是這個。實測：沒有這張表，深息被翻成ディープシープ。")

    prompts_exported_at: datetime | None = Field(
        default=None,
        description="使用者導出過提示詞的時間。導出過才能進下一階段。",
    )

    content_screening: ContentScreening | None = Field(
        default=None,
        description=(
            "內容安全預審結果。None = 還沒掃過。"
            "掃過不代表安全 —— 這是預警不是保證，見 ContentScreening 的說明。"
        ),
    )

    test_shot_id: str | None = None
    test_shot_approved: bool = False

    # ---- 全片成品（所有段落接起來）----
    film_output_uri: str | None = Field(
        default=None,
        description=(
            "整部片的 URI（所有段落成片接起來）。"
            "🔴 跟 segments[].output_uri 是兩個層級：段落成片是中間產物，"
            "這個才是使用者最後要的東西。分開存的理由是重接某一段之後"
            "全片會過期 —— 兩者不同步是**正常狀態**，不是錯誤，"
            "UI 要顯示「有段落更新過，全片需要重接」。"
        ),
    )
    film_output_seconds: float = Field(default=0.0, description="全片總長")
    film_built_at: str = Field(
        default="",
        description="全片是什麼時候合成的（ISO）。晚於任何段落的 output_built_at 才算新鮮。",
    )
    film_burned: dict[str, str] = Field(
        default_factory=dict,
        description="已燒錄字幕的全片檔名，key 是語言碼。理由同 Segment.burned。",
    )
    film_subtitles: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "全片字幕，key 是語言碼。跟段落字幕分開存 —— "
            "全片字幕的時間軸帶著各段的累計偏移，直接拿段落字幕去接會全部對不上。"
        ),
    )

    # ---- 帳本與決策歷史 ----
    spend: SpendLedger = Field(default_factory=SpendLedger)
    decisions: list[DecisionRecord] = Field(default_factory=list)

    # ---- 給人看的狀態 ----
    notes: list[str] = Field(
        default_factory=list,
        description="系統在流程中留下的提醒，例如「第 4 場建議改拍法」。",
    )

    # -----------------------------------------------------------------------
    # 閘門規則
    # -----------------------------------------------------------------------

    def blockers_for(self, target: Stage) -> list[str]:
        """要進到 target 階段，還缺什麼。空清單代表可以走。

        回傳的是**人話說明**，不是布林值。
           按鈕被鎖住時一定要說出原因 ——
           只顯示一個灰掉的按鈕而不解釋，使用者會以為系統壞了。
        """
        missing: list[str] = []

        if target == Stage.PARSED and not self.screenplay:
            missing.append("還沒上傳劇本")

        if target == Stage.SHOTS_PLANNED and not self.screenplay:
            missing.append("要先完成劇本解析")

        if target == Stage.LOOK_CHOSEN and not self.shot_list:
            missing.append("要先產生鏡頭表")

        if target == Stage.TIER_CHOSEN:
            if not self.plan:
                missing.append("要先產生製片方案")
            if self.stage.order < Stage.LOOK_CHOSEN.order:
                missing.append("要先選定風格與畫面比例")

        if target == Stage.CAST_LOCKED:
            if not self.chosen_tier:
                missing.append("要先選定畫質方案")
            if self.casting is None:
                missing.append("還沒開始選角")
            elif not self.casting.can_proceed:
                pending = "、".join(self.casting.pending_characters)
                missing.append(f"還有角色沒選定：{pending}")

        if target == Stage.PROMPTS_READY and self.stage.order < Stage.CAST_LOCKED.order:
            missing.append("要先完成選角")

        if target == Stage.TEST_SHOT_PASSED:
            # 關卡：看過提示詞才能開始花錢生成。
            if self.prompts_exported_at is None:
                missing.append("要先產生並確認提示詞")

        if target == Stage.GENERATING:
            if not self.test_shot_approved:
                missing.append("要先通過試拍一鏡")

        if target == Stage.ASSEMBLED and self.stage.order < Stage.GENERATING.order:
            missing.append("要先完成批次生成")

        return missing

    def can_advance(self, target: Stage) -> bool:
        return not self.blockers_for(target)

    def advance(self, target: Stage) -> None:
        """推進到下一階段。缺條件就拋例外。

        刻意不允許靜默失敗：如果呼叫端沒先檢查 blockers 就推進，
        那是 bug，該當場爆出來而不是讓專案卡在錯誤的狀態。
        """
        blockers = self.blockers_for(target)
        if blockers:
            raise ValueError(
                f"還不能進入「{STAGE_LABELS[target][0]}」：" + "；".join(blockers)
            )
        self.stage = target
        self.touch()

    def rewind_to(self, target: Stage) -> list[str]:
        """回退到前面的階段，回傳「會失效的東西」清單。

        為什麼允許回退：
            使用者選完角色才發現「這個故事應該用動畫風」是很正常的事。
            不讓他改，他只能整個重來。

        為什麼要回傳失效清單：
            回退是有代價的 —— 改了風格，已經選定的角色圖是寫實風的，
            跟新風格對不上。系統必須誠實說出「這樣做會讓什麼失效」，
            讓他自己決定值不值得。

        這個函式只回報後果、不自動清資料。實際要不要清由呼叫端
           在使用者確認後執行 —— 誤刪十輪選角的結果是不可接受的。
        """
        if target.order >= self.stage.order:
            return []

        invalidated: list[str] = []

        if target.order < Stage.CAST_LOCKED.order and self.casting:
            locked = sum(1 for s in self.casting.sessions if s.is_locked)
            if locked:
                invalidated.append(
                    f"{locked} 個已選定的角色（累計花費 ${self.casting.total_spent_usd:.2f}）"
                )

        if target.order < Stage.PROMPTS_READY.order and self.prompts_exported_at:
            invalidated.append("已產生的提示詞（需要重新產生）")

        if target.order < Stage.TEST_SHOT_PASSED.order and self.test_shot_approved:
            invalidated.append("試拍結果（需要重拍一鏡驗證）")

        if target.order < Stage.LOOK_CHOSEN.order and self.shot_list:
            invalidated.append("鏡頭表的風格與比例設定")

        return invalidated

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def record_decision(
        self,
        choice: str,
        rationale: str = "",
        followed_recommendation: bool = True,
    ) -> None:
        self.decisions.append(
            DecisionRecord(
                stage=self.stage,
                choice=choice,
                rationale=rationale,
                followed_recommendation=followed_recommendation,
            )
        )
        self.touch()

    # -----------------------------------------------------------------------
    # 給 UI 的摘要
    # -----------------------------------------------------------------------

    def progress_summary(self) -> dict:
        """進度摘要。使用者隔三天回來，看這個就知道自己在哪裡。"""
        label, description = STAGE_LABELS[self.stage]
        all_stages = sorted(_STAGE_ORDER, key=lambda s: s.order)
        next_stage = next(
            (s for s in all_stages if s.order == self.stage.order + 1), None
        )

        return {
            "project_id": self.project_id,
            "title": self.title,
            "stage": self.stage.value,
            "stage_label": label,
            "stage_description": description,
            "step": self.stage.order + 1,
            "total_steps": len(all_stages),
            "next_stage_label": STAGE_LABELS[next_stage][0] if next_stage else None,
            "blockers": self.blockers_for(next_stage) if next_stage else [],
            "spent_usd": self.spend.total_usd,
            # 定調的兩個維度要吐出來，否則前端每次重整都不知道使用者
            #    選過什麼（format_id 時發現 style_id 也漏了）。
            "style_id": self.style_id,
            "format_id": self.format_id,
            "aspect_ratio": self.aspect_ratio,
            "updated_at": self.updated_at.isoformat(),
            "rail_stage": self.rail_stage.value,
            "sample_info": self.sample_info,
        }

    @property
    def rail_stage(self) -> Stage:
        """左側進度燈該亮到哪 —— 只給顯示用，不寫回 stage。

        stage 只記全片一次性的關（解析、定調、選角），選角之後的分鏡、試拍、
        生成、接片都是每段各自做的，stage 不會再往前走。進度燈若照 stage 畫，
        做完整部片的專案也永遠停在「選角」（範例專案實證：
        使用者以為沒有分鏡）。所以這裡取 stage 與「走最遠那一段」的較後者。
        stage 本身不動：退回（rewind_to）靠它判斷要清掉什麼。
        """
        from .segment import SegmentStatus

        by_status = {
            SegmentStatus.DONE: Stage.ASSEMBLED,
            SegmentStatus.GENERATING: Stage.GENERATING,
            SegmentStatus.TEST_SHOT_PASSED: Stage.TEST_SHOT_PASSED,
            SegmentStatus.PROMPTS_READY: Stage.PROMPTS_READY,
            SegmentStatus.SHOTS_PLANNED: Stage.SHOTS_PLANNED,
        }
        best = self.stage
        candidates = [by_status[s.status] for s in self.segments if s.status in by_status]
        if self.film_output_uri:
            candidates.append(Stage.ASSEMBLED)
        for c in candidates:
            if c.order > best.order:
                best = c
        return best
