"""選角 agent —— 把劇本裡的角色變成三張可以用眼睛選的候選圖。

設計理由（第 5 關）：
    候選人物圖便宜、可以反覆重抽；使用者選定滿意的之後才進影片生成。

    成本階梯：角色圖 $0.02 / 8 秒鏡頭 $0.80 / 3 分鐘片 $18。
    角色長相是所有鏡頭的地基，所以把猶豫留在最便宜的階段。

為什麼是兩階段（文字 → 圖），不是直接叫圖片模型畫三張：
    直接讓圖片模型生三張，會得到三張幾乎一樣的臉 —— 它沒有理由把它們拉開。
    使用者看不出差別，這一關就等於只有一個選項，白花錢。

    所以先用 Gemini Pro 想出三個沿著明確軸線分開的詮釋
    （保守／平衡／大膽，或具象／風格化／抽象），每個詮釋產出一組英文外貌標籤，
    再讓圖片模型照著畫。三張圖的差異因此是可解釋的 ——
    UI 上每張圖底下都能寫出「這張是什麼方向」。

分工（跟 prompt_export 同一套原則）：
    創意的部分（角色長什麼樣）給 agent，組裝給 code。
    構圖、風格前綴、方向修飾、負面約束全部由這個檔案組裝，
    agent 只回傳 appearance_tags。這樣三張候選的差異只來自角色詮釋本身，
    不會混進構圖的隨機性 —— 使用者比較的是「這三個人」，不是「這三張照片」。

成本：
    文字階段約 $0.005（Pro，輸入短、輸出短）
    圖片階段 3 × $0.02 = $0.06
    一輪合計約 $0.065
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.casting import (
    CastingSession,
    CharacterCandidate,
    CharacterKind,
    InferenceSource,
)
from ..services import rates
from ..services import api_retry, config_loader, media_store, prompt_rooms

# ---------------------------------------------------------------------------
# 模型的輸出契約
# ---------------------------------------------------------------------------
#
# 只要角色本身的視覺資訊。構圖與風格不在這裡 —— 那是 code 的事。


class _Interpretation(BaseModel):
    """一種角色詮釋。"""

    interpretation: str = Field(
        description="這個詮釋方向，一句人話。UI 顯示在圖片下方讓使用者分辨三張的差別。"
    )
    appearance_tags: str = Field(
        description=(
            "英文外貌標籤，逗號分隔。人物以外觀族裔＋性別＋年齡帶開頭（'East Asian woman, late 20s, …'），"
            "族裔照劇本的時空背景推，三個候選一致。"
            "只寫不隨鏡頭改變的視覺特徵："
            "五官、髮型髮色、體型、常穿的服裝。不要寫名字、性格、表情、場景。"
        )
    )
    voice_tags: str = Field(
        description=(
            "英文音色描述，一句。寫年齡帶、性別感、音質、語速。"
            "非人角色也要有聲音 —— 想想它在劇中如何被聽見。"
            "例：'a young woman in her twenties, soft calm voice, gentle unhurried pacing'"
        )
    )
    inference_source: str = Field(
        description=(
            "這組外貌是根據什麼推的，只能是："
            "explicit_description / dialogue_reference / age_signal / "
            "role_function / tone_of_voice"
        )
    )
    inference_note: str = Field(
        description="具體說明根據什麼推的。例如「劇本第 371 行的外貌描述」。"
    )


class _DesignResult(BaseModel):
    candidates: list[_Interpretation]


# ---------------------------------------------------------------------------
# 內部工具
# ---------------------------------------------------------------------------


def _client():
    """Vertex genai client。timeout 統一在 services/genai_client 管。

    選角生圖：實測 p50 110.7 秒。
    timeout 不能寫死成跟 Cloud Run 請求上限一樣的值 —— 那等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.LONG_MS, action="角色詮釋")


class _NoImageModel(Exception):
    """設定頁沒有可用的生圖模型 —— 不是失敗，是這一輪只出文字候選。"""


def _text_sees_images() -> bool:
    """寫詮釋的那個 AI 看不看得到圖。Nemotron 只吃文字 —— 有上傳的臉也不能塞給它（轉接會停下來報錯）。"""
    from ..services import settings_store

    return settings_store.role_for("角色詮釋")[1].get("provider") == "google"


