"""鏡頭層的資料契約。

設計理由：
    一場戲不等於一個鏡頭。Veo 單次最長只能生 8 秒，
    所以一場 45 秒的戲必須被拆成 6 個鏡頭，並且決定哪幾個要用首尾幀接續。

    這一層的欄位命名刻意貼近真實影視術語（shot_type / camera_move / insert），
    原因有二：
      1. 這些概念本來就存在，重新發明詞彙只會讓懂行的人覺得外行。
      2. 這些標籤同時是成功率預估的特徵值 —— priors.yaml 裡的修正項
         就是按這些欄位分類的。schema 跟預測模型共用同一套詞彙，
         不會出現「規劃講 A、預測講 B」的錯位。

重要：這裡的每個列舉值，priors.yaml 的 modifiers 都必須有對應項。
   兩邊不同步的話，success_predictor 會靜默地用預設值 0.0，
   估出來的數字看起來正常但其實沒吃到修正。
   `services/config_loader.py` 啟動時會做這個一致性檢查。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# 每次 Veo Extend 增加的秒數。
#
# 為什麼寫在這裡而不是從 config 讀：
#   schema 是最底層，不該依賴 services（會造成循環 import）。
#   但這個值必須與 pricing.yaml veo.extend.seconds_per_extension 一致 ——
#   config_loader.verify_config_integrity() 啟動時會檢查兩邊是否相同，
#   不一致就拒絕啟動。用架構保證同步，不靠人記得。
_SECONDS_PER_EXTENSION = 7


class ShotType(str, Enum):
    """鏡別。畫面資訊量由大到小。

    資訊量越大，模型出錯的地方越多 —— 見 priors.yaml modifiers.shot_type。
    """

    EXTREME_WIDE = "EXTREME_WIDE"
    WIDE = "WIDE"
    MEDIUM = "MEDIUM"
    MEDIUM_CLOSE = "MEDIUM_CLOSE"
    CLOSE_UP = "CLOSE_UP"
    EXTREME_CLOSE = "EXTREME_CLOSE"
    INSERT = "INSERT"


class CameraMove(str, Enum):
    """運鏡。攝影機動得越複雜，時間一致性越難維持。"""

    STATIC = "STATIC"
    PAN = "PAN"
    TILT = "TILT"
    DOLLY = "DOLLY"
    TRACKING = "TRACKING"
    HANDHELD = "HANDHELD"
    CRANE = "CRANE"
    ZOOM = "ZOOM"


class ContentFlag(str, Enum):
    """已知的高風險內容標記。

    這些是實務上模型最常崩的地方。shot_planner 標出來之後有兩個用途：
      1. 餵給 success_predictor 調降成功率
      2. 在 UI 上主動提示使用者「這個鏡頭建議改成不入鏡的拍法」

    例如 TEXT_IN_FRAME（畫面內文字）幾乎必崩，正確做法是生成時不放文字、
    後製再疊上去。系統應該主動這樣建議，而不是讓使用者燒三次錢才發現。
    """

    HANDS_VISIBLE = "hands_visible"
    TEXT_IN_FRAME = "text_in_frame"
    FAST_ACTION = "fast_action"
    WATER_OR_FIRE = "water_or_fire"
    CROWD = "crowd"
    ANIMAL = "animal"
    PERIOD_COSTUME = "period_costume"
    NIGHT_SCENE = "night_scene"

    # 以下三個是為了觸發 constraints.yaml 的對應約束規則而加的。
    # 它們本身也是風險（所以 priors.yaml 有對應修正），但更重要的是
    # 它們讓 constraint_engine 知道該注入哪幾句提示詞。
    SCREEN_IN_FRAME = "screen_in_frame"
    COUNTABLE_GROUP = "countable_group"
    REFLECTIVE_SURFACE = "reflective_surface"


class Shot(BaseModel):
    """一個鏡頭 —— 也就是一次 Veo 呼叫。

    首尾幀邏輯（有些鏡需要頭尾幀、有些不需要，交給 AI 判斷）：

        `first_frame_from` 有值 = 這個鏡頭要接上一鏡的末幀，畫面連續。
        通常用在同一場戲內的連續動作、或需要維持場景一致的相鄰鏡頭。

        `first_frame_from` 為 None = 這是一個新的起點。
        場景切換、時間跳躍、或刻意的硬切，都不該接首幀 ——
        硬接反而會讓模型試圖從舊畫面「演變」到新場景，產生詭異的過渡。

        判斷規則由 shot_planner agent 執行，但預設保守：
        跨場景一律不接，同場景相鄰且無時間跳躍才接。
    """

    shot_id: str = Field(description="穩定識別碼，例如 s003_02")
    scene_number: int
    order_in_scene: int

    base_duration_seconds: Literal[4, 6, 8, 10] = Field(
        default=8,
        description=(
            "單次生成的長度。🔴 引擎各有硬限制，不是我們的選擇："
            "Veo 只能 4 / 6 / 8（durationSeconds 參數）；"
            "Omni Flash 固定 10（不可調，實測）。"
            "見 pricing.yaml 各檔位設定。"
        ),
    )

    extensions: int = Field(
        default=0,
        ge=0,
        le=20,
        description=(
            "續拍次數。每次 Extend 在片尾接 +7 秒，最多 20 次。"
            "例如 base=8、extensions=1 → 15 秒（「一段 15 秒」）。"
            "續拍會累積誤差（角色漂移、光線飄移），每一次都會降低成功率，"
            "見 priors.yaml per_extension_penalty。"
        ),
    )

    shot_type: ShotType = ShotType.MEDIUM
    camera_move: CameraMove = CameraMove.STATIC

    description: str = Field(
        default="",
        description=(
            "這個鏡頭要拍什麼 —— **使用者的語言**（中文），給人看的。"
            "UI 上顯示這一欄，不顯示 visual_prompt。"
        ),
    )

    visual_prompt: str | None = Field(
        default=None,
        description=(
            "餵給模型的英文描述，由 prompt_writer agent 產生。"
            "🔴 為什麼要分兩欄：Veo 對英文指令的服從度明顯高於中文，"
            "而且導出給別家工具用時英文通用性更好。"
            "但使用者不該被迫看英文 —— 他寫的是中文劇本。"
            "所以：description 給人看，visual_prompt 給模型。"
            "沒有這一欄時 prompt_export 會退回用 description 並發出警告。"
        ),
    )
    characters_in_frame: list[str] = Field(default_factory=list)
    dialogue: str | None = Field(
        default=None, description="這個鏡頭要說的台詞。有台詞會降低成功率（對嘴難）。"
    )
    voice_role: Literal["character", "offscreen", "narrator"] = Field(
        default="character",
        description=(
            "這句話**以什麼身份說出來**。典型症狀是"
            "「紀錄片的第三人稱旁白，突然變成主角開口在講」。 "
            "character＝畫面裡的角色開口說話、要對嘴； "
            "offscreen＝某個**角色**在畫面外說話（電話、隔壁房間），"
            "有身份也有音色，但畫面上不該有人對嘴； "
            "narrator＝**第三人稱旁白**，不是劇中任何角色 ——"
            "紀錄片解說、導讀、全知視角的敘述都是這種。 "
            "🔴 為什麼 speaker 不夠用：speaker 只存「名字」，"
            "而上面三種在名字上長得一樣（都可能填某個角色名或空字串）。"
            "下游 prompt_export 拿 speaker 去查選角表，旁白就被當成角色 ——"
            "套上他的外貌標籤與音色，再組出 `X says:`，"
            "而 `[主體] says` 正是官方用來**觸發對嘴**的語法"
            "（向平台 AI 查證）。模型只是忠實執行了我們的要求。 "
            "預設 character 是為了向後相容 —— 這個欄位之前的分鏡表沒有它，"
            "而那些分鏡表裡的台詞絕大多數確實是角色在說。"
        ),
    )
    speaker: str | None = Field(
        default=None,
        description=(
            "**誰在說這句話**。角色名，必須是劇本裡的角色。"
            " 這跟 characters_in_frame 是兩回事："
            "前者是「誰在說」，後者是「畫面上有誰」。劇本裡這兩者經常不同 ——"
            "畫外音、旁白、電話那頭、隔壁房間傳來的聲音、打字傳來的訊息。"
            " 實例：s001_06 畫面是深息伸手觸碰光幕，台詞卻是月音問「你會夢嗎」。"
            "在這個欄位存在之前，系統只能從 characters_in_frame 取說話者，"
            "於是把深息的男聲套在月音的台詞上。"
            " None = 無法判斷（例如全知旁白）。那時提示詞退回中性的 A voice，"
            "而且**不會鎖定任何角色的音色** —— 鎖錯人比不鎖更糟："
            "不鎖只是每次隨機，鎖錯是穩定地錯下去。"
        ),
    )
    sfx: str | None = None
    music_cue: str | None = None

    content_flags: list[ContentFlag] = Field(default_factory=list)

    # ---- 連戲控制 ----
    first_frame_from: str | None = Field(
        default=None,
        description="接哪一鏡的末幀當首幀。None = 新起點（場景切換時應為 None）。",
    )
    flash_before: bool = Field(
        default=False,
        description=(
            "這一鏡前面閃白：前一鏡尾巴淡成白、這一鏡從白淡回來（正式管線 s3_assemble 的閃白）。"
            "用在同一場戲裡時間跳了一下、情緒前後接不上的地方（例：s061_15→16，"
            "月音從冷冷質問直接跳到哭著哀求，閃白當作中間吵了一段時間）。"
        ),
    )
    user_first_frame: str | None = Field(
        default=None,
        description=(
            "使用者自己上傳的首幀（媒體庫 uri）。有值就原圖釘在第 0 格，優先於接幀、不修畫質"
            "（人物卡、空景、首幀都可以自己帶；只有結尾幀要靠我們的接幀）。"
        ),
    )
    gate_waived: bool = Field(
        default=False,
        description=(
            "使用者看過生成前關卡的錯、決定照樣生成（第三世試跑：ぷく的「にゃあ」是貓叫不是講話，"
            "短句會唸糊那條不適用；前一句是籠外的塔列昂，沒有能併的鏡 —— 關卡分不出來，人分得出來）。"
        ),
    )
    reference_image_uri: str | None = Field(
        default=None,
        description="角色參考圖的 GCS URI。有參考圖大幅提升一致性（+0.20）。",
    )

    # ---- 生成產物（規劃階段為空）----
    veo_prompt: str | None = Field(
        default=None, description="prompt_writer agent 產出的實際提示詞"
    )
    output_uri: str | None = None

    # ---- Greenlight Local：導演迴圈（director/）與本機引擎（H3）的欄位 ----
    # 為什麼另開欄位、不改上面的：雲端引擎（Google）照舊用 base_duration_seconds／camera_move；
    # 本機引擎長度可以是 3–10 任何秒數，運鏡也比上面的大類細（每一句運鏡都是作者管線實測過的寫法）。
    engine_duration_seconds: float | None = Field(
        default=None, description="本機引擎的實際長度（3–10 秒，照台詞量算出來的）。有值時 total_seconds 用它。")
    engine_camera_move: str | None = Field(
        default=None, description="導演迴圈的精細運鏡（BREATHE、PUSH_FACE、PULL_FOCUS…），H3 提示詞照它寫。")
    spot: str | None = Field(default=None, description="站位（場面調度定的地點），決定場景行與場景參考圖。")
    blocking: str | None = Field(default=None, description="兩人同框的相對位置（side_by_side、facing…）。")
    performance: str | None = Field(
        default=None, description="導演寫的表演句（劇本語言），H3 提示詞的「表演」那一行。")
    dialogue_lines: list[dict] = Field(
        default_factory=list, description="一鏡有兩句以上（兩個人一來一往）時，每句 {speaker, text}。")
    director_scene_id: str | None = Field(default=None, description="對應 segment.director_scenes 裡哪一場的調度資料。")

    @property
    def total_seconds(self) -> int:
        """這個鏡頭的實際銀幕長度 = 基底 + 續拍（本機引擎：導演算出來的實際秒數）。

        成本與排片一律用這個數字，不要用 base_duration_seconds ——
        那只是第一次 API 呼叫的長度，不是觀眾看到的長度。
        """
        if self.engine_duration_seconds:
            return int(round(self.engine_duration_seconds))
        return self.base_duration_seconds + self.extensions * _SECONDS_PER_EXTENSION

    @property
    def api_call_count(self) -> int:
        """這個鏡頭要打幾次 Veo API。

        用途：配額估算與進度顯示。一個 15 秒的鏡頭是兩次呼叫（生成 + 續拍），
        使用者在進度條上會看到兩個步驟，不是一個。
        """
        return 1 + self.extensions

    @property
    def character_count_bucket(self) -> str:
        """把人數轉成 priors.yaml 用的分桶字串。

        為什麼要分桶而不是直接用數字：
        四人以上的失敗率不再有意義的區別（都很糟），
        繼續細分只會讓每一桶的樣本數過少，統計上不可靠。
        """
        n = len(self.characters_in_frame)
        return str(n) if n <= 3 else "4+"


class ShotList(BaseModel):
    """一份完整的鏡頭表。

    `total_seconds` 是所有鏡頭長度加總，也就是成片長度。
    這個數字乘上畫質檔位的每秒單價，就是最基本的成本估算 ——
    但真正的預估還要加上重試次數，見 services/cost_engine.py。
    """

    screenplay_title: str
    shots: list[Shot] = Field(default_factory=list)

    aspect_ratio: Literal["16:9", "9:16"] = Field(
        default="16:9",
        description=(
            "畫面比例。🔴 Veo 只接受這兩個值，沒有 1:1 也沒有寬銀幕 —— "
            "想要電影黑邊要後製裁切。"
            "比例會改變鏡頭難度：直式的橫向空間只有三分之一，"
            "遠景與多人場面容易擠爆，但特寫反而更自然。"
        ),
    )

    # ═══════════════════════════════════════════════════════════════════════
    # 語言是三個獨立的維度
    # ═══════════════════════════════════════════════════════════════════════
    #
    #   介面語言      使用者看的文字        → 前端的事，不存在這裡
    #   提示詞語言    餵給模型的畫面描述     → prompt_language
    #   影片語言      角色說什麼話、字幕     → film_language
    #
    # 會中文的人也可能要生成日文動畫：介面語言是一回事，影片語種是另一回事，
    # 使用者要有選擇權。
    #
    # 這三個不綁死的實例：
    #   台灣編劇 → 中文介面 + 日文提示詞（動畫風更精準）+ 日文配音（受眾在日本）
    #   或       → 中文介面 + 日文提示詞 + 中文配音（角色講中文，字幕上日文）
    #
    # 為什麼提示詞語言與影片語言可以不同：
    #   提示詞裡「畫面描述」與「台詞」是分開的兩段
    #   （見 services/prompt_export.py 的組裝順序）。
    #   畫面描述用日文寫、台詞用中文寫，是合法且合理的組合。

    prompt_language: str = Field(
        default="zh",
        description=(
            "寫畫面描述用哪種語言（zh / en / ja）。"
            "⚠️ 「哪種語言效果最好」目前是假設不是結論 —— "
            "早期 code 註解說「Veo 對英文服從度較高」是沿用業界說法，"
            "我們自己沒實測過。見 config/prompts/README.md。"
        ),
    )

    film_language: str = Field(
        default="zh",
        description=(
            "成品的語言：角色台詞說什麼話、字幕用什麼語言。"
            "Veo 的原生音訊會依提示詞裡台詞的語言發音，"
            "所以這個欄位實際控制的是台詞要用哪種語言寫進提示詞。"
        ),
    )

    style_id: str = Field(
        default="photoreal",
        description=(
            "影片風格，值來自 config/styles.yaml 的 presets[].id。"
            "🔴 風格是整片一致的決定，不是逐鏡設定 —— 混風格會讓觀眾出戲。"
            "風格會同時影響成功率（寫實最難、繪本最寬容）與哪些物理約束適用。"
        ),
    )

    planning_notes: list[str] = Field(
        default_factory=list,
        description=(
            "規劃過程的判斷說明，例如「第 3 場拆成 4 鏡，因為含一次時間跳躍」。"
            "這些要顯示給使用者 —— 讓他知道 AI 為什麼這樣拆，才有辦法修正。"
        ),
    )

    @property
    def total_seconds(self) -> int:
        """成片總長度（含所有續拍）。"""
        return sum(s.total_seconds for s in self.shots)

    @property
    def clip_count(self) -> int:
        """鏡頭數（不是 API 呼叫數）。"""
        return len(self.shots)

    @property
    def api_call_count(self) -> int:
        """總 Veo 呼叫次數。配額與進度顯示用。"""
        return sum(s.api_call_count for s in self.shots)
