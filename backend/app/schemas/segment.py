"""分段 —— 讓一份 45 分鐘的劇本可以一段一段拍。

設計理由：
    「裡面有幾個章節嘛，我們可以按章節分也可以啊，不用整部拍完嘛。
      沒有的話就幫它分段，建議它共五段或共十二段，就是有個選擇權，
      就是給它看全貌嘛。也可以選 1、2、3 一段來先做，看看效果也可以。
      之前已經幫他生成過第一章節的影片，第二章節他也可以接著生，不影響。」

    這解決一個實測出來的真實問題：
    測試用的真實劇本解析出來是 **45.2 分鐘**，
    最便宜的 Lite 檔全片也要 $135 —— 超過整個專案的 $100 預算。

    但這份劇本本身有 8 個章節。按章節拍第一章只要約 $15，
    看完效果滿意再拍第二章。這才是真實創作者負擔得起的方式。

═══════════════════════════════════════════════════════════════════════════
什麼跨段共用，什麼每段獨立
═══════════════════════════════════════════════════════════════════════════

    **跨段共用（在專案層決定一次）**
        劇本解析結果、風格、畫面比例、畫質檔位、**角色選定**

        角色為什麼一定要共用：第 1 章選好的深息長相，第 2 章必須一模一樣。
        每段各選一次的話，同一個角色在不同章節長得不一樣 ——
        那是這個產品最不能犯的錯。

    **每段獨立（各自進度）**
        分鏡表、提示詞、試拍、批次生成、接片、花費

    這個分層讓「做完第一章、隔兩週再做第二章」變得自然：
    回來的時候角色、風格都還在，只要接著分鏡就好。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .shot import ShotList


class SegmentSource(str, Enum):
    """這個分段方案是怎麼來的。

    影響 UI 上的措辭 —— 「劇本自帶的 8 個章節」比「系統分的 8 段」
    可信得多，使用者的信任度不同，要如實標示。
    """

    CHAPTER = "chapter"        # 劇本自己就有章節標記
    SUGGESTED = "suggested"    # 系統依敘事結構建議的分段
    MANUAL = "manual"          # 使用者自己調的


class SegmentStatus(str, Enum):
    """單一段落的進度。

    刻意跟 ProjectState 的 Stage 分開 ——
    專案層的階段（劇本解析、選風格、選角）是全片一次性的，
    段落層的階段是每段各自重複的。混在一起會變成
    「這個專案到底在第幾步」這種答不出來的問題。
    """

    NOT_STARTED = "not_started"
    SHOTS_PLANNED = "shots_planned"
    PROMPTS_READY = "prompts_ready"
    TEST_SHOT_PASSED = "test_shot_passed"
    GENERATING = "generating"
    DONE = "done"


_STATUS_LABELS: dict[SegmentStatus, str] = {
    SegmentStatus.NOT_STARTED: "尚未開始",
    SegmentStatus.SHOTS_PLANNED: "已分鏡",
    SegmentStatus.PROMPTS_READY: "提示詞已產生",
    SegmentStatus.TEST_SHOT_PASSED: "試拍已通過",
    SegmentStatus.GENERATING: "生成中",
    SegmentStatus.DONE: "已完成",
}


class TestShotRecord(BaseModel):
    """試拍一鏡的結果（第 7 關）。

    這一關的意義是：**在花 $9–$72 跑整段之前，先用 $0.40 看一眼。**
    所以記下來的東西要足夠讓人做「繼續還是重來」的判斷，
    不是只記一個成功旗標。
    """

    shot_id: str
    uri: str = Field(description="media_store URI，前端透過 API 播放")
    seconds: float = Field(description="🔴 實際輸出秒數，從 mp4 解析，不是請求值")
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    tier: str = ""
    cost_usd: float = 0.0
    generated_at: str = ""
    audit: dict = Field(
        default_factory=dict,
        description="看片檢核結果，同 ClipRecord.audit。",
    )
    verify: dict = Field(default_factory=dict, description="本機查證，同 ClipRecord.verify。")
    diagnosis: dict = Field(default_factory=dict, description="查證診斷，同 ClipRecord.diagnosis。")
    trim_head: float = Field(default=0.0, description="成片剪頭秒數，同 ClipRecord.trim_head。")
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "生成時的異常。例如「要求 8 秒實際輸出 6 秒」——"
            "這種偏差不會讓 API 報錯，但會讓整段接片的時間軸對不上。"
        ),
    )

    attempts: int = Field(
        default=1, description="同一鏡試拍過幾次。重新試拍就 +1。"
    )
    pending_ledger: "PendingLedgerFacts | None" = Field(
        default=None,
        description=(
            "試拍**也是一次真實生成、也有人裁決**，"
            "在這之前它完全沒進 ledger —— 而它是整個流程裡第一個花錢的動作，"
            "漏掉等於統計從第二筆才開始。"
            "\n\n"
            "欄位語意與寫入時機跟 ClipRecord.pending_ledger 完全一致："
            "生成時把事實扣在這裡，人按通過（accepted）或重新試拍"
            "（上一支落成 rejected）時才落地。"
            "\n\n"
            "⚠️ 有這個欄位之後 TestShotRecord 就跟 ClipRecord 介面相容，"
            "`_flush_ledger()` 兩種都吃得下 —— 不要為試拍另寫一套落地邏輯，"
            "兩套寫入路徑遲早會有一邊漏改。"
        ),
    )


class ClipStatus(str, Enum):
    """一個鏡頭在批次生成裡的狀態。"""

    NOT_STARTED = "not_started"
    AWAITING_APPROVAL = "awaiting_approval"   # 生完了，等人看過
    APPROVED = "approved"                     # 人說 OK，不會再動它
    FAILED = "failed"                         # 重試過仍然失敗


class PendingLedgerFacts(BaseModel):
    """一次生成當下的事實，等人裁決之後才寫進 ClickHouse。

    為什麼不是「生成完就直接寫 ledger」：
        ledger 的一筆代表**一次有結論的生成**。生成剛結束時還沒有結論 ——
        人還沒看過。先寫一筆 outcome='pending' 再回頭改是錯的做法：
        兩個物化檢視都是 insert 觸發的，pending 那筆會先記一次 attempts
        （accepted=0），裁決之後再記一次 —— 同一次生成被算成兩次，
        通過率直接腰斬。而 ClickHouse 的 UPDATE 是非同步 mutation，
        逐筆改比重寫還貴。

        所以事實先扣在這裡。專案 JSON 是原子寫入的，關掉瀏覽器也不會掉；
        人一按通過／重拍，才落一筆完整紀錄。這仍然是「即時」——
        即時的是**結論產生的那一刻**，不是 API 回應的那一刻。

        代價：生成了但從沒裁決的鏡頭不會進統計。那是對的 ——
        沒有結論的生成本來就不該算進通過率。

    這裡只存**沒有別的地方存、事後也算不回來**的東西。
    鏡別、運鏡、人數那些從 Shot 推導，不重複存一份 ——
    存兩份就會有兩份不同步的風險。
    """

    attempt: int = Field(description="這是第幾次生成。ledger 只用 attempt=1 算一次過關率。")
    predicted_success_rate: float = Field(
        default=0.0,
        description=(
            "🔴 生成當下系統預測的成功率。**必須在生成時抓，事後重算沒有意義** ——"
            "重算會用到後來才寫進 ledger 的資料，那是拿答案回去改考卷。"
        ),
    )
    applied_constraints: list[str] = Field(
        default_factory=list,
        description="套用了哪些約束規則的 id。這一欄讓「邊界設得好成功率越高」變成可驗證的主張。",
    )
    model_id: str = ""
    latency_seconds: int = 0
    cost_usd: float = Field(
        default=0.0,
        description="🔴 **這一次**的花費，不是 ClipRecord.cost_usd 那個累計值。",
    )
    has_first_frame: bool = Field(
        default=False,
        description="實際上有沒有接到上一鏡的末幀。分鏡表標了「接」但抽幀失敗時是 False。",
    )
    prompt_excerpt: str = ""


class TakeRecord(BaseModel):
    """同一鏡的某一次生成成品（一個 take）。

    每一次生成都是使用者花了錢的資產，**永不覆蓋、永不刪除**——
    重拍不保證比上一次好，導演要有把舊 take 拿回來用的權利。
    檔名帶次數（s014_02_a2.mp4），所以每個 take 是獨立檔案。
    """

    attempt: int = 0
    uri: str = ""
    seconds: float = 0.0
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    tier: str = ""
    generated_at: str = ""


class ClipRecord(BaseModel):
    """批次生成裡的一個鏡頭。

    產品決定**每一鏡都停下來確認**：
        「做一個檢查過沒問題，再生成下一個…免得浪費錢。」

    所以這裡記的不只是「生出來了」，還要記**它花了多少、試了幾次、
    現在等誰**。中途關掉瀏覽器再回來，要能從同一個地方接下去 ——
    重跑已經通過的鏡頭是這個流程裡最貴的錯誤。
    """

    shot_id: str
    status: ClipStatus = ClipStatus.NOT_STARTED
    uri: str = ""
    seconds: float = 0.0
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    tier: str = ""
    cost_usd: float = Field(
        default=0.0,
        description="這一鏡的**累計**花費，含重拍。不是最後一次的錢。",
    )
    attempts: int = Field(
        default=0,
        description="生成了幾次。>1 代表重拍過 —— 這個數字要寫進 ledger 校準成功率。",
    )
    generated_at: str = ""
    takes: list[TakeRecord] = Field(
        default_factory=list,
        description=(
            "這一鏡的歷次生成成品，每個 take 獨立檔案、永不刪。"
            "uri 等頂層欄位永遠反映「目前採用」的 take；切換走 take 路由。"
        ),
    )
    audit: dict = Field(
        default_factory=dict,
        description=(
            "看片檢核的結果（frame_auditor）。"
            "這是雙向迴路的回向紀錄（要留 log、可翻查）：原始觀察 + 對照發現 + 成本，"
            "永久存在專案裡。發現同時會進 warnings（給 UI）與"
            "重拍時的 rejection_reason（給 ClickHouse 學習迴路）。"
        ),
    )
    verify: dict = Field(
        default_factory=dict,
        description=(
            "本機查證（verify/checks.py）：程式量的，不是模型說好。台詞聽寫比對、開頭亂語、有沒有講完、"
            "講話時段的臉部截圖（看對嘴）、20 格。本機版沒有雲端看片，這就是它的眼睛和耳朵。"),
    )
    diagnosis: dict = Field(
        default_factory=dict,
        description=(
            "查證沒過時的 agent 診斷（verify/doctor.py）：對到症狀庫哪一條、引用的證據、建議的修法。"
            "沒引用症狀庫或查證數字的診斷不收。重拍時修法會進提示詞，退件原因會進生成紀錄。"),
    )
    trim_head: float = Field(
        default=0.0, description="成片時從開頭剪掉幾秒（開頭亂語）。查證量出來、使用者可改；不重生。")
    warnings: list[str] = Field(default_factory=list)
    error: str = Field(
        default="",
        description="最後一次失敗的原因。狀態是 failed 時 UI 要顯示這個，不能只給紅字。",
    )
    rejection: dict = Field(
        default_factory=dict,
        description=(
            "被內容安全審查擋下時的歸因（失敗時要告訴使用者是哪一句台詞、什麼內容過不了）。"
            "API 只回一句籠統的 violates Responsible AI practices、"
            "**不會說是哪一段**，所以這是我們自己讀出來的推測："
            "{likely_cause, excerpt, category_id, explanation, suggestion}。"
            "⚠️ 是推測不是判決 —— 真正的確認來自無聲重拍成功與否，"
            "而那個確認零額外成本（那筆錢本來就要花）。"
        ),
    )
    pending_ledger: PendingLedgerFacts | None = Field(
        default=None,
        description=(
            "最近一次生成、還沒被裁決的事實。人按下通過或重拍時寫進 ClickHouse 並清空。"
            "不是 None 就代表「有一次生成還懸在那裡沒有結論」。"
        ),
    )


class Segment(BaseModel):
    """一個段落 —— 可以獨立拍完的一塊。"""

    segment_id: str
    order: int = Field(description="第幾段，從 1 開始")
    title: str = Field(description="段落標題。章節分段就用章節名。")
    format_id: str | None = Field(
        default=None,
        description=(
            "這一段的敘事形式，覆寫專案層的設定。None = 跟著專案走。 "
            "為什麼是段落層而不是專案層："
            "一個劇本裡可能好幾種類型混合 —— 同一部作品第一章用旁白鋪陳、"
            "第二章讓角色自己演，是很正常的寫法。 "
            "⚠️ 這跟 style_id 的分層剛好相反，兩者不能一起處理："
            "**風格必須全片一致**（角色長相是照風格生的，換了就要重畫），"
            "**敘事形式可以逐段不同**（它只影響分鏡策略，重新分鏡就生效）。"
        ),
    )

    scene_numbers: list[int] = Field(
        default_factory=list, description="這一段包含劇本的哪幾場"
    )
    estimated_seconds: float = 0.0

    synopsis: str = Field(
        default="",
        description="這一段講什麼，一到兩句。讓使用者能判斷「先拍這段划不划算」。",
    )
    is_self_contained: bool = Field(
        default=True,
        description=(
            "這一段能不能獨立成立（看得懂、有起承轉合）。"
            "🔴 這是「先拍一段看效果」能不能成立的關鍵 —— "
            "如果一段只是半截劇情，單獨拍出來沒有意義。"
        ),
    )

    # ---- 每段獨立的進度 ----
    #
    # `status` 刻意**不是儲存欄位**，是從事實推導的 property（見下方）。
    #    之前它是儲存欄位，而 6 個狀態裡**只有 SHOTS_PLANNED
    #    被賦值過一次**（routes/projects.py）。PROMPTS_READY / TEST_SHOT_PASSED
    #    / GENERATING / DONE 全專案零賦值。
    #
    #    結果：第 1 段實際上做完了（12 鏡全通過、成片 84 秒、三語字幕齊全），
    #    UI 重載後永遠顯示「已分鏡」。而且不會報錯 —— 沒有人會發現。
    #
    #    儲存式狀態機的失敗模式是「有人忘了更新」，而那個失敗是靜默的。
    #    推導式沒有這個模式：狀態就是事實本身的一種讀法，不可能不同步。
    shot_list: ShotList | None = None
    # ---- Greenlight Local：導演迴圈 ----
    director_scenes: dict[str, dict] = Field(
        default_factory=dict,
        description="每一場的場面調度資料（agents/stager.py 產出、使用者可改）：世界、站位、道具、台詞與講者。"
                    "導演迴圈和 H3 提示詞都讀這份。key = director 的 scene_id（s061）。",
    )
    direction_log: list[dict] = Field(
        default_factory=list,
        description="導演迴圈的即時紀錄（每一回合、每一次評審、每一個階段），畫面的進度欄就是讀這份 —— "
                    "要讓使用者看得到程式在做什麼、做到哪一步。",
    )
    direction_status: str | None = Field(
        default=None, description="導演工作的狀態：running／done／failed（None＝還沒導過）。")
    refs_log: list[dict] = Field(
        default_factory=list,
        description="準備參考卡（全身卡、正反打空景、每鏡用哪張）的即時紀錄，畫面讀這份顯示進度（services/ref_jobs.py）。",
    )
    refs_status: str | None = Field(
        default=None, description="準備參考卡的狀態：running／done／failed（None＝還沒做過）。")
    # ---- 學語言模式：台詞導演（language/jobs.py）----
    lines: list[dict] = Field(
        default_factory=list,
        description="台詞導演的結果，一句一筆：{id, shot_id, speaker, source（劇本原句）, ruby（原聲語言＋讀音標記）, "
                    "spoken（去掉讀音的台詞，送 H3 唸的就是這句）, register, why, subs{語言: 字幕}}。"
                    "原句一定留著：重導台詞要從原句重來，不能拿改過的再改。")
    study_notes: list[dict] = Field(
        default_factory=list, description="這一段的學習筆記（挑幾句講為什麼這樣說、換對象怎麼說、同句型例句）。")
    lines_log: list[dict] = Field(default_factory=list, description="台詞導演的即時紀錄，畫面讀這份顯示進度。")
    lines_status: str | None = Field(default=None, description="台詞導演的狀態：running／done／failed（None＝還沒做過）。")
    finish_log: list[dict] = Field(
        default_factory=list, description="成片流程（剪頭→放大→轉場→接片→燒字幕，services/finish.py）的即時紀錄，畫面讀這份。")
    finish_status: str | None = Field(default=None, description="成片流程狀態：running／done／failed。")
    finish_outputs: dict[str, str] = Field(
        default_factory=dict, description="成片流程的產出：master（沒字幕）、各字幕版（ja-zh…）→ 媒體庫 uri。")
    study_videos: dict[str, dict] = Field(
        default_factory=dict,
        description="學習字幕版成片，key＝字幕語言（ja-zh 的 zh）：{status, uri, error}。重新接片或重導台詞時清空。")
    prompts_exported_at: str | None = None
    test_shot_id: str | None = None
    test_shot_approved: bool = False
    clips: list[ClipRecord] = Field(
        default_factory=list,
        description=(
            "批次生成的逐鏡進度。**必須持久化** —— 產品設計是每一鏡都停下來確認，"
            "所以這個流程天生會橫跨很長的時間（12 鏡至少 30 分鐘）。"
            "中途關掉瀏覽器、隔天再回來，都要能從同一個地方接下去。"
        ),
    )
    test_shot: TestShotRecord | None = Field(
        default=None,
        description=(
            "試拍結果。🔴 存**實際**輸出規格而不只是「成功了」——"
            "API 回 200 不代表它照做了（9:16 那次就是這樣驗出來的），"
            "所以要留下實際秒數與尺寸，讓人能自己核對。"
        ),
    )
    output_uri: str | None = Field(
        default=None,
        description="接好的成片。media_store URI，接片後才有值。",
    )
    output_seconds: float = Field(default=0.0, description="成片實際總長")
    output_built_at: str = Field(
        default="",
        description=(
            "成片是什麼時候接的（ISO 時間）。"
            "🔴 用途是判斷「過期」：任何 clip 的 generated_at 比它晚，"
            "代表接完之後有鏡頭重拍過，成片跟鏡頭對不上了。"
            "過期是正常狀態不是錯誤（重接一次就好），但 UI 必須講出來 ——"
            "否則使用者反悔重拍第五鏡之後，下載到的還是舊成片而不自知。"
        ),
    )
    burned: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "已燒錄字幕的成片檔名，key 是語言碼（zh/en/ja）。"
            "🔴 沒有這個欄位之前，燒錄狀態只存在前端 state —— F5 一按，"
            "明明燒好的檔案還在媒體庫，畫面卻顯示「還沒燒」，"
            "使用者會再燒一次白等 90 秒。重新接片時清空（舊燒錄對應舊成片）。"
        ),
    )
    subtitles: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "各語言的 SRT 內容，key 是語言碼。"
            "外掛不燒錄：影片只生一次，"
            "字幕想出幾種語言都行 —— 燒進畫面的話換語言就要重新輸出整支。"
            "翻譯過的存下來，同一種語言不重複付費。"
        ),
    )

    spent_usd: float = 0.0

    @property
    def status(self) -> SegmentStatus:
        """這一段走到哪了 —— **從事實推導，不是儲存的**。

        由後往前判斷（最進階的條件先問），因為進度是單調往前的：
        有成片就一定分過鏡，反過來不成立。

        每一個判斷都綁在一件「真的發生過、留下痕跡」的事上：

            DONE              有 output_uri      → 接片產出的檔案存在
            GENERATING        有任何 clip 有 uri → 至少生出過一鏡
            TEST_SHOT_PASSED  test_shot_approved → 人按過通過
            PROMPTS_READY     prompts_exported_at→ 提示詞產生過
            SHOTS_PLANNED     shot_list 不是 None
            NOT_STARTED       以上皆非

        所以「狀態不對」只可能有一種原因：**那件事實本身被清掉了**。
        那正是我們要的行為 —— 例如 reset_batch 清掉 clips 與 output_uri
        之後，狀態自己就退回 TEST_SHOT_PASSED，不需要誰記得去改。
        """
        if self.output_uri:
            return SegmentStatus.DONE
        if any(c.uri for c in self.clips):
            return SegmentStatus.GENERATING
        if self.test_shot_approved:
            return SegmentStatus.TEST_SHOT_PASSED
        if self.prompts_exported_at:
            return SegmentStatus.PROMPTS_READY
        if self.shot_list is not None:
            return SegmentStatus.SHOTS_PLANNED
        return SegmentStatus.NOT_STARTED

    @property
    def status_label(self) -> str:
        return _STATUS_LABELS[self.status]

    @property
    def characters_needed(self) -> list[str]:
        """這一段需要哪些角色選定。

        按需選角：做第 1 章時，第 5 章才出現的角色不需要先選。
           逼使用者為還用不到的角色選長相，是沒必要的門檻。

        規劃階段（還沒分鏡）時回空清單 —— 那時候還不知道誰會入鏡，
        由呼叫端改從劇本的場次資料推。
        """
        if not self.shot_list:
            return []
        names: list[str] = []
        for shot in self.shot_list.shots:
            for name in shot.characters_in_frame:
                if name not in names:
                    names.append(name)
        return names


class SegmentPlan(BaseModel):
    """一種分段方案。

    使用者會看到二到四個方案並排比較：
        按章節分（8 段，平均 5.6 分鐘，每段約 $17）
        分成 5 段（平均 9 分鐘，每段約 $27）
        分成 12 段（平均 3.8 分鐘，每段約 $11）

    這就是「給它看全貌」。
    """

    plan_id: str
    label: str = Field(description="給人看的方案名稱，例如「按劇本章節分（8 段）」")
    source: SegmentSource
    segments: list[Segment] = Field(default_factory=list)

    rationale: str = Field(
        default="", description="為什麼這樣分。人話一到兩句。"
    )

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def total_seconds(self) -> float:
        return sum(s.estimated_seconds for s in self.segments)

    @property
    def average_seconds(self) -> float:
        return self.total_seconds / len(self.segments) if self.segments else 0.0

    @property
    def self_contained_count(self) -> int:
        """有幾段能獨立成立。越多越適合「先拍一段看效果」。"""
        return sum(1 for s in self.segments if s.is_self_contained)


class SegmentationOptions(BaseModel):
    """所有分段方案 —— 使用者用按鈕挑一個（鐵律 7：不打字）。"""

    screenplay_title: str
    total_scenes: int
    total_seconds: float

    options: list[SegmentPlan] = Field(default_factory=list)
    recommended_plan_id: str | None = None
    recommendation_reason: str = ""

    def plan(self, plan_id: str) -> SegmentPlan | None:
        return next((p for p in self.options if p.plan_id == plan_id), None)


class SegmentProgress(BaseModel):
    """整個專案的分段進度摘要。給 UI 的總覽畫面用。

    「保留進度」：使用者隔兩週回來，看這個就知道
    「第 1 章拍完了、第 2 章分鏡到一半、其他還沒開始」。
    """

    plan_label: str
    total_segments: int
    done: int
    in_progress: int
    not_started: int
    total_spent_usd: float

    next_suggested_segment: str | None = Field(
        default=None,
        description="建議接下來做哪一段。通常是「第一個還沒開始的」。",
    )