def _image_client() -> tuple[Any, str]:
    """生角色圖用誰，回傳 (client, 不生圖的理由)。

    - 設定頁「生角色圖」選 Google → Google 的 client（使用者自己的金鑰）
    - 選本機 → "local"（Qwen-Image 2512 走使用者的 ComfyUI，見 engines/local_image.py）
    - 本機生不了（ComfyUI 沒開／在忙／缺模型）或沒設 → (None, 理由)：這一輪只出文字候選。
      這不是錯 —— 本機 H3 只吃文字提示詞，外貌標籤＋音色就是它要的全部。
    """
    from ..services import genai_client, settings_store

    provider = settings_store.role_for("生成角色候選圖")[1].get("provider")
    if provider == "google":
        return genai_client.client(genai_client.LONG_MS, action="生成角色候選圖"), ""
    if provider == "local":
        from ..engines import local_image

        try:
            local_image.check()
            return "local", ""
        except local_image.Unavailable as exc:
            return None, str(exc)
    return None, "no image model set for character images"


def _text_model() -> str:
    """文字階段用哪顆模型。

    走 pricing.yaml 的 assignments，跟劇本解析同一條規則 ——
    角色詮釋要讀懂潛台詞與人物關係，屬於「需要通盤理解」那一類 ——
    這類工作派給 Pro，格式明確的產出（提示詞、翻譯）才交給 Flash。
    """
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("character_design", "pro")
    tiers = pricing.get("tiers", {})
    return tiers.get(tier_key, {}).get("model_id", "gemini-3.1-pro-preview")


def _image_model(tier: str = "fast") -> tuple[str, float]:
    """圖片階段用哪顆模型，回傳 (model_id, 單張成本)。

    預設 fast 檔 —— 候選圖是拿來比較方向的，不需要最高畫質。
    選定之後才用 standard 重生一張正式參考圖（casting.yaml 的
    upgrade_principal_to_standard）。
    """
    cfg = config_loader.pricing().get("imagen", {}).get("tiers", {}).get(tier, {})
    return (
        cfg.get("model_id", "gemini-3.1-flash-image"),
        float(cfg.get("per_image_usd", 0.02)),
    )


def _reference_shot() -> dict[str, Any]:
    return config_loader.casting().get("reference_shot", {})


def _direction(direction_id: str) -> dict[str, Any]:
    """找方向按鈕的設定。找不到回空 dict（等同沒有修飾詞）。"""
    for item in config_loader.casting().get("directions", []):
        if item.get("id") == direction_id:
            return item
    return {}


def _slug(name: str) -> str:
    """角色名 → 檔名安全的短碼。

    角色名可能是中文、可能帶標點（「監管AI.CTRL-7」）。
    直接當檔名在跨平台與 URL 上都會出事，所以一律轉成 hash 短碼。
    同一個名字永遠得到同一個短碼，方便對照與除錯。
    """
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "", name)[:8].lower()
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"{ascii_part}{digest}" if ascii_part else digest


# ── 身份參考子句（英文，直接進提示詞；風格前綴等其他段照舊）────────────────
# 為什麼寫死在 code 而不是 casting.yaml：這不是可調的「方向」，是身份層的硬規則 ——
# 換哪個風格、哪個方向都一樣要保臉。可調的東西才進 config。
_IDENTITY_IMAGE_CLAUSE = (
    "The attached image is the IDENTITY REFERENCE for this character. "
    "Keep the same person: same facial structure, eyes, nose, mouth, age impression, "
    "skin tone and body type — recognisably the same individual. "
    "Do NOT copy the reference photo's background, clothing, pose, lighting or camera; "
    "re-render this person in the visual style, framing and wardrobe described next."
)
_LOOK_VARIANT_CLAUSE = (
    "This is a WARDROBE / STYLING VARIANT of an already-cast character: "
    "change only clothing, accessories and hairstyle as directed; "
    "face and body must stay identical to the reference."
)
_IDENTITY_TEXT_NOTE = (
    "An identity reference photo of this character is attached. "
    "Base every appearance description on the person in the photo "
    "(face, hair, age, build, skin tone); ignore the photo's clothing and background — "
    "wardrobe and styling are yours to propose for this film's style. "
    "If the screenplay's description conflicts with the photo, the photo wins for identity."
)


