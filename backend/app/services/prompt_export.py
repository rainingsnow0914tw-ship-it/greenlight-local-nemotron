"""提示詞組裝與導出。

設計理由：
    選好角色之後先出完全部提示詞，使用者可以拿去別的工具生成，然後才有下一步。
    這一關有三個價值，按重要性排：

      1. 免費的檢查點。提示詞是純文字，產生它幾乎不花錢。
         在這裡發現「某一鏡的描述不對」，比生完整支片才發現便宜得多。

      2. 不把使用者鎖住。使用者可以拿這些提示詞去別家工具跑。

      3. 透明度。使用者看得到系統到底做了什麼、加了哪些約束、為什麼加。

組裝順序是有講究的，而且兩個引擎的講究是相反的：

Veo 3.1（備援檔位）：逗號標籤鏈，全部串成一行

    [角色標籤] [風格前綴] [鏡別/運鏡] [鏡頭描述] [物理約束] [台詞]
    [音效/音樂] [畫面人員邊界] [Negative: a, b, c]

    角色標籤放最前面：模型對提示詞前段的服從度明顯較高，
    把角色特徵放中間，角色就會飄。

    負面約束放最後面：它們是全域限制，不該打斷描述的語意連貫。

Omni Flash（主引擎）：完整自然語言敘事，逐段換行

    [風格] [攝影機在做什麼] [場景描述] [畫面裡的人長什麼樣] [物理約束]
    [台詞] [聲音] [畫面人員邊界] [Do not include…]

    官方 prompting guide 明說 Omni 是多模態推理模型，要敘事不要
    單字堆疊；也沒有 negative prompt 欄位，建議用自然語言限制句收尾。

    角色描述在這條路上改放描述之後：「標籤放最前面」是 Veo 的
    服從度技巧，Omni 的身份鎖定另有機制（參考圖 ＋ video_gen 組的
    `[character N]` 編號綁定），敘事該先講場景再講人。

兩條路共用同一批素材（約束、音色、人員邊界），差別只在外框。
不要為了敘事式去改素材本身，那些是選角與規則庫鎖定的內容。

分工原則：
    「鏡頭描述」那一段由 prompt_writer agent 產生（那是創意工作）。
    其餘全部由這個模組機械組裝（那是規則工作）。
    這樣提示詞的結構是穩定、可預測、可單元測試的：
    每次生成的差異只來自描述本身，不會因為 LLM 輸出波動就換一套結構。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..schemas.casting import CastingGate
from ..schemas.shot import ContentFlag, Shot, ShotList
from . import config_loader, constraint_engine

# 全域負面跟規則的 negative_keywords 合併成一行「Negative: a, b, c」
# 在 build_prompt 尾端組，watermarks/logos/captions 三個固定項直接寫在那裡。


def _speaker_label(shot: Shot, casting: CastingGate | None) -> str:
    """台詞的說話者要怎麼指稱。

    模型認得的是外觀不是名字。所以：
      畫面只有一個人  → "The character"（沒有歧義，不需要多說）
      畫面有多個人    → 用該角色外貌標籤的前幾個詞去指
      沒有人（旁白）  → "A voice"

    為什麼不直接用角色名：模型不知道角色名是誰，那個 token 對它
    毫無資訊量，還會把一個中文詞塞進英文提示詞中間。
    這跟 appearance_tags 放在提示詞最前面是同一個原則：
    角色一致性靠外觀描述維持，不靠名字。
    """
    # 說話者看 `speaker` 不看 `characters_in_frame`：
    # 畫外音、旁白、電話那頭，說話的人常常不在畫面上。
    # 用畫面上的角色去取，會把別人的音色套在說話者的台詞上。
    name = shot.speaker
    present = shot.characters_in_frame or []

    if not name:
        # 沒指明說話者：畫面單人時那個人說的機率最高，
        # 但仍不鎖定音色（見 _voice_label）—— 猜構圖可以，猜聲音不行。
        if len(present) == 1:
            return "The character"
        return "A voice"

    # 說話者就在畫面上、而且只有他一個 → 不需要多描述
    if present == [name]:
        return "The character"

    session = casting.session_for(name) if casting else None
    chosen = session.selected if session else None
    if chosen and chosen.appearance_tags:
        words = chosen.appearance_tags.replace(",", " ").split()
        # 標籤自己帶冠詞開頭時去掉，否則會疊成 "of the a flowing ribbon"
        # （實測看到的輸出）。
        while words and words[0].lower() in ("a", "an", "the"):
            words = words[1:]
        if words:
            # 說話者不在畫面上時，講明它是畫外音 ——
            # 否則模型可能為了「有人說話」而把他畫進來。
            prefix = "An offscreen voice of the" if name not in present else "The"
            return f"{prefix} " + " ".join(words[:6])

    # 沒選角就沒有外貌可以指 —— 退回中性說法，
    # 總比放一個模型看不懂的名字好。
    return "A voice" if name not in present else "The character"


def _people_boundary(shot: Shot) -> str:
    """這一鏡「畫面裡可以有誰」的硬邊界，從結構化欄位算出來。

    為什麼不交給 constraints.yaml：
       `exclude_unspecified_props` 規則有「不准加人」，但實測規則套用了、
       模型照樣在畫面角落補人。通用的「不要加沒列的人」對模型來說太抽象，
       它不知道「沒列的」是誰。分鏡表明確記錄了這一鏡有幾個角色，
       所以 code 可以講出模型聽得懂的具體版本：
       「這一鏡完全沒有人」或「只有上面描述的那（幾）位」。

       規則求不住的，就用結構化事實說死（能用架構解的不要用 prompt 解）。
       constraints.yaml 那條保留，它還管物件與配件。

    crowd 標記的鏡頭跳過，人群戲本來就該有背景人物。
    """
    if ContentFlag.CROWD in shot.content_flags:
        return ""
    n = len(shot.characters_in_frame or [])
    if n == 0:
        return (
            "This shot contains no people at all: no faces, no heads, "
            "no silhouettes, no partial or distant figures — not in the "
            "background, not at any edge or corner of the frame."
        )
    only = "the single character" if n == 1 else "the characters"
    return (
        f"Only {only} described above appear{'s' if n == 1 else ''} in this "
        "shot. No other people, faces, heads or silhouettes appear anywhere — "
        "not in the background, not at any edge or corner of the frame."
    )


def _voice_label(shot: Shot, casting: CastingGate | None) -> str:
    """這一鏡說話者的音色描述。

    取自角色選定候選的 `voice_tags`。沒有的話回空字串 ——
    寧可不寫，也不要編一個音色出來：編的音色每一鏡都一樣是好的，
    但它會蓋掉使用者選角時聽過（看過）的那個角色印象。

    voice_tags 欄位加入前選定的角色沒有這個值，會走到空字串分支，
    重抽一輪就會有。
    """
    # 只認 `speaker`，不從畫面上的角色猜：
    # 猜錯構圖只是畫面怪，猜錯聲音會讓角色變成另一個人，
    # 而且那個錯誤會被鎖定下來，之後每一鏡都錯。
    # 寧可不鎖，也不要鎖錯人。
    if not shot.speaker or not casting:
        return ""

    session = casting.session_for(shot.speaker)
    chosen = session.selected if session else None
    return (chosen.voice_tags or "").strip() if chosen else ""


def _engine_of(tier: str | None) -> str:
    """這個檔位走哪個引擎（omni / veo）。

    為什麼要有這個函數？
    兩個引擎要的提示詞形態是相反的（官方 prompting guide）：
      · Veo 3.1：逗號堆疊標籤（`cinematic, 4k, wide shot`）
      · Omni Flash：完整自然語言敘事，官方明說不要單字堆疊
    組裝時要知道這一鏡最後會送給誰，不能只有一種寫法。

    tier 傳 None 時走設定裡的預設檔位，呼叫端沒改也會自動走對的路。
    """
    tier = tier or config_loader.default_veo_tier()
    try:
        return str(config_loader.veo_tier(tier).get("engine", "veo"))
    except Exception:
        # 檔位查不到不該讓提示詞組不出來，退回 Veo 式（保守、資訊不會少）
        return "veo"


def _narrative_character(tags: list[str]) -> str:
    """把角色的外貌標籤串包成英文句子。

    為什麼要有這個函數？
    `appearance_tags` 是給 Veo 用的名詞短語堆
    （"young asian woman, monolids, deep brown eyes, ..."），
    直接丟給 Omni 就是官方明講要避免的標籤堆疊。
    第一個標籤幾乎都是主體（"young asian woman"），其餘是特徵，
    加一個主詞框架就成句，不需要真的做語言處理。

    只做包裝不改內容：標籤本身是選角時鎖定的那份，
    改寫它等於改掉使用者選的長相。
    """
    lines = []
    for tag in tags:
        tag = tag.strip().rstrip(".")
        if not tag:
            continue
        head, _, rest = tag.partition(",")
        head = head.strip()
        rest = rest.strip()
        # 標籤是名詞短語（"young asian woman"），當主詞補語要有冠詞。
        # 標籤自己帶冠詞時不重複加 —— 會疊成 "is a a young woman"。
        if head and not head.lower().startswith(("a ", "an ", "the ")):
            head = ("an " if head[0].lower() in "aeiou" else "a ") + head
        if rest:
            lines.append(f"One character in this shot is {head}, with {rest}.")
        else:
            lines.append(f"One character in this shot is {head}.")
    return " ".join(lines)


def _sentence(text: str) -> str:
    """把一段可能已經自帶句號的文字收成剛好一個句子。

    為什麼要有這個函數？
    `sfx` / `music_cue` 是分鏡 agent 寫的自由文字，有時自帶句號有時沒有。
    敘事式把它們包進句子（"The soundtrack carries X."）時就會變成
    "…breathing.."。標籤式看不出來（本來就是碎片），敘事式一眼就髒。
    """
    return text.strip().rstrip(".。").strip()


def build_prompt(
    shot: Shot,
    shot_list: ShotList,
    casting: CastingGate | None = None,
    include_dialogue: bool = True,
    tier: str | None = None,
) -> dict:
    """組出一個鏡頭的完整提示詞。

    include_dialogue=False 是「無聲重拍」：
    台詞句被內容安全審查擋下時（實測拿掉台詞就通過），畫面照生、台詞不進音軌，
    改由字幕層呈現。字幕是後製自己疊的，審查管不到，而且劇本一個字都不用改。

    回傳 dict 而不是字串，因為導出時需要分段呈現
    （使用者要看得出哪一段是角色、哪一段是約束）。
    完整字串在 `full` 欄位。

    依檔位的 engine 分流：官方 prompting guide 明講兩者相反，Veo 吃逗號標籤堆疊，
    Omni 是多模態推理模型、要完整自然語言敘事。Veo 那條保持原樣
    （它是備援檔位，Omni 是 Preview 無 SLA，官方也建議留 fallback）。
    兩條路的 `segments` 分段要跟 `full` 走同一個變數，否則 `full` 是新語言、
    展開看到的分段還是舊的，使用者以為送出去的是他看到的那份。
    """
    style = config_loader.style_preset(shot_list.style_id)
    cplan = constraint_engine.plan_for_shot(shot, shot_list.style_id)

    engine = _engine_of(tier)
    narrative = engine == "omni"

    parts: list[str] = []

    warnings: list[str] = []

    # ---- 1. 角色標籤（Veo：放最前面，模型對前段服從度高）----
    #
    # 用英文外貌標籤，不用角色名字。
    # 模型不需要知道角色叫什麼，需要的是可視覺化的特徵。
    #
    # Omni 走敘事式時這一段改放描述之後：
    # 「標籤放最前面」是 Veo 的服從度技巧，Omni 的身份鎖定另有機制
    #  （參考圖 + video_gen 組的 `[character N]` 編號綁定），
    #  敘事式該先講場景再講人。
    character_tags = ""
    if casting and shot.characters_in_frame:
        tags = []
        for name in shot.characters_in_frame:
            session = casting.session_for(name)
            chosen = session.selected if session else None
            if chosen and chosen.appearance_tags:
                tags.append(chosen.appearance_tags.strip().rstrip("."))
            elif chosen:
                warnings.append(
                    f"角色「{name}」缺少英文外貌標籤（appearance_tags），"
                    f"提示詞裡沒有它的外貌描述，一致性會失效"
                )
        if tags:
            character_tags = _narrative_character(tags) if narrative else ". ".join(tags)
            if not narrative:
                parts.append(character_tags + ".")

    # ---- 2. 風格前綴 ----
    style_prefix = " ".join(str(style.get("prompt_prefix", "")).split())
    if style_prefix:
        parts.append(style_prefix)

    # ---- 3. 鏡別與運鏡 ----
    #
    # Veo  ：影視術語標籤（"Medium close-up, slow pan."），它認得，而且精確
    # Omni ：同樣的意思寫成句子。官方 guide 明說它是多模態推理模型，
    #        要的是「攝影機在做什麼」而不是術語標籤。
    framing = (
        f"The camera holds {_SHOT_TYPE_NARRATIVE[shot.shot_type.value]} "
        f"and {_CAMERA_NARRATIVE[shot.camera_move.value]}."
        if narrative
        else f"{_SHOT_TYPE_EN[shot.shot_type.value]}, {_CAMERA_EN[shot.camera_move.value]}."
    )
    parts.append(framing)

    # ---- 4. 鏡頭描述（由 prompt_writer agent 產生的創意部分）----
    #
    # 有英文版就用英文版：
    # `description` 是中文（給人看），`visual_prompt` 是英文（給模型）。
    # 一段中文夾在一堆英文中間是服從度最不穩的形態，
    # 使用者拿去別的工具會誤以為是工具的問題。
    #
    # 沒填的時候退回中文而不是留白：一份帶中文描述的提示詞仍然可用，
    # 少了描述的提示詞則完全不能用。
    description = shot.visual_prompt or shot.description
    if description:
        parts.append(description)

    # Omni 敘事式：場景講完才講畫面裡的人長什麼樣（見第 1 段的說明）
    if narrative and character_tags:
        parts.append(character_tags)

    # ---- 5. 物理約束（依鏡頭內容自動選出）----
    constraint_block = cplan.injection_block
    if constraint_block:
        parts.append(constraint_block)

    # ---- 6. 台詞 ----
    #
    # 用冒號格式不用引號格式。
    # 引號會被模型理解成「要顯示的文字」而觸發字幕燒錄。
    # 這是 constraints.yaml 的 dialogue_colon_format 規則，
    # 但它是格式規則不是附加句，所以在這裡實作而不是靠注入。
    #
    # 依 `voice_role` 分流：
    # 官方 Veo prompting guide 明講 `[主體] says` 是用來觸發對嘴的語法，
    # 模型看到就會去畫面裡找人臉同步嘴型。旁白若也走這個結構，
    # 等於主動要求它讓畫面裡的角色開口。
    if shot.dialogue and include_dialogue and shot.voice_role == "narrator":
        # 旁白：不指向畫面裡的任何人，也不從選角表拿音色（它不是劇中角色）
        narrator_voice = " ".join(
            str(
                config_loader.casting()
                .get("narrator", {})
                .get("default_voice_en", "")
            ).split()
        )
        voiced = f", {narrator_voice}," if narrator_voice else ""
        parts.append(
            f"An off-screen narrator{voiced} speaks over this shot. "
            f"The narrator is not a character in the scene and never appears "
            f"on camera. The narration is: {shot.dialogue}"
        )
        # 正向描述嘴巴狀態，不用否定句：
        # 「The character's mouth is strictly closed」比 "no lip movement"
        # 這種否定寫法更有強制力。
        if shot.characters_in_frame:
            parts.append(
                "Everyone visible in the frame stays silent: mouths closed "
                "and still, no lip movement, no lip sync, nobody speaks on camera."
            )
        dialogue_line = shot.dialogue
    elif shot.dialogue and include_dialogue and shot.voice_role == "offscreen":
        # 畫外音：是某個角色在說（所以保留他的音色），但畫面上沒人該對嘴
        speaker = _speaker_label(shot, casting)
        voice = _voice_label(shot, casting)
        voiced = f", {_sentence(voice)}," if voice else ""
        parts.append(
            f"{speaker}{voiced} speaks from off camera, not visible in this "
            f"shot. The line is: {shot.dialogue}"
        )
        if shot.characters_in_frame:
            parts.append(
                "Everyone visible in the frame stays silent: mouths closed "
                "and still, no lip movement, no lip sync."
            )
        dialogue_line = shot.dialogue
    elif shot.dialogue and include_dialogue:
        # 說話者不能用角色名字：
        # 模型不知道角色名是誰，而且一個中文名夾在英文指令中間，
        # 是提示詞裡最沒有作用的那種字。
        #
        # 改用可視覺化的指代：畫面裡只有一個人時說 "The character"，
        # 有多個人時用該角色已選定的英文外貌標籤前幾個詞去指
        # （"The young asian woman with round glasses says: ..."），
        # 那才是模型認得出來的東西。
        speaker = _speaker_label(shot, casting)
        # 音色必須寫進每一鏡：
        # 每一鏡都是獨立生成的，Veo 每次自己決定配音音色，
        # 同一個角色在不同鏡會變成兩個人在講話。
        # 這不是續拍才有的問題，是跨鏡頭的預設行為。
        voice = _voice_label(shot, casting)
        # Omni 敘事式：音色用逗號插入語而不是括號，括號在敘事裡
        # 讀起來像旁註，模型容易當成可略過的補充。
        # 兩條路都不加引號：引號會被理解成「要顯示的文字」而觸發
        # 字幕燒錄（constraints.yaml 的 dialogue_colon_format）。
        if voice and narrative:
            spoken = _sentence(voice)
            spoken = spoken[:1].lower() + spoken[1:] if spoken else spoken
            dialogue_line = f"{speaker}, {spoken}, says: {shot.dialogue}"
        elif voice:
            dialogue_line = f"{speaker} ({voice}) says: {shot.dialogue}"
        else:
            dialogue_line = f"{speaker} says: {shot.dialogue}"
        parts.append(dialogue_line)

    # ---- 7. 音效與音樂 ----
    #
    # 先收集成 audio_lines 再 append：下面回傳的 `segments["audio"]`
    # 要用同一份，否則敘事式的 full 是新句子、展開看到的分段卻是
    # "Audio: …" 舊格式。
    audio_lines: list[str] = []
    if shot.sfx:
        audio_lines.append(
            f"The soundtrack carries {_sentence(shot.sfx)}."
            if narrative
            else f"Audio: {shot.sfx}."
        )
    if shot.music_cue:
        # 這裡只做組裝，不加任何補救文字（只修規則，不寫死固定的提示詞）。
        # 「有音樂又有台詞會被唱出來」這個模型缺陷，是
        # constraints.yaml 的 `music_not_sung` 規則在處理，
        # 寫在那裡才有 principle、evidence，也才會被 ClickHouse 校準。
        audio_lines.append(
            f"Underneath it all plays {_sentence(shot.music_cue)}."
            if narrative
            else f"Background music: {shot.music_cue}."
        )
    parts.extend(audio_lines)

    # ---- 8. 畫面人員硬邊界（從 characters_in_frame 算，不是求出來的）----
    people_line = _people_boundary(shot)
    if people_line:
        parts.append(people_line)

    # ---- 9. 全域負面約束＋規則的負面關鍵字包 ----
    #
    # 雙頻道的第二路：
    # 句子頻道上限降到 4 條保護注意力；禁止類規則的保護
    # 改成逗號分隔的關鍵字全收在這裡。影片模型對
    # 「Negative: a, b, c」的反應比長句 Do not 好，也省注意力。
    #
    # Omni 沒有 negative prompt 欄位（官方確認），guide 建議
    # 「自然語言限制句收尾」，所以同一批關鍵字換成一句話講。
    # 關鍵字本身不變，變的是外框。
    neg_terms = list(cplan.negative_keywords) + [
        "watermarks",
        "logos",
        "on-screen captions",
    ]
    negatives_line = (
        "Do not include any of the following anywhere in the shot: "
        + ", ".join(neg_terms)
        + "."
        if narrative
        else "Negative: " + ", ".join(neg_terms) + "."
    )
    parts.append(negatives_line)

    # 敘事式用換行分段（場景 / 人物 / 規則 / 聲音各自成段，模型比較好讀）；
    # Veo 式維持單行標籤鏈，不動。
    full = ("\n".join if narrative else " ".join)(
        p.strip() for p in parts if p.strip()
    )

    return {
        "shot_id": shot.shot_id,
        "scene_number": shot.scene_number,
        "duration_seconds": shot.total_seconds,
        "base_duration_seconds": shot.base_duration_seconds,
        "extensions": shot.extensions,
        "aspect_ratio": shot_list.aspect_ratio,
        "style": shot_list.style_id,
        "reference_image": shot.reference_image_uri,
        "first_frame_from": shot.first_frame_from,
        # 一定要 serialize 出去：欄位沒吐出來時從 API 看永遠是 None，
        # 會被誤判成 agent 沒填。UI 也要靠它顯示「這一鏡是旁白」。
        "voice_role": shot.voice_role,
        "segments": {
            "character_tags": character_tags,
            "style_prefix": style_prefix,
            "framing": framing,
            # 跟上面 parts 用同一個變數：
            # 直接讀 shot.description 的話，改寫成另一種語言之後
            # `full` 是新語言、展開看到的分段卻是舊的，
            # 使用者以為送出去的是他看到的那份，其實不是。
            # 任何「同一份資料呈現兩次」的地方都要走同一個來源。
            "description": description,
            "constraints": constraint_block,
            "dialogue": shot.dialogue or "",
            # 跟上面 parts 用同一份（見第 7 段的說明）
            "audio": " ".join(audio_lines),
            "negatives": negatives_line,
        },
        "applied_constraints": [
            {
                "rule_id": c.rule_id,
                "category": c.category_label,
                "principle": " ".join(c.principle.split()),
            }
            for c in cplan.applied
        ],
        "advisories": cplan.advisories,
        "full": full,
    }


# 影視術語的英文對照。
# Veo 對這些標準術語的服從度比自然語言描述好，
# "medium close-up" 比 "拍到胸口以上" 精確得多。
_SHOT_TYPE_EN = {
    "EXTREME_WIDE": "Extreme wide shot",
    "WIDE": "Wide shot",
    "MEDIUM": "Medium shot",
    "MEDIUM_CLOSE": "Medium close-up",
    "CLOSE_UP": "Close-up",
    "EXTREME_CLOSE": "Extreme close-up",
    "INSERT": "Insert shot",
}

_CAMERA_EN = {
    "STATIC": "static locked-off camera",
    "PAN": "slow pan",
    "TILT": "slow tilt",
    "DOLLY": "dolly move",
    "TRACKING": "tracking shot",
    "HANDHELD": "handheld camera",
    "CRANE": "crane move",
    "ZOOM": "slow zoom",
}

# Omni 敘事式用的同義表。
#
# 同樣的鏡別與運鏡，寫成「攝影機在做什麼」的完整句子而不是術語標籤：
# 官方 prompting guide 說 Omni 是多模態推理模型，敘事勝過單字堆疊。
# 特寫類不寫「臉」：物件特寫是空鏡，
# 寫成 "close-up on the subject's face" 會讓模型為了有臉而補一個人進來。
_SHOT_TYPE_NARRATIVE = {
    "EXTREME_WIDE": "an extreme wide view that takes in the whole space",
    "WIDE": "a wide view of the scene",
    "MEDIUM": "a medium shot of the main subject",
    "MEDIUM_CLOSE": "a medium close-up framed from the chest up",
    "CLOSE_UP": "a close-up on the main subject",
    "EXTREME_CLOSE": "an extreme close-up on one small detail",
    "INSERT": "a tight insert shot of a single object",
}

_CAMERA_NARRATIVE = {
    "STATIC": "stays completely still, locked off on a tripod",
    "PAN": "pans slowly across the scene",
    "TILT": "tilts slowly",
    "DOLLY": "glides smoothly on a dolly",
    "TRACKING": "tracks alongside the subject",
    "HANDHELD": "is handheld, with a subtle natural sway",
    "CRANE": "rises steadily on a crane",
    "ZOOM": "zooms in slowly",
}


def build_all(
    shot_list: ShotList,
    casting: CastingGate | None = None,
    tier: str | None = None,
) -> list[dict]:
    """組出整份鏡頭表的提示詞。

    tier 決定提示詞形態（Omni 敘事式 / Veo 標籤式）。
    導出看到的必須跟實際送出去的是同一份，否則使用者以為
    他複製走的那段就是餵給模型的那段。
    """
    return [build_prompt(shot, shot_list, casting, tier=tier) for shot in shot_list.shots]


# ---------------------------------------------------------------------------
# 導出格式
# ---------------------------------------------------------------------------

def export_plain_text(
    shot_list: ShotList,
    casting: CastingGate | None = None,
    tier: str | None = None,
) -> str:
    """純文字 —— 最通用的格式，貼到任何地方都能用。

    這是「拿去別的工具玩」的主要格式。刻意做得能直接複製單一鏡頭，
    不需要使用者自己從一大坨文字裡挑。
    """
    prompts = build_all(shot_list, casting, tier)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# {shot_list.screenplay_title}",
        f"# 風格：{shot_list.style_id} · 比例：{shot_list.aspect_ratio} · "
        f"{shot_list.clip_count} 個鏡頭 · 共 {shot_list.total_seconds} 秒",
        f"# 產生時間：{stamp}",
        "#",
        "# 這些提示詞可以直接拿到任何影片生成工具使用。",
        "# 每個鏡頭的長度限制：單次生成 4/6/8 秒，更長要用該工具的續拍功能。",
        "",
    ]

    for p in prompts:
        lines.append("=" * 72)
        header = f"[{p['shot_id']}] 第 {p['scene_number']} 場 · {p['duration_seconds']} 秒"
        if p["extensions"]:
            header += f"（{p['base_duration_seconds']} 秒 + 續拍 {p['extensions']} 次）"
        lines.append(header)
        if p["first_frame_from"]:
            lines.append(f"接續自：{p['first_frame_from']}（用該鏡末幀當首幀）")
        if p["reference_image"]:
            lines.append(f"角色參考圖：{p['reference_image']}")
        lines.append("")
        lines.append(p["full"])
        lines.append("")

    return "\n".join(lines)


def export_json(
    shot_list: ShotList,
    casting: CastingGate | None = None,
    tier: str | None = None,
) -> str:
    """JSON —— 給要串接其他工具的人用。

    保留分段結構與約束說明，這樣接收方能自己重組
    （例如只要描述不要約束句）。
    """
    payload = {
        "title": shot_list.screenplay_title,
        "style": shot_list.style_id,
        "aspect_ratio": shot_list.aspect_ratio,
        "clip_count": shot_list.clip_count,
        "total_seconds": shot_list.total_seconds,
        "api_call_count": shot_list.api_call_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "shots": build_all(shot_list, casting, tier),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_markdown(
    shot_list: ShotList,
    casting: CastingGate | None = None,
    tier: str | None = None,
) -> str:
    """Markdown —— 給人閱讀與審閱用，含每條約束的原理說明。

    這個格式是「透明度」那個價值的載體：
    使用者看得到系統加了什麼、以及為什麼加。
    """
    prompts = build_all(shot_list, casting, tier)
    style = config_loader.style_preset(shot_list.style_id)
    ratio = config_loader.aspect_ratio(shot_list.aspect_ratio)

    lines = [
        f"# {shot_list.screenplay_title} — 提示詞清單",
        "",
        f"**風格**：{style.get('label')} · **比例**：{ratio.get('label')} · "
        f"**{shot_list.clip_count} 個鏡頭** · 共 {shot_list.total_seconds} 秒 · "
        f"{shot_list.api_call_count} 次生成呼叫",
        "",
        "> 這些提示詞可以直接拿到任何影片生成工具使用。",
        "> 每個鏡頭下方列出系統自動加上的物理約束與原因 —— "
        "換工具的話，這些約束通常一樣適用。",
        "",
    ]

    for p in prompts:
        lines.append(f"## {p['shot_id']} · 第 {p['scene_number']} 場 · {p['duration_seconds']} 秒")
        lines.append("")
        if p["extensions"]:
            lines.append(
                f"- 生成方式：{p['base_duration_seconds']} 秒基底 + 續拍 {p['extensions']} 次"
            )
        if p["first_frame_from"]:
            lines.append(f"- 接續自 `{p['first_frame_from']}` 的末幀")
        if p["reference_image"]:
            lines.append(f"- 角色參考圖：`{p['reference_image']}`")
        lines.append("")
        lines.append("```")
        lines.append(p["full"])
        lines.append("```")
        lines.append("")

        if p["applied_constraints"]:
            lines.append("<details><summary>系統加了這些物理約束，以及為什麼</summary>")
            lines.append("")
            for c in p["applied_constraints"]:
                lines.append(f"- **[{c['category']}] {c['rule_id']}** — {c['principle']}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        if p["advisories"]:
            lines.append("**需要注意：**")
            for a in p["advisories"]:
                lines.append(f"- {' '.join(a.split())}")
            lines.append("")

    return "\n".join(lines)
