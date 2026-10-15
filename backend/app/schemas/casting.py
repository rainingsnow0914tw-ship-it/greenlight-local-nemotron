"""選角關卡的資料契約。

設計理由：
    「他生完三張人物圖，沒滿意可以更換，因為圖片比較便宜嘛。
      他選好滿意了，才能有下一步，才開始生成影片，多一個關卡。」

    這是整個成本結構裡最划算的一次停頓：
        一張角色圖 $0.02 / 一個 8 秒鏡頭 $0.80 / 一支 3 分鐘的片 $18

    角色長相是所有後續鏡頭的地基。開始生影片才發現主角長得不對，
    前面所有片段全部報廢。所以要把猶豫留在最便宜的階段 ——
    在這裡多花 $0.30 反覆挑，避免後面 $18 的重來。

這一關是**硬性閘門**，不是建議：
   所有角色都選定之前，生成影片的路徑必須是鎖住的。
   見 CastingGate.can_proceed。
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field


def _name_keys(name: str) -> set[str]:
    """把角色名拆成可比對的別名集合。

    `Luna (月音/翎)` → {"luna (月音/翎)", "luna", "月音", "翎"}

    只拆兩種結構：括號（同名的注記寫法）與斜線（並列別名）。
    不拆空格與點號 —— 「監管AI.CTRL-7」「Dr. Chen」這種名字
       拆了會碎成毫無意義的片段，反而製造假交集。
    """
    cleaned = (name or "").casefold().strip()
    if not cleaned:
        return set()
    keys = {cleaned}
    # 括號內外分開收（全形半形都認）
    for part in re.split(r"[()（）]", cleaned):
        for token in re.split(r"[/／、]", part):
            token = token.strip()
            if token:
                keys.add(token)
    return keys


class CharacterKind(str, Enum):
    """角色類型 —— 決定重抽方向按鈕給哪一組。

    《數據深淵中的凝視》的主角「深息」是一個沒有身體的 AI。
    對他問「要更年輕還是更成熟」毫無意義，該問的是
    「更具象還是更抽象」「更冷還是更溫」。
    """

    HUMAN = "human"
    NON_HUMAN = "non_human"


class InferenceSource(str, Enum):
    """這張候選圖的外貌是根據什麼推出來的。

    誠實原則：UI 必須顯示這個。
       「依劇本明確描述」跟「依說話方式推測」的可信度差很多，
       使用者有權知道哪些是他自己寫的、哪些是系統猜的。
    """

    EXPLICIT_DESCRIPTION = "explicit_description"
    DIALOGUE_REFERENCE = "dialogue_reference"
    AGE_SIGNAL = "age_signal"
    ROLE_FUNCTION = "role_function"
    TONE_OF_VOICE = "tone_of_voice"


class CharacterCandidate(BaseModel):
    """一張候選角色圖。"""

    candidate_id: str
    character_name: str
    round_number: int

    image_uri: str = Field(default="", description="GCS URI。規劃階段為空。")
    prompt_used: str = Field(default="", description="產生這張圖的完整提示詞")

    interpretation: str = Field(
        default="",
        description=(
            "這張圖的詮釋方向，一句人話（中文）。例如「偏內斂、書卷氣」。"
            "UI 顯示在圖片下方 —— 三張並排時，使用者需要知道差異在哪裡，"
            "不能只靠看圖自己猜。"
        ),
    )

    appearance_tags: str = Field(
        default="",
        description=(
            "餵給 Veo 的**英文外貌標籤**，注入在每個鏡頭提示詞的最前面。"
            "🔴 這不是角色名字 —— 模型不需要知道她叫林曉薇，"
            "需要的是「asian woman, late 20s, shoulder-length wavy black hair,"
            " bright yellow knit sweater」這種可視覺化的特徵。"
            "選定候選圖時一併固定下來，之後所有鏡頭共用同一組標籤，"
            "這是角色一致性的核心機制。"
        ),
    )

    voice_tags: str = Field(
        default="",
        description=(
            "餵給 Veo 的**英文音色描述**，注入每一鏡的台詞段。"
            "🔴 這是配音一致性的唯一機制 —— 每一鏡都是獨立生成的，"
            "Veo 每次會自己決定音色，同一個角色在第 1 鏡和第 8 鏡"
            "會變成兩個人在講話。先前實測已確認："
            "每段 prompt 都要寫明配音者特徵。"
            "例：'a young woman in her twenties, soft calm voice, gentle pacing'"
        ),
    )

    inference_source: InferenceSource = InferenceSource.ROLE_FUNCTION
    inference_note: str = Field(
        default="",
        description="根據什麼推的，具體說明。例如「劇本第 371 行的外貌描述」。",
    )

    cost_usd: float = 0.0
    image_skipped: bool = Field(
        default=False,
        description=(
            "True = 這一輪刻意沒生圖（設定頁沒有生圖模型），是文字版候選，可以直接選定。"
            "跟「圖生失敗」分開：失敗的不給選，文字版的給選 —— 本機 H3 只吃文字，外貌標籤＋音色就夠了。"
        ),
    )


class CastingRound(BaseModel):
    """一輪三張候選。"""

    round_number: int
    direction_id: str = Field(
        default="initial",
        description="這一輪是按哪個方向按鈕生的。第一輪固定是 initial。",
    )
    direction_label: str = "初次生成"
    candidates: list[CharacterCandidate] = Field(default_factory=list)
    segment_order: int | None = Field(
        default=None,
        description=(
            "None = 一般選角輪（決定角色本體）。"
            "有值 = 這一輪是**第 N 段的造型變體**（換服裝／換髮型），"
            "臉沿用已鎖定的身份參考，只換造型。"
            "同一角色可能有幾個章節要換衣服 —— "
            "服裝是段落層的東西，跟敘事形式同一層；長相才是全片鎖死的。"
        ),
    )

    @property
    def cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.candidates), 3)


class CastingSession(BaseModel):
    """一個角色的完整選角過程。"""

    character_name: str
    kind: CharacterKind = CharacterKind.HUMAN
    is_principal: bool = False

    source_description: str | None = Field(
        default=None,
        description="劇本裡的外貌描述。沒有就是 None —— 這很常見，不是錯誤。",
    )

    rounds: list[CastingRound] = Field(default_factory=list)

    selected_candidate_id: str | None = Field(
        default=None,
        description="使用者選定的那一張。None = 還沒選，這個角色還沒過關。",
    )
    voice_locked_from: str | None = Field(
        default=None,
        description=(
            "音色是從哪一鏡聽出來並鎖定的（shot_id）。"
            "第一次出現的音色就鎖定在那個角色上，不能每鏡隨機生成。"
            "所以只鎖一次 —— 有值就代表這個角色的音色已經定了，"
            "後面每一鏡都用同一串描述，不再重新分析。"
            "重新分析等於每一鏡都可能換一種說法，那就回到隨機了。"
        ),
    )
    final_reference_uri: str | None = Field(
        default=None,
        description=(
            "選定後用 Imagen Standard 重生的高品質正式參考圖。"
            "後續所有鏡頭的角色一致性靠這張。"
        ),
    )

    # ── 身份參考（臉）vs 造型（這部片裡的樣子）—— 兩層分開──
    #
    # 使用者上傳的多半只是樣貌參考（一張大頭照），沒有造型、沒有服裝。
    #
    #    所以上傳的照片**不是角色卡**，是設計師的**輸入**：拿著這張臉，
    #    照這部片的視覺風格生三個造型候選，選定的那張才是角色卡。
    #    沒上傳的角色也一樣有身份參考 —— 第一次鎖定時自動取選定那張，
    #    之後每次重生（換服裝、換髮型）都拿它當輸入，臉才不會跑。
    identity_reference_uri: str | None = Field(
        default=None,
        description=(
            "身份參考圖（臉）。來源見 identity_source。"
            "有值時，character_designer 生圖會把這張圖當輸入、只換造型不換臉。"
        ),
    )
    identity_source: str | None = Field(
        default=None,
        description=(
            "'uploaded' = 使用者上傳（大頭照／自帶角色設定圖）；"
            "'generated' = 第一次鎖定時從選定候選自動取用。"
            "解除鎖定時 generated 的會清掉（他就是不要這張臉），uploaded 的保留。"
        ),
    )
    identity_consent: dict | None = Field(
        default=None,
        description=(
            "上傳身份參考時的免責確認：{accepted_at, statement_version}。"
            "使用者若拿別人的照片來生成，責任歸屬在使用者。"
            "沒勾就不收檔（route 層擋），勾了就把時間記在專案裡。"
        ),
    )
    segment_looks: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "段落造型覆寫：{str(segment_order): candidate_id}。"
            "key 用字串是因為 Firestore map key 必須是字串。"
            "有覆寫的段落，影片生成的參考圖用這張；沒有就用全片選定的那張。"
        ),
    )

    @property
    def total_spent_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.rounds), 3)

    @property
    def rounds_used(self) -> int:
        return len(self.rounds)

    @property
    def is_locked(self) -> bool:
        """選定了就鎖住。鎖住之後不能再重抽 —— 免得改來改去讓已生成的鏡頭失效。"""
        return self.selected_candidate_id is not None

    @property
    def selected(self) -> CharacterCandidate | None:
        if not self.selected_candidate_id:
            return None
        for rnd in self.rounds:
            for cand in rnd.candidates:
                if cand.candidate_id == self.selected_candidate_id:
                    return cand
        return None

    def candidate(self, candidate_id: str) -> CharacterCandidate | None:
        for rnd in self.rounds:
            for cand in rnd.candidates:
                if cand.candidate_id == candidate_id:
                    return cand
        return None

    def look_for(self, segment_order: int | None) -> CharacterCandidate | None:
        """第 N 段實際要用的角色卡：有段落造型覆寫就用它，沒有就用全片選定那張。

        影片生成（`_reference_images_for`）唯一該問的入口 ——
        不要在呼叫端自己翻 segment_looks，規則只放一處。
        """
        if segment_order is not None:
            cid = self.segment_looks.get(str(segment_order))
            if cid:
                found = self.candidate(cid)
                if found and found.image_uri:
                    return found
        return self.selected

    def all_candidates(self) -> list[CharacterCandidate]:
        """所有輪次的候選圖攤平。

        用途：讓使用者能回頭選前面幾輪的圖 ——
        抽了五輪之後常常會覺得「還是第二輪那張好」。
        不保留歷史等於逼他重抽，那是白花錢。
        """
        return [c for rnd in self.rounds for c in rnd.candidates]


class CastingGate(BaseModel):
    """整片所有角色的選角關卡。

    這是硬性閘門。can_proceed 為 False 時，
       UI 必須把「開始生成影片」的按鈕鎖住並說明還缺誰。
    """

    plan_id: str
    sessions: list[CastingSession] = Field(default_factory=list)

    @property
    def total_spent_usd(self) -> float:
        return round(sum(s.total_spent_usd for s in self.sessions), 3)

    @property
    def pending_characters(self) -> list[str]:
        """還沒選定的角色。UI 用這個列出「還差誰」。"""
        return [s.character_name for s in self.sessions if not s.is_locked]

    @property
    def can_proceed(self) -> bool:
        """所有角色都選定了才能往下走。

        沒有角色的片（純空鏡）也算通過 —— sessions 是空的，
        all() 對空序列回 True，這個行為是對的。
        """
        return all(s.is_locked for s in self.sessions)

    def session_for(self, character_name: str) -> CastingSession | None:
        """依角色名找 session，容忍名字的不同寫法。

        為什麼不能只做精確比對：
           parser 認出「Luna／月音／翎」是同一人後，把角色名合併成
           `Luna (月音/翎)` 存進 casting session；但 shot_planner 寫進
           `characters_in_frame` 的是劇本該場實際用的那個寫法（`Luna`）。
           精確比對對不上 → 外貌標籤與音色**靜默不注入** ——
           選角明明鎖了，生成出來卻像沒選過角，而且不會有任何錯誤。

           角色名是使用者劇本來的資料，不能要求它格式統一
           （跟「角色名不能當 URL 路徑」是同一條教訓）。
           所以由系統把 `A (B/C)` 這種複合名拆成別名集合來比對。
        """
        target = _name_keys(character_name)
        exact = next(
            (s for s in self.sessions if s.character_name == character_name), None
        )
        if exact:
            return exact

        # 別名比對：兩邊的別名集合有交集就算同一人。
        # 撞名（兩個 session 都匹配）時回 None 不亂猜 ——
        # 注錯角色的外貌比不注入更糟，錯誤會被穩定鎖進每一鏡。
        matched = [
            s
            for s in self.sessions
            if target & _name_keys(s.character_name)
        ]
        return matched[0] if len(matched) == 1 else None