def _build_image_prompt(
    tags: str,
    style_id: str,
    direction_id: str,
    kind: CharacterKind = CharacterKind.HUMAN,
) -> str:
    """組出送給圖片模型的完整提示詞。

    順序有講究（實測：模型對前段服從度較高）：
        [風格前綴][參考圖構圖][角色外貌標籤][方向修飾][風格額外注入][負面約束]

    角色標籤放在構圖之後、修飾之前 —— 構圖決定「這是一張什麼圖」，
    標籤決定「圖上是誰」，修飾是這一輪要往哪偏。負面約束一律最後。

    構圖依角色類型分開：
       人物用的構圖詞（one person / waist up / head and shoulders）
       套在沒有身體的 AI 上，模型會畫出「一個人捧著那個東西」——
       因為那是唯一能同時滿足「一個人的半身像」與「羽毛形態的存在」的答案。
       非人角色另外一組構圖，並額外強調「畫面裡不要有人」。
    """
    style = config_loader.style_preset(style_id)
    ref = _reference_shot()
    direction = _direction(direction_id)
    is_non_human = kind is CharacterKind.NON_HUMAN

    framing = ref.get(
        "framing_non_human_en" if is_non_human else "framing_human_en", ""
    )

    negative = ref.get("negative_en", "").strip()
    if is_non_human:
        negative = f"{negative} {ref.get('negative_non_human_en', '').strip()}".strip()

    parts = [
        style.get("prompt_prefix", "").strip(),
        framing.strip(),
        tags.strip(),
        direction.get("prompt_modifier", "").strip(),
        style.get("extra_inject", "").strip(),
        negative,
    ]
    return " ".join(p for p in parts if p)


def _user_prompt(
    session: CastingSession,
    style_id: str,
    direction_id: str,
    context_excerpt: str,
    previous_tags: list[str],
) -> str:
    """組 user prompt。

    給模型的東西刻意精簡：角色資訊 + 一小段劇本語境。
    不整份劇本丟進去 —— 一份 40K token 的劇本每抽一輪就重讀一次，
    抽十輪就是 400K token 的浪費，而選角需要的資訊其實只有幾百字。
    """
    ref = _reference_shot()
    direction = _direction(direction_id)
    count = int(config_loader.casting().get("candidates_per_round", 3))

    kind_note = (
        "這是一個**沒有實體身體**的角色（AI／系統／抽象存在）。"
        "要給它一個在劇中說得通的視覺形體 —— 想想它如何被感知，"
        "不要偷懶畫成人形機器人。"
        if session.kind is CharacterKind.NON_HUMAN
        else "這是一個有血有肉的人物。"
    )

    lines = [
        f"角色名稱：{session.character_name}",
        f"角色定位：{'主要角色' if session.is_principal else '配角'}",
        kind_note,
    ]

    if session.source_description:
        lines.append(
            f"🔴 劇本明確寫了外貌，**必須照著寫，不要改**：\n{session.source_description}"
        )
    else:
        lines.append(
            "劇本沒有寫這個角色的外貌 —— 這很常見，不是錯誤。"
            "請從間接線索推導，並老實標明你根據的是什麼。"
        )

    if context_excerpt:
        lines.append(f"\n── 劇本語境（節錄）──\n{context_excerpt}")

    if previous_tags:
        # 重抽時把前幾輪的標籤列出來，明確要求避開。
        # 沒有這一段，模型會反覆生出高度相似的結果 ——
        # 使用者按了五次「換一批」還是同一張臉，那正是這個關卡最糟的失敗模式。
        joined = "\n".join(f"・{t}" for t in previous_tags[-9:])
        lines.append(
            f"\n── 前幾輪已經出現過的方向（請明確避開，不要再給類似的）──\n{joined}"
        )

    if direction.get("prompt_modifier"):
        lines.append(
            f"\n── 這一輪的方向 ──\n"
            f"{direction.get('label', direction_id)}："
            f"{direction.get('hint', '')}"
        )

    style = config_loader.style_preset(style_id)
    lines.append(
        f"\n── 影片風格 ──\n{style.get('label', style_id)}。"
        f"外貌標籤要寫得適合這個畫風（例如動畫風不需要描述毛孔與皮膚質地）。"
    )

    lines.append(f"\n{ref.get('spread_axis_en', '')}")
    lines.append(
        f"\n請給 {count} 個詮釋。"
        f"appearance_tags 每個不超過 "
        f"{ref.get('max_appearance_tag_words', 24)} 個英文單字。"
    )

    return "\n".join(lines)


