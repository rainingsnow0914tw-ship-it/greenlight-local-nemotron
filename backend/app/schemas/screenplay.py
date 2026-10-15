"""劇本層的資料契約。

設計理由：
    這一層是整個系統的入口。使用者丟進來的是一份非結構化的劇本（PDF 或純文字），
    script_parser agent 的唯一工作就是把它填成這裡定義的形狀。

    為什麼要先定 schema 再寫 agent —— 見 PROJECT_GENESIS 鐵律 2「schema 當邊界」：
    agent 輸出對不對，由 pydantic 驗證說了算，不靠人肉看 JSON。
    Gemini 支援 structured output，我們把這些 model 直接當成回應格式送過去。

踩坑預防：
    劇本格式在真實世界極度混亂 —— Final Draft 匯出的 PDF、Word 轉的、
    甚至是編劇直接貼在記事本裡的。所以每個欄位都要能容忍缺漏，
    只有 `scene_number` 跟 `raw_text` 是必填。寧可少抓也不要 parser 整個炸掉。

用真實素材驗證後的重大修正：
    拿一份真實劇本來測，那份**完全沒有場景標題行**。
    它是小說體，混合了系統日誌（時間戳、狀態標記）、對話、內心獨白、
    「用戶視角筆記」。

    這才是真實世界創作者手上的東西 —— 有好故事的人多半不寫標準劇本格式。
    如果 script_parser 只認 `INT./EXT.` 開頭的 slugline，它會對這份劇本
    完全失效，而那正好是我們的目標用戶。

    所以：
      - `SceneSetting` / `TimeOfDay` 的 UNKNOWN 不是例外情況，是常態
      - 場景的切分要靠**語意判斷**（地點變了、時間跳了、視角換了），
        不是靠找 slugline
      - 散文體的「一段對話」可能橫跨整章，agent 要自己判斷該切成幾場
      - `slugline` 欄位在這類劇本裡永遠是 None，UI 不能假設它有值
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SceneSetting(str, Enum):
    """場景的內外景標記。

    劇本的場景標題行（slugline）開頭一定是這個，例如 `INT. 咖啡廳 - 日`。
    抓不到的時候用 UNKNOWN，不要猜 —— 猜錯會讓後面的打光提示詞整段歪掉。
    """

    INT = "INT"
    EXT = "EXT"
    INT_EXT = "INT/EXT"
    UNKNOWN = "UNKNOWN"


class TimeOfDay(str, Enum):
    """場景時間。影響 Veo 提示詞裡的光線描述，也影響成功率（夜景較難生）。"""

    DAY = "DAY"
    NIGHT = "NIGHT"
    DAWN = "DAWN"
    DUSK = "DUSK"
    CONTINUOUS = "CONTINUOUS"
    UNKNOWN = "UNKNOWN"


class Character(BaseModel):
    """劇本裡的一個角色。

    `visual_description` 是後面 character_designer agent 生 Imagen 參考圖的依據。
    劇本常常不寫外貌（編劇留給選角），所以這欄可以是空的 ——
    真的空的時候，UI 要引導使用者自己補，不要讓 AI 亂編一個。
    """

    name: str
    visual_description: str | None = Field(
        default=None,
        description=(
            "外貌描述。劇本常常不寫（編劇留給選角），所以這欄可以是空的。"
            "🔴 空的時候不要求使用者打字補 —— 違反鐵律 7「零輸入」。"
            "改由 character_designer agent 依台詞、年齡線索、劇情功能"
            "生成三個候選外貌，讓使用者用按鈕選。"
        ),
    )
    first_appearance_scene: int | None = None
    line_count: int = Field(default=0, description="台詞行數，用來判斷主配角權重")
    is_principal: bool = Field(
        default=False,
        description="是否為主要角色。主要角色才值得花 Imagen Ultra 生高品質參考圖。",
    )


class DialogueLine(BaseModel):
    """一句台詞。

    `parenthetical` 是劇本格式裡演員括號指示，例如 `（壓低聲音）`。
    這個對 Veo 的表演提示很有用，不要在解析時丟掉。
    """

    character: str
    text: str
    parenthetical: str | None = None


class Scene(BaseModel):
    """劇本的一場戲。

    `estimated_screen_seconds` 是這一層最重要的推估值 ——
    後面 shot_planner 要靠它決定這場戲切成幾個鏡頭。

    業界慣例是「一頁劇本約一分鐘銀幕時間」，但那是給對白戲用的；
    動作場面一行字可能演三十秒。所以這個數字由 agent 依內容判斷，
    不是用頁數換算。
    """

    scene_number: int
    setting: SceneSetting = SceneSetting.UNKNOWN
    location: str | None = None
    time_of_day: TimeOfDay = TimeOfDay.UNKNOWN
    slugline: str | None = Field(
        default=None, description="原始場景標題行，保留原文供人工核對"
    )
    action: str = Field(default="", description="動作描述（非台詞的敘述文字）")
    dialogue: list[DialogueLine] = Field(default_factory=list)
    characters_present: list[str] = Field(default_factory=list)
    estimated_screen_seconds: float = Field(
        default=0.0,
        description="推估銀幕時間（秒）。由 agent 依內容判斷，不是用頁數換算。",
    )

    # ---- 原文定位 ----
    #
    # 成本優化：
    #   agent 只回報「第 12–45 行是第 3 場」，不要把原文抄一遍。
    #   原文我們手上就有，讓 LLM 複製貼上是純粹的浪費 ——
    #   一份 40K token 的劇本，抄回來就是 40K token 的輸出，
    #   以 Pro 的 $12/1M 計算等於白花 $0.48。
    #
    #   raw_text 由呼叫端依行號從原檔切出來填入，不經過模型。
    source_line_start: int = Field(
        default=0, description="這場戲在原檔的起始行號（1-based）"
    )
    source_line_end: int = Field(default=0, description="結束行號（含）")

    raw_text: str = Field(
        default="",
        description="原始文字。**由 code 依行號回填，不是 agent 輸出的**。",
    )


class Screenplay(BaseModel):
    """一份完整的劇本。

    `total_estimated_seconds` 是各場戲推估時間的加總，
    也是使用者第一次看到「這份劇本大概是幾分鐘的片」的依據。

    注意：這個數字通常會遠大於使用者的預算能負擔的長度。
    這正是 budget_analyst 存在的理由 —— 它要告訴使用者
    「你這份劇本完整拍是 12 分鐘、要 $86；如果只拍前三場是 2 分鐘、$14」。
    """

    title: str
    author: str | None = None
    source_format: str = Field(
        default="text", description="pdf / fountain / fdx / text"
    )
    scenes: list[Scene] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    total_estimated_seconds: float = 0.0
    language: str = Field(default="zh-TW", description="劇本語言，影響配音與字幕")

    parse_warnings: list[str] = Field(
        default_factory=list,
        description=(
            "解析過程中的疑慮，例如「第 7 場沒有場景標題行」。"
            "🔴 這些必須顯示給使用者看 —— 沉默地猜測是最糟的做法。"
        ),
    )
