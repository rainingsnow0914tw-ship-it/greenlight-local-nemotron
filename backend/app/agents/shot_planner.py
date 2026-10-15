"""分鏡 —— 把場次拆成一個一個可以生成的鏡頭。

設計理由：
    這一步要同時處理四件互相牽扯的事：

      1. **物理約束**：Veo 單次只能生 4/6/8 秒，更長要用 Extend（每次 +7 秒）
      2. **連戲**：哪幾鏡要接上一鏡的末幀，哪幾鏡該硬切
      3. **風險標記**：畫面裡有手、有文字、有群眾 → 影響成功率與該注入的約束
      4. **雙語**：中文描述給人看、英文提示詞給模型

    這四件事不能分開做 —— 決定「這一鏡 15 秒」就同時決定了它要續拍一次、
    誤差會累積、成功率要打折。所以一次讓模型全部想清楚，
    而不是先拆鏡再回頭補參數。

這份劇本的特殊挑戰（實跑《數據深淵中的凝視》發現）：
    大部分場景發生在「對話框」「系統內部」—— 沒有物理空間可拍。
    script_parser 自己也把這件事寫進了 warnings。

    分鏡時必須把抽象場景轉成具體畫面：
    「深息在等她上線」不能直接拍，要變成
    「螢幕的光映在空無一人的房間 / 游標在輸入框閃爍 / 數據流在暗處緩慢流動」。

    這是分鏡師的核心工作，也是這個 agent 存在的理由。
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.screenplay import Screenplay
from ..schemas.segment import Segment
from ..schemas.shot import (
    CameraMove,
    ContentFlag,
    Shot,
    ShotList,
    ShotType,
)
from ..services import rates
from ..services import config_loader, prompt_rooms, refinery


class PlannedShot(BaseModel):
    """模型回報的一個鏡頭。"""

    scene_number: int = Field(description="屬於哪一場")
    order_in_scene: int = Field(description="這場戲裡的第幾鏡，從 1 開始")

    base_duration_seconds: int = Field(
        description="單次生成長度。🔴 只能是 4、6 或 8，沒有別的選擇。"
    )
    extensions: int = Field(
        description=(
            "續拍次數，每次 +7 秒。0 = 不續拍。"
            "8 秒 + 1 次 = 15 秒。超過 2 次會明顯漂移，建議改拆鏡。"
        )
    )

    shot_type: str = Field(
        description="EXTREME_WIDE / WIDE / MEDIUM / MEDIUM_CLOSE / CLOSE_UP / EXTREME_CLOSE / INSERT"
    )
    camera_move: str = Field(
        description="STATIC / PAN / TILT / DOLLY / TRACKING / HANDHELD / CRANE / ZOOM"
    )

    description_zh: str = Field(description="這一鏡要拍什麼，中文，給人看的")
    visual_prompt_en: str = Field(
        description=(
            "餵給影片模型的英文描述。具體、可視覺化、不要抽象名詞。"
            "不要寫風格（系統會自動加）、不要寫角色外貌（系統會注入角色標籤）。"
        )
    )

    characters_in_frame: list[str] = Field(description="這一鏡畫面裡出現的角色")
    dialogue: str = Field(description="這一鏡的台詞。沒有就空字串。")
    speaker: str = Field(
        description=(
            "這句台詞是**誰在說**，用劇本裡的角色名。沒有台詞、或說話者"
            "不明（環境音、群眾雜聲）就空字串。"
            "🔴 跟 characters_in_frame 是兩回事 —— 旁白、畫外音、電話那頭，"
            "說話的人常常不在畫面上，此時 speaker 照填、characters_in_frame 不含他。"
        )
    )
    voice_role: str = Field(
        description=(
            "這句話以什麼身份說出來，三選一："
            "character（畫面裡的角色開口，會對嘴）、"
            "offscreen（某個角色在畫面外說話，例如電話、隔壁房間）、"
            "narrator（**第三人稱旁白**，不是劇中任何角色 —— "
            "紀錄片解說、導讀、全知視角的敘述）。"
            "🔴 劇本裡標了「（旁白）」「V.O.」「NARRATOR」的一律 narrator。"
            "沒有台詞時填 character（不影響任何事）。"
        )
    )
    sfx: str = Field(description="音效描述，英文。沒有就空字串。")
    music_cue: str = Field(description="音樂方向，英文。沒有就空字串。")

    content_flags: list[str] = Field(
        description=(
            "風險標記，從這些選：hands_visible / text_in_frame / fast_action / "
            "water_or_fire / crowd / animal / period_costume / night_scene / "
            "screen_in_frame / countable_group / reflective_surface。沒有就空陣列。"
        )
    )

    continues_from_previous: bool = Field(
        description=(
            "要不要接上一鏡的末幀當首幀。"
            "🔴 同一場戲的連續動作才接；換場、時間跳躍、刻意硬切都不要接 —— "
            "硬接會讓模型試圖從舊畫面「演變」到新場景，產生詭異的過渡。"
        )
    )

    planning_note: str = Field(
        description="這一鏡為什麼這樣拆、這樣長度。一句話。"
    )


class ShotPlanResult(BaseModel):
    shots: list[PlannedShot]
    notes: list[str] = Field(
        description="整體規劃說明，例如「第 3 場是純內心獨白，改用畫面加旁白處理」"
    )


# system instruction 走 config/prompts/base.yaml，不在這裡寫死。
#
#    這裡原本有一份 `_SYSTEM` 常數，而 base.yaml 裡**同時也有一份**
#    agents.shot_planner.system_instruction —— 兩份內容一模一樣，
#    只有排版符號不同（═══ vs ──）。實際生效的是 code 這份，
#    所以任何人去改 base.yaml 都不會有作用，而且不會有任何錯誤提示。
#
#    這些只是提示它生成提示詞的邊界跟規則——
#    規則只該有一個來源。順帶讓這個 agent 也吃得到三語房間
#    （在這之前它的 system instruction 寫死中文，選日文也沒用）。


def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    分鏡：實測 max 93.8 秒。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.LONG_MS, action="分鏡")


_SHOT_TYPES = {t.value for t in ShotType}
_MOVES = {m.value for m in CameraMove}
_FLAGS = {f.value for f in ContentFlag}


def audit_rhythm(shots: list[Shot]) -> list[str]:
    """檢查鏡頭表的節奏與連戲，把可疑的地方講出來。

    為什麼要在 code 層做這個（鐵律 6：能用架構解的不要用 prompt 解）：

       第一次實跑，模型產出的 13 個鏡頭**全部硬切、全部 8 秒以下**，
       完全沒有用到續拍。改 prompt 可以改善傾向，但 LLM 的輸出永遠是機率性的 ——
       下一次換個劇本可能又退回保守。

       所以除了改 prompt，還要有一個不依賴模型的檢查：
       算出實際的接續比例與長鏡比例，偏離常態就明講。

       這些是**提醒不是錯誤** —— 有些戲本來就該全部硬切
       （快節奏剪接、蒙太奇）。所以只提示，不自動修改。
    """
    notes: list[str] = []
    if len(shots) < 3:
        return notes

    # ---- 連戲：同一場戲內相鄰鏡頭的接續比例 ----
    same_scene_pairs = 0
    linked = 0
    for prev, curr in zip(shots, shots[1:]):
        if prev.scene_number == curr.scene_number:
            same_scene_pairs += 1
            if curr.first_frame_from:
                linked += 1

    if same_scene_pairs >= 3:
        ratio = linked / same_scene_pairs
        if ratio == 0:
            notes.append(
                f"⚠️ 同場戲內 {same_scene_pairs} 組相鄰鏡頭**全部硬切**，沒有一鏡接首尾幀。"
                f"同一個時空的連續片刻硬切會有跳接感 —— "
                f"如果不是刻意的快節奏剪接，建議讓部分鏡頭接續。"
            )
        elif ratio < 0.3:
            notes.append(
                f"⚠️ 同場戲內只有 {linked}/{same_scene_pairs} 組相鄰鏡頭接續（{ratio:.0%}）。"
                f"接續能減少跳接感、也能降低重拍率，可以考慮再多接幾組。"
            )

    # ---- 節奏：有沒有長短對比 ----
    durations = [s.total_seconds for s in shots]
    longest = max(durations)
    if longest <= 8 and len(shots) >= 6:
        notes.append(
            f"⚠️ {len(shots)} 個鏡頭全部在 8 秒以內，**沒有用到續拍**。"
            f"整段沒有長鏡呼吸的空間，節奏會顯得平。"
            f"情緒段落（沉默、凝視、等待）適合用 15 秒的續拍鏡頭。"
        )

    over_extended = [s.shot_id for s in shots if s.extensions > 2]
    if over_extended:
        notes.append(
            f"⚠️ 這幾鏡續拍超過 2 次，畫面會累積漂移，建議拆鏡："
            f"{'、'.join(over_extended)}"
        )

    return notes


def plan_segment(
    screenplay: Screenplay,
    segment: Segment,
    style_id: str = "photoreal",
    aspect_ratio: str = "16:9",
    lang: str = "zh",
    film_lang: str = "zh",
    format_id: str = "drama",
) -> tuple[ShotList, dict[str, Any]]:
    """替一個段落產出鏡頭表。回傳 (ShotList, 用量)。

    `lang` 決定用哪個語言房間的寫作規則與術語慣例
    （見 services/prompt_rooms.py）。
    """
    from google.genai import types

    client = _client()
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("shot_planning", "pro")
    model = pricing.get("tiers", {}).get(tier_key, {}).get(
        "model_id", "gemini-3.1-pro-preview"
    )

    scenes = [s for s in screenplay.scenes if s.scene_number in segment.scene_numbers]
    if not scenes:
        raise ValueError(f"段落「{segment.title}」找不到對應的場次")

    style = config_loader.style_preset(style_id)
    ratio = config_loader.aspect_ratio(aspect_ratio)
    # 敘事形式：跟 style 是兩個維度 ——
    # style 管「看起來像什麼」，format 管「用什麼方式講」。
    fmt = config_loader.format_preset(format_id)

    scene_blocks = []
    for s in scenes:
        scene_blocks.append(
            f"""── 第 {s.scene_number} 場（推估 {s.estimated_screen_seconds:.0f} 秒）──