_GENDER_WORDS = re.compile(
    r"\b(woman|women|man|men|girl|boy|female|male|lady|gentleman|person|child|kid|teen(?:ager)?)\b", re.I
)
# 聲音描述的開頭：「a woman in her late 20s」「an elderly man」「a teenage girl」
_VOICE_HEAD = re.compile(
    r"^\s*an?\s+(?:(?P<adj>young|elderly|older|middle-aged|teenage)\s+)?"
    r"(?P<who>woman|man|girl|boy|lady|gentleman|child|teen(?:ager)?)"
    r"(?:\s+in\s+(?:her|his|their)\s+(?P<age>[a-z\- ]*\d0s))?",
    re.I,
)


def _ensure_identity_head(tags: str, voice: str) -> str:
    """人物的外貌標籤一定要有性別：沒有就從它自己的聲音描述補到最前面。

    實測：Nemotron 給 Lisa 的三組外貌標籤都沒寫性別（聲音描述明明寫了「a woman in her late 20s」），
    生圖模型自己猜 → 一張畫成男生。這串標籤之後會注入每一鏡，漏性別等於整部片都在賭。
    補的是模型自己寫的資訊（同一個候選的聲音描述），不是程式猜的；聲音也沒寫就原樣留著。
    """
    if _GENDER_WORDS.search(tags or ""):
        return tags
    m = _VOICE_HEAD.search(voice or "")
    if not m:
        return tags
    head = " ".join(x for x in (m.group("adj"), m.group("who")) if x).lower()
    if m.group("age"):
        head += ", " + m.group("age").strip().lower()
    return f"{head}, {tags}" if tags else head