地點：{s.location or '未指明'}　時間：{s.time_of_day.value}
摘要：{s.action}
在場角色：{'、'.join(s.characters_present) or '無'}

原文：
{s.raw_text}"""
        )

    # ---- 主引擎的長度制式（Omni 10 秒制）----
    #
    # Omni Flash 每鏡固定 ~10 秒且不可調（實測）。10 秒制的好處：
    # 「分鏡數目就會降比較少，出錯率就降低」—— 鏡頭少、接縫少、錯誤面小。
    # Veo 檔位當主引擎時維持 4/6/8 自由制。
    _default_cfg = config_loader.veo_tier(config_loader.default_veo_tier())
    fixed_seconds = (
        int(_default_cfg.get("fixed_seconds", 0))
        if _default_cfg.get("engine", "veo") == "omni"
        else 0
    )
    duration_rule = (
        f"• 🔴 每一鏡固定 {fixed_seconds} 秒（引擎硬性規格，不能長也不能短）。"
        f"用**較少、較長**的鏡頭覆蓋整段：把連續的動作與對白合併在同一鏡，"
        f"不要拆碎。鏡頭數 ≈ 總長 ÷ {fixed_seconds}"
        if fixed_seconds
        else "• 鏡頭長度只能是 4、6 或 8 秒"
    )

    prompt = f"""把以下場次拆成鏡頭表。

作品：《{screenplay.title}》
段落：{segment.title}
目標總長：約 {segment.estimated_seconds:.0f} 秒
影片風格：{style.get('label')}（{style.get('one_liner', '')}）
敘事形式：{fmt.get('label')}（{fmt.get('one_liner', '')}）
畫面比例：{ratio.get('label')}　{ratio.get('hint', '')}

── 這個敘事形式怎麼分鏡 ──
{' '.join(str(fmt.get('planning_hint', '')).split())}
鏡頭語言：{' '.join(str(fmt.get('camera_hint', '')).split())}
🔴 voice_role 怎麼判：{' '.join(str(fmt.get('voice_hint', '')).split())}

{chr(10).join(scene_blocks)}

要求：
• 總長度盡量接近 {segment.estimated_seconds:.0f} 秒
{duration_rule}
• 鏡頭數量依內容決定，不要為了湊時間硬加鏡頭
• 沒有實體空間的場景（虛擬世界、系統內部、內心戲）記得轉成具體可拍的畫面"""

    # 帶著記憶做事：規劃時把過去實測學到的規律一起放進提示詞。
    #    memory_block 是實測統計提煉出來的規律，沒有資料時回空字串 ——
    #    冷啟動、ClickHouse 掛掉、樣本不足，三種情況都是照原本的方式跑。
    #    附在 system instruction **後面**而不是前面：
    #    前段是規則（必須遵守），後段是經驗（參考用），
    #    模型對前段服從度高，順序本身就在表達兩者的優先級。
    config = types.GenerateContentConfig(
        system_instruction=(
            prompt_rooms.system_instruction("shot_planner", lang)
            + refinery.memory_block(style_id)
        ),
        response_mime_type="application/json",
        response_schema=ShotPlanResult,
        temperature=0.4,  # 分鏡需要一點創意，但不能天馬行空
        max_output_tokens=32_768,
    )

    # 串流：長輸出用非串流會被連線切斷
    chunks: list[str] = []
    last = None
    for chunk in client.models.generate_content_stream(
        model=model, contents=prompt, config=config
    ):
        last = chunk
        if chunk.text:
            chunks.append(chunk.text)

    result = ShotPlanResult.model_validate_json("".join(chunks))

    # ---- 轉成系統的 Shot ----
    shots: list[Shot] = []
    previous_id: str | None = None
    warnings: list[str] = []

    for ps in result.shots:
        shot_id = f"s{ps.scene_number:03d}_{ps.order_in_scene:02d}"

        # 防呆：秒數是引擎的硬性物理限制，不能靠 prompt 保證，code 層夾住。
        duration = ps.base_duration_seconds
        if fixed_seconds:
            # Omni 10 秒制：不管模型回什麼一律定值（不逐鏡記警告，收一條總註記）
            duration = fixed_seconds
        else:
            if duration not in (4, 6, 8):
                nearest = min((4, 6, 8), key=lambda v: abs(v - duration))
                warnings.append(
                    f"{shot_id}：模型給了 {duration} 秒（不合法），已調整為 {nearest} 秒"
                )
                duration = nearest

            # 鏈頭 + 有角色 → 一律 8 秒（Veo 專用，）。
            #    Veo 參考圖只能在「8 秒、不接幀」的鏡頭進入，
            #    鏈頭不是 8 秒，整條鏈就沒有角色錨。模型只遵守一半，code 夾住。
            #    Omni 制不需要 —— 它每一鏡都能帶參考圖。
            if (
                not ps.continues_from_previous
                and ps.characters_in_frame
                and duration < 8
            ):
                warnings.append(
                    f"{shot_id}：鏈頭鏡頭帶角色，{duration} 秒改為 8 秒"
                    f"（角色參考圖只能從 8 秒鏈頭進入，後續鏡頭靠接幀繼承）"
                )
                duration = 8

        shot_type = ps.shot_type.upper()
        if shot_type not in _SHOT_TYPES:
            warnings.append(f"{shot_id}：未知鏡別「{ps.shot_type}」，改用 MEDIUM")
            shot_type = "MEDIUM"

        move = ps.camera_move.upper()
        if move not in _MOVES:
            warnings.append(f"{shot_id}：未知運鏡「{ps.camera_move}」，改用 STATIC")
            move = "STATIC"

        flags = []
        for f in ps.content_flags:
            if f in _FLAGS:
                flags.append(ContentFlag(f))
            elif f:
                warnings.append(f"{shot_id}：未知風險標記「{f}」，已忽略")

        shots.append(
            Shot(
                shot_id=shot_id,
                scene_number=ps.scene_number,
                order_in_scene=ps.order_in_scene,
                base_duration_seconds=duration,  # type: ignore[arg-type]
                extensions=max(0, min(20, ps.extensions)),
                shot_type=ShotType(shot_type),
                camera_move=CameraMove(move),
                description=ps.description_zh,
                visual_prompt=ps.visual_prompt_en,
                characters_in_frame=ps.characters_in_frame,
                dialogue=ps.dialogue or None,
                # speaker 欄位 才接上管線：base.yaml 的規則（「有台詞的
                #    鏡頭一定要填 speaker」）早就寫了，但輸出 schema 一直沒有這個欄位，
                #    模型想遵守也交不出來 —— 音色注入與鎖定因此整條沒觸發過。
                #    規則寫了不夠，要檢查 schema 收不收得到。
                speaker=(ps.speaker or "").strip() or None,
                # 認不得的值一律退回 character，不要讓一個拼錯的字串
                #    穿過 Literal 驗證讓整份分鏡表作廢（agent 是機率性輸出）
                voice_role=(
                    ps.voice_role
                    if ps.voice_role in ("character", "offscreen", "narrator")
                    else "character"
                ),
                sfx=ps.sfx or None,
                music_cue=ps.music_cue or None,
                content_flags=flags,
                first_frame_from=previous_id if ps.continues_from_previous else None,
            )
        )
        previous_id = shot_id

    if fixed_seconds:
        warnings.append(
            f"主引擎為 Omni（固定 {fixed_seconds} 秒制），"
            f"全部 {len(shots)} 鏡已統一為 {fixed_seconds} 秒。"
        )

    shot_list = ShotList(
        screenplay_title=f"{screenplay.title} — {segment.title}",
        shots=shots,
        style_id=style_id,
        aspect_ratio=aspect_ratio,
        # 這一行從來沒寫過，於是分鏡明明用英文寫、
        #    ShotList.prompt_language 卻永遠停在 schema 預設的 "zh"。
        #    症狀：提示詞面板標「中文 目前」但列出來的全是英文，
        #    使用者會按下 EN 再改寫一次（多花錢、多等一趟，而且結果一樣）。
        #    **用了某個設定就要把它記下來** —— 沒記下來的設定，
        #    下游只能靠猜，而猜錯不會報錯。
        prompt_language=lang,
        film_language=film_lang,
        # Omni 制不做節奏稽核 —— audit_rhythm 檢查的是接續比例與長鏡比例，
        # 那套標準是 4/6/8 自由制的，10 秒定長制下全是誤報
        planning_notes=result.notes
        + warnings
        + ([] if fixed_seconds else audit_rhythm(shots)),
    )

    # ---- 用量 ----
    meta = getattr(last, "usage_metadata", None)
    p_tok = getattr(meta, "prompt_token_count", 0) or 0
    o_tok = getattr(meta, "candidates_token_count", 0) or 0
    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    cost = (p_tok / 1_000_000) * float(cfg.get("input_per_1m_usd", 2.0)) + (
        o_tok / 1_000_000
    ) * float(cfg.get("output_per_1m_usd", 12.0))

    return shot_list, {
        "model": model,
        "prompt_tokens": p_tok,
        "output_tokens": o_tok,
        "cost_usd": round(cost, 4),
        "adjustments": warnings,
    }