def _parse_source(value: str) -> InferenceSource:
    """模型回的字串 → enum。認不得就當「劇情功能」。

    退回 role_function 而不是拋錯：來源標籤只影響 UI 上的可信度說明，
    為了一個標籤讓整輪 $0.06 的生成失敗不划算。
    """
    try:
        return InferenceSource(value.strip().lower())
    except ValueError:
        return InferenceSource.ROLE_FUNCTION


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def design_round(
    project_id: str,
    session: CastingSession,
    *,
    style_id: str = "photoreal",
    direction_id: str = "initial",
    lang: str = "zh",
    context_excerpt: str = "",
    image_tier: str = "fast",
    project_aspect: str = "16:9",
    identity_image: tuple[bytes, str] | None = None,
    look_segment: int | None = None,
) -> tuple[list[CharacterCandidate], dict[str, Any]]:
    """生一輪候選圖。回傳 (候選清單, 用量統計)。

    這個函式**不動 session 的狀態** —— 加進去是 casting.add_round 的事。
    理由：閘門規則（鎖定後不能重抽）集中在 casting service 一個地方，
    agent 只負責產出。分開之後，測試 agent 不需要準備一個合法的 session 狀態。

    Args:
        project_id: 決定圖片存在哪個子目錄
        session: 這個角色的選角過程（帶劇本描述、類型、已抽過的輪次）
        style_id: 影片風格，會改變畫風前綴
        direction_id: 這一輪按的方向按鈕
        lang: 介面語言，決定 interpretation 用什麼語言寫
        context_excerpt: 劇本語境節錄，通常是這個角色的幾段台詞
        image_tier: 圖片檔位，預設 fast
        identity_image: 身份參考圖 (bytes, mime)。有值就把它當圖片輸入餵給
            詮釋模型與圖片模型 —— 臉照這張、造型照風格與方向重畫。
            純文字生圖的話，連「換服裝」都是拿上一輪的文字標籤重畫，臉會跑。
            臉是輸入、造型是產出，這條線從這裡開始。
        look_segment: 有值 = 這一輪是第 N 段的造型變體（只換衣服／髮型），
            提示詞會明講「同一個人、只改造型」。
    """
    from google.genai import types

    client = _client()
    image_client, skip_reason = _image_client()
    text_model = _text_model()
    image_model, per_image = _image_model(image_tier)
    count = int(config_loader.casting().get("candidates_per_round", 3))
    round_number = session.rounds_used + 1
    ref_cfg = _reference_shot()

    # 節流走自適應調節，不用固定間隔：
    #    真正的配額查不到 —— Monitoring 沒有這個 metric、
    #    Service Usage 的配額清單裡也沒有生成模型。
    #    所以固定值永遠是猜的。改成讓它自己撞、自己學（見 rate_governor）。

    # ---- 階段一：想出三個詮釋 ----
    previous_tags = [c.appearance_tags for c in session.all_candidates()]

    config = types.GenerateContentConfig(
        system_instruction=prompt_rooms.system_instruction("character_designer", lang),
        response_mime_type="application/json",
        response_schema=_DesignResult,
        # 溫度比劇本解析高：這一步要的是分得開的三個提案，
        #    不是唯一正確答案。溫度太低會讓三個詮釋互相靠攏，
        #    正好是這個關卡最不能出現的失敗模式。
        temperature=0.9,
        max_output_tokens=4_096,
    )

    text_prompt = _user_prompt(
        session, style_id, direction_id, context_excerpt, previous_tags
    )
    # 有身份參考圖時，詮釋也要看著這張臉寫 —— 不然劇本說「白髮老太太」、
    # 上傳的是三十歲的照片，外貌標籤跟圖打架，下游提示詞會兩套外貌並存。
    # 寫詮釋的 AI 看不到圖（Nemotron）→ 只給劇本文字；臉留給生圖那一步，或使用者自己對照。
    if identity_image and _text_sees_images():
        text_contents: Any = [
            types.Part.from_bytes(data=identity_image[0], mime_type=identity_image[1]),
            _IDENTITY_TEXT_NOTE + "\n\n" + text_prompt,
        ]
    else:
        text_contents = text_prompt

    response = api_retry.call(
        lambda: client.models.generate_content(
            model=text_model,
            contents=text_contents,
            config=config,
        ),
        label="character_design:text",
    )

    if not response.text:
        raise RuntimeError("角色詮釋模型沒有回傳任何內容")

    design = _DesignResult.model_validate_json(response.text)
    interpretations = design.candidates[:count]
    if not interpretations:
        raise RuntimeError("角色詮釋模型沒有給出任何候選")
    if session.kind is CharacterKind.HUMAN:
        for item in interpretations:
            item.appearance_tags = _ensure_identity_head(item.appearance_tags, item.voice_tags)

    # ---- 階段二：照著畫 ----
    candidates: list[CharacterCandidate] = []
    image_failures: list[str] = []

    for index, item in enumerate(interpretations, 1):
        candidate_id = f"{_slug(session.character_name)}-r{round_number}-{index}"
        prompt = _build_image_prompt(
            item.appearance_tags, style_id, direction_id, session.kind
        )
        if identity_image:
            # 身份子句放最前面（模型對前段服從度較高，同 _build_image_prompt 的理由）
            prompt = (
                _IDENTITY_IMAGE_CLAUSE
                + (" " + _LOOK_VARIANT_CLAUSE if look_segment is not None else "")
                + " " + prompt
            )

        image_uri = ""
        cost = 0.0
        try:
            if image_client is None:
                raise _NoImageModel
            if image_client == "local":
                # 本機 Qwen-Image 2512（$0）。圖照專案比例（同下面 Google 那條的理由）。
                from ..engines import local_image

                data, mime, _info = local_image.generate(
                    prompt,
                    aspect=(
                        project_aspect
                        if ref_cfg.get("aspect_ratio", "follow_project") == "follow_project"
                        else ref_cfg.get("aspect_ratio")
                    ),
                    prefix=f"greenlight/{project_id}/{candidate_id}",
                )
                cost_each = 0.0
            else:
                # 節流 + 重試（實測必要）：
                #    連續打三次圖片生成，第三次會回 429 RESOURCE_EXHAUSTED ——
                #    不是額度用完，是每分鐘請求數的配額。
                img_response = api_retry.governed(
                    image_model,
                    # `prompt=prompt` 綁定當前值，不要靠 lambda 抓外層變數 ——
                    #    重試發生在迴圈的下一次之後時，閉包抓到的會是別張圖的提示詞。
                    lambda prompt=prompt: image_client.models.generate_content(
                        model=image_model,
                        # 有身份參考就走「圖 + 文」：圖是臉、文是造型。
                        #    generateContent 的圖片模型吃多模態輸入（實測）。
                        contents=(
                            [
                                types.Part.from_bytes(
                                    data=identity_image[0], mime_type=identity_image[1]
                                ),
                                prompt,
                            ]
                            if identity_image
                            else prompt
                        ),
                        # 圖片生成走 generateContent + responseModalities（實測）。
                        #    舊的 Imagen `:predict` + instances/parameters 路徑不可用 ——
                        #    us-central1 的 publisher models 清單裡一個 imagen-* 都沒有。
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE"],
                            # 比例跟著專案的影片比例：
                            #    參考圖會送進生成（Veo referenceImages、
                            #    Omni image input），Omni 實測會跟著參考圖方向走 ——
                            #    3:4 直式圖把 16:9 專案的影片帶成直式。
                            #    config 寫 follow_project 就用專案比例；
                            #    寫具體值（"3:4"）則固定用那個比例。
                            image_config=types.ImageConfig(
                                aspect_ratio=(
                                    project_aspect
                                    if ref_cfg.get("aspect_ratio", "follow_project")
                                    == "follow_project"
                                    else ref_cfg.get("aspect_ratio")
                                ),
                                image_size=ref_cfg.get("image_size", "1K"),
                            ),
                        ),
                    ),
                    label=f"image:{candidate_id}",
                )
                data, mime = _extract_image(img_response)
                cost_each = per_image
            image_uri = media_store.save(project_id, candidate_id, data, mime)
            cost = cost_each
        except _NoImageModel:
            pass  # 刻意不生圖：候選標 image_skipped，UI 顯示「文字版」而不是「沒生出來」
        except Exception as exc:  # noqa: BLE001
            # 一張圖失敗不能讓整輪失敗：其他兩張已經花錢生出來了，
            #    整批丟掉等於白燒。缺的那張在 UI 上顯示為生成失敗，
            #    使用者仍然可以從成功的候選裡選。
            image_failures.append(f"{candidate_id}: {exc}")

        candidates.append(
            CharacterCandidate(
                candidate_id=candidate_id,
                character_name=session.character_name,
                round_number=round_number,
                image_uri=image_uri,
                prompt_used=prompt,
                interpretation=item.interpretation,
                appearance_tags=item.appearance_tags,
                voice_tags=item.voice_tags,
                inference_source=_parse_source(item.inference_source),
                inference_note=item.inference_note,
                cost_usd=cost,
                image_skipped=image_client is None,
            )
        )

    usage = _usage_stats(response, text_model)
    generated = sum(1 for c in candidates if c.image_uri)
    usage["images_generated"] = generated
    usage["image_model"] = "local qwen-image-2512" if image_client == "local" else image_model
    # 照每張實際記的錢加總（本機是 $0），不要用張數 × 雲端單價
    usage["image_cost_usd"] = round(sum(c.cost_usd for c in candidates), 4)
    usage["total_cost_usd"] = round(
        usage.get("cost_usd", 0.0) + usage["image_cost_usd"], 4
    )
    if image_failures:
        usage["image_failures"] = image_failures
    if image_client is None:
        usage["images_skipped"] = skip_reason

    return candidates, usage


def _extract_image(response: Any) -> tuple[bytes, str]:
    """從 generateContent 回應裡挖出圖片位元組。

    回應結構：candidates[].content.parts[].inline_data.{data, mime_type}
    parts 裡可能混著文字說明，所以要遍歷找第一個帶 inline_data 的，
    不能寫死 parts[0]。
    """
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            blob = getattr(part, "inline_data", None)
            data = getattr(blob, "data", None)
            if data:
                return data, getattr(blob, "mime_type", "image/png") or "image/png"

    raise RuntimeError(
        "回應裡沒有圖片。檢查 responseModalities 是否為 ['IMAGE']，"
        "以及該模型在 global endpoint 是否可用。"
    )


def _usage_stats(response: Any, model: str) -> dict[str, Any]:
    """算文字階段花了多少。

    硬預算下，每一次呼叫都要能說出花了多少 ——
    沒有這個，「還剩多少可以用」就只能用猜的。
    """
    meta = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0

    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    in_rate = float(cfg.get("input_per_1m_usd", 2.0))
    out_rate = float(cfg.get("output_per_1m_usd", 12.0))

    cost = (prompt_tokens * in_rate + output_tokens * out_rate) / 1_000_000

    return {
        "model": model,
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 4),
    }
