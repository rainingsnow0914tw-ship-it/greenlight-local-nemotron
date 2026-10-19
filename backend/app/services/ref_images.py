"""參考圖：全身卡、場景空景、首幀 —— 本機影片引擎（H3）要的圖，在使用者自己的 ComfyUI 上生。

為什麼要有這一層（提示詞和場景寫好之後，參考圖要怎麼生；真人和動畫的邏輯不一樣）：
本機 H3 可以吃參考圖（提示詞寫「就是參考圖 <Picture N> 裡的那個人／地方」，長相與擺設交給圖），
但使用者自己的專案沒有人做這些圖。這裡把它們做出來，三條規矩：

1. **臉只生一次**：選角鎖定的那張。全身卡、首幀都用 Qwen-Image-Edit 從它「改」出來，不准用文字重畫臉 ——
   真人的臉差一點觀眾就看得出來換了人。
2. **全身卡背景素色**：它要餵進影片模型，背景有東西會被當成場景帶進畫面（一件事只能有一個定義者：地點歸空景）。
3. **空景不准有人**：地點圖只管地點；人由全身卡負責。走位用到的東西（門、隔間、洗手台…）一定要在，不然後面的鏡頭沒地方演。

文字（空景描述、首幀指示）交給 Nemotron 寫（設定頁「場面調度」那一格），圖交給本機 Qwen（engines/local_image.py）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

FULL_BODY_INSTRUCTION = (
    "Show the same person from image 1 in a full-body shot: standing naturally, the whole figure visible "
    "from the top of the head down to the shoes, relaxed three-quarter stance facing the camera. "
    "Keep the face, hairstyle and top exactly the same, and complete the outfit below the waist in a matching style. "
    "Plain light grey seamless studio background, soft even lighting."
)
# 非人角色（動物、AI、精靈）：上面那句寫死「人、鞋子、補下半身衣服」—— 第三世試跑，選角選好的小貓ぷく
#    做全身卡時被畫成穿衣服、兩腳站的貓人。改成「保持它原本的物種與樣子」。
#    同日：機器人塔列昂照「保持物種」被畫成條紋貓人（盔甲不見）→ 補「材質」與「機器保持機器」。
NON_HUMAN_FULL_BODY_INSTRUCTION = (
    "Show the same creature or entity from image 1 with its whole body visible, in its own natural form and pose. "
    "Keep its species, body shape, size, markings, colours and materials exactly. If it is an animal it stays a real animal — "
    "in its natural animal posture, no clothes, no human body, no standing upright like a person. "
    "If it is a robot, machine or armoured being it stays exactly that — the same metal plates, armour and lights, "
    "never turned into fur or skin. "
    "Plain light grey seamless studio background, soft even lighting."
)
# 臉卡：選角那張原圖帶著背景（選角構圖刻意要「符合角色世界的氛圍背景」）—— 第三世試跑，
#    臉卡是森林背景，送進 H3 之後第 1 場「核心處理器」整鏡變成森林裡（空景卡明明是機房）。
#    臉卡只該管臉：同一張臉、同樣髮型與衣服，背景換成素色（跟全身卡同一種背景）。
FACE_CARD_INSTRUCTION = (
    "Show the same character from image 1, head and shoulders, facing the camera. "
    "Keep the face, hairstyle, colours and clothing exactly the same. "
    "Replace the background with a plain light grey seamless studio background, soft even lighting. Nothing else in the picture."
)
PLATE_HEAD = "An empty room with nobody in it — an architectural interior photograph."
PLATE_TAIL = ("Empty set: no people, no figures, no silhouettes, no reflections of people in mirrors or glass. "
              "Wide establishing view, everything in focus.")
PLATE_TAIL_NEAR = ("Empty set: no people, no figures, no silhouettes, no reflections of people in mirrors or glass. "
                   "Medium framing: only this section of the wall fills the frame, as the background behind someone "
                   "standing chest-up in front of it; everything in focus.")
# 每一份首幀指示後面都由程式補上（不靠寫指示的 AI 記得 —— 它看不到空景，不知道房間裡有鏡子、門在哪）。
# 實際抓過兩次：①Lisa 站在一扇空景沒有的門裡 ②鏡子照得到門、照不到站在門前的人。
PHYSICS_TAIL = (
    "Physics must be real: do not add, move, open or remove any door, opening, wall or fixture — the room stays "
    "exactly as in image 1. Every person stands on visible floor with correct scale for their distance. "
    "Mirrors, glass and other reflective surfaces show correct reflections of every person in the room, "
    "positioned consistently with where they stand and which way they face."
)


class _Plate(BaseModel):
    description_en: str = Field(description=(
        "One paragraph of English describing the empty set as a photograph would show it: architecture, fixtures, "
        "materials, light for the given time of day. Include EVERY fixture the blocking uses (doors, stalls, sinks, "
        "windows, furniture people walk to or touch), placed so the action can happen. No people, no names, no story."))


class _PlateViews(BaseModel):
    materials_en: str = Field(description=(
        "The room's finishes only, shared by every view: wall colour and tiles, floor, ceiling, light for the time of "
        "day. NO fixtures and NO positions here (no sinks, doors, mirrors, beds) — those belong to one wall only."))
    wall_a_en: str = Field(description=(
        "The wall view A faces, named by its fixtures, e.g. 'the wall with the sinks and the long mirror'. "
        "Pick the side the action mostly plays toward."))
    visible_a_en: str = Field(description=(
        "What a wide shot facing wall A shows, left to right — ONLY that wall and what stands against it. Do not "
        "mention anything on the opposite wall."))
    near_a_en: str = Field(description=(
        "What is right behind a person who stands in the middle of the room with wall A behind them, framed chest-up: "
        "the middle section of wall A only (two or three fixtures at most)."))
    wall_b_en: str = Field(description=(
        "The wall DIRECTLY OPPOSITE wall A (the camera has turned around 180 degrees), named by its fixtures. "
        "It must have different fixtures from wall A — a reverse shot that shows the same fixtures is useless."))
    visible_b_en: str = Field(description=(
        "What a wide shot facing wall B shows, left to right — ONLY that wall. None of wall A's fixtures appear."))
    near_b_en: str = Field(description="Like near_a_en, for wall B: the middle section of wall B only.")
    backs_to_a: list[str] = Field(description=(
        "From the blocking: the names of the people who have wall A BEHIND them when they face the camera "
        "(they look toward wall B). Everyone else in the scene has wall B behind them."))


# 空景描述裡出現這些字 ＝ 寫進了人或動作，生圖模型會照畫（實測：寫了洗臉，兩張空景都多一個人在洗臉）
_PEOPLE_WORDS = re.compile(
    r"\b(person|people|woman|women|man|men|girl|boy|she|he|her|his|they|someone|figure|character|"
    r"splash\w*|wash\w*|stand\w*|walk\w*|sit\w*|look\w*|lean\w*|enter\w*|exit\w*)\b", re.I)


def plate_views(dscene: dict, location_key: str, style_prefix: str) -> tuple[dict[str, dict], dict]:
    """正反打空景的提示詞：{view_id: {"faces_en", "prompt"}}，view_id ＝ A／A_near／B／B_near；另回 "_backs_to_a"。

    換機位一律用文字整段重生，不拿另一個機位的圖去改（本機管線的規矩：`--ref` 只能給同一個機位的圖；
    實測兩次——拿正打空景叫修圖模型轉 180 度或轉 90 度，它只會左右翻轉或照原構圖，連門都弄丟）。
    🔴 改：兩張共用的只留材質與光線，設施只寫在它那一面。舊版兩張都放完整房間描述（含每面牆的設施），
    千問照描述畫、忽略「鏡頭轉向對面」—— 第 61 場 A／B 是同一個角落（相關係數 0.447；正式管線真正反打 0.13～0.18）。
    遠（A、B）給全景、中景；近（A_near、B_near）＝同一面牆中間那段，給中近景、特寫（正式管線：挑機位最貼近這一鏡構圖的空景）。
    """
    loc = (dscene.get("locations") or {}).get(location_key, location_key)
    user = (f"Location: {loc}\nTime of day: {dscene.get('time', '')}\n"
            f"Blocking (only to know which fixtures must exist and which way the action plays — do NOT describe "
            f"the people or what they do): {dscene.get('blocking', '')}\n"
            f"Props: {dscene.get('props') or 'none listed'}")
    system = "You are a production designer planning shot / reverse-shot coverage of one empty film set."
    pv, usage = _ask("場面調度", system, user, _PlateViews)
    # 關卡：描述裡有人或動作的字眼 → 退回重寫一次；還有就不送（寧可停下來，不要生一張有人的空景）
    texts = lambda p: " ".join((p.materials_en, p.visible_a_en, p.visible_b_en, p.near_a_en, p.near_b_en))  # noqa: E731
    hits = sorted({m.lower() for m in _PEOPLE_WORDS.findall(texts(pv))})
    if hits:
        pv, u2 = _ask("場面調度", system, user + f"\n\nYour previous draft mentioned people or actions ({', '.join(hits)}). "
                      "Rewrite it with fixtures only.", _PlateViews)
        usage = {k: usage.get(k, 0) + u2.get(k, 0) for k in ("in", "out")}
        hits = sorted({m.lower() for m in _PEOPLE_WORDS.findall(texts(pv))})
        if hits:
            raise ValueError(f"empty-set description still mentions people or actions {hits} — not generating it")
    out = {}
    other = {"A": pv.wall_b_en.strip(), "B": pv.wall_a_en.strip()}
    for vid, wall, visible, near in (("A", pv.wall_a_en, pv.visible_a_en, pv.near_a_en),
                                     ("B", pv.wall_b_en, pv.visible_b_en, pv.near_b_en)):
        # 「空房間、沒有人」放最前面：模型對前段服從度最高（原版 character_designer 的實測），放最後壓不住
        back = f"The camera has its back to {other[vid]}, so none of that is in view."
        wide = " ".join(p for p in (PLATE_HEAD, style_prefix.strip(), "Finishes:", pv.materials_en.strip(),
                                    f"The camera faces {wall.strip()}.", back, visible.strip(), PLATE_TAIL) if p)
        close = " ".join(p for p in (PLATE_HEAD, style_prefix.strip(), "Finishes:", pv.materials_en.strip(),
                                     f"A chest-high medium shot facing {wall.strip()}.", back, near.strip(), PLATE_TAIL_NEAR) if p)
        out[vid] = {"faces_en": wall.strip(), "prompt": wide}
        out[f"{vid}_near"] = {"faces_en": wall.strip(), "prompt": close}
    out["_backs_to_a"] = list(pv.backs_to_a or [])
    return out, usage


def plate_similarity(a: bytes, b: bytes) -> float:
    """兩張空景有多像（灰階 64×36 的相關係數）。正反打該很不像：正式管線真正反打 0.13～0.18；
    第 61 場舊版同一個角落 0.447、同機位不同時間 0.569 → 門檻 PLATE_TOO_SIMILAR。"""
    import io

    import numpy as np
    from PIL import Image, ImageOps

    def sig(x):
        arr = np.asarray(ImageOps.grayscale(Image.open(io.BytesIO(x))).resize((64, 36)), dtype=np.float32)
        return (arr - arr.mean()) / (arr.std() + 1e-6)
    return float((sig(a) * sig(b)).mean())


PLATE_TOO_SIMILAR = 0.35
VISION_MODEL = "nvidia/Nemotron-3.5-Super-VL"   # 問 Nebius /v1/models 找到的看圖版 Nemotron


def plate_people(data: bytes) -> str | None:
    """空景裡有沒有人（含鏡子裡的倒影）；有就回傳在哪裡，沒有回 None，沒金鑰／叫不動也回 None（不擋）。

    第 61 場新版四張空景：中空景鏡子裡站著一個人影。Nemotron VL 四張全判對（每張約 5 秒）。"""
    import base64
    import json as _json

    from . import nebius_client, settings_store

    key = settings_store.key("nebius")
    if not key:
        return None
    q = ("Look at this photo of an empty film set. Is there any person, human figure, silhouette, or reflection of a "
         'person anywhere (including in mirrors or glass)? Answer with JSON only: {"people": true|false, "where": "..."}')
    msg = [{"role": "user", "content": [{"type": "text", "text": q}, {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(data).decode()}}]}]
    try:
        text, _ = nebius_client.chat(msg, VISION_MODEL, key, temperature=0, max_tokens=2000, timeout_s=120)
        m = re.search(r"\{.*\}", text, re.S)
        ans = _json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001 - 檢查叫不動不擋生成，只是少一道關
        return None
    return (ans.get("where") or "somewhere in the frame") if ans.get("people") else None


def outfit_zh(data: bytes) -> str | None:
    """看全身卡，回這個人最外層那件衣服的中文（「淡藍色雪紡長洋裝」），給同框鏡標「誰講這句」用。叫不動回 None。

    為什麼看圖不看文字：第三世試跑，選角候選的文字標籤寫 pale pink chiffon dress，生圖模型畫成淡藍色；
    台詞行照文字寫「穿淡粉色連衣裙的璃安」→ 文字壓過參考卡，從 s002_03 起整條鏈的裙子變粉紅。
    正式製作的標籤是人看著卡寫的，所以從來對得上 —— 這裡改成看卡本身。"""
    import base64
    import json as _json

    from . import nebius_client, settings_store

    key = settings_store.key("nebius")
    if not key:
        return None
    q = ("Look at the person or character in this picture. Name the colour and type of the outermost thing they wear "
         "(or, if they wear nothing, their body covering, e.g. 'silver metal armour', 'orange striped fur'), "
         'in Traditional Chinese, at most 10 characters. Answer with JSON only: {"outfit": "..."}')
    msg = [{"role": "user", "content": [{"type": "text", "text": q}, {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(data).decode()}}]}]
    try:
        text, _ = nebius_client.chat(msg, VISION_MODEL, key, temperature=0, max_tokens=2000, timeout_s=120)
        m = re.search(r"\{.*\}", text, re.S)
        out = (_json.loads(m.group(0)) if m else {}).get("outfit", "").strip("。. ")
    except Exception:  # noqa: BLE001 - 標不了不影響生成，只是同框鏡少一個辨認依據
        return None
    return out or None


class _FirstFrame(BaseModel):
    instruction_en: str = Field(description=(
        "One English instruction for an image-editing model. Image 1 is the empty set; images 2+ are the people in "
        "this shot (full-body reference cards, in the order given). Rules: "
        "(1) EXACTLY the people in images 2+ appear — nobody else, no extra figures, no one mentioned who is not in "
        "an image. (2) NEVER use names — the editing model does not know them; say 'the woman from image 2'. "
        "(3) The frame is the first VISIBLE moment of the shot, inside the set: if the action is entering, the person "
        "has just stepped in through the door; if it is coming out of somewhere, they are at its doorway. "
        "(4) Say where each person stands relative to the set's fixtures, which way they face (never posing for the "
        "camera unless the shot says so), and the framing for the shot type. "
        "(5) Keep the room's fixtures and layout from image 1, and each person's face, hair and clothes exactly as "
        "in their image. No dialogue, no later actions. "
        "(6) Physics: you have NOT seen image 1, so never invent where things are. Say explicitly: 'Do not add, move, "
        "open or remove any door, opening, wall or fixture; the room stays exactly as in image 1.' Each person stands "
        "on visible open floor of image 1 — for entering, 'standing on the floor just inside the room's existing "
        "entrance door'; for coming out of somewhere, 'standing on the floor right in front of it'."))


def _ask(action: str, system: str, user: str, schema: type[BaseModel]) -> tuple[Any, dict]:
    """問設定頁指定的文字 AI（Nemotron 預設），回傳 (解析好的物件, 用量)。"""
    from google.genai import types

    from . import genai_client

    client = genai_client.client(genai_client.LONG_MS, action=action)
    resp = client.models.generate_content(
        model="", contents=user,
        config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                           response_schema=schema, temperature=0.4, max_output_tokens=4096),
    )
    parsed = getattr(resp, "parsed", None) or schema.model_validate_json(resp.text)
    usage = getattr(resp, "usage_metadata", None)
    return parsed, {"in": getattr(usage, "prompt_token_count", 0), "out": getattr(usage, "candidates_token_count", 0)}


def plate_prompt(dscene: dict, location_key: str, style_prefix: str) -> tuple[str, dict]:
    """場景空景的生圖提示詞：風格句＋Nemotron 寫的空景描述（含走位用到的東西）＋「不准有人」。"""
    loc = (dscene.get("locations") or {}).get(location_key, location_key)
    user = (f"Location: {loc}\nTime of day: {dscene.get('time', '')}\n"
            f"Blocking in this location (what people do here): {dscene.get('blocking', '')}\n"
            f"Props: {dscene.get('props') or 'none listed'}")
    plate, usage = _ask("場面調度", "You are a production designer describing an empty film set for a location photo.",
                        user, _Plate)
    return " ".join(p for p in (style_prefix.strip(), plate.description_en.strip(), PLATE_TAIL) if p), usage


def first_frame_instruction(shot: dict, cast_order: list[str], dscene: dict) -> tuple[str, dict]:
    """首幀的修圖指示：場景（圖 1）＋角色全身卡（圖 2、3），照這一鏡開頭那一刻的站位與景別。"""
    who = "\n".join(f"image {i}: {name}" for i, name in enumerate(cast_order, 2))
    user = (f"Shot type: {shot.get('shot_type')}  Camera move: {shot.get('engine_camera_move')}\n"
            f"Characters (reference images):\n{who}\n"
            f"What happens in this shot (the first frame is the moment BEFORE it starts): {shot.get('description')}\n"
            f"Scene blocking: {dscene.get('blocking', '')}")
    system = "You direct the opening frame of a live-action shot and brief an image-editing model."
    ff, usage = _ask("場面調度", system, user, _FirstFrame)
    # 關卡：指示裡出現任何角色名字 → 退回重寫一次（實測：寫了不在這一鏡的 Lisa，
    # 修圖模型就自己多畫一個人，還把在場的那個人放錯位置；名字它本來就不認得）。
    names = _character_names(dscene)
    leaked = [n for n in names if n.lower() in ff.instruction_en.lower()]
    if leaked:
        ff, usage2 = _ask("場面調度", system, user + "\n\nYour previous draft used names (" + ", ".join(leaked)
                          + "). Rewrite it: no names at all, refer only to 'the woman/man from image N', "
                          "and include only the people in the images.", _FirstFrame)
        usage = {k: usage.get(k, 0) + usage2.get(k, 0) for k in ("in", "out")}
        leaked = [n for n in names if n.lower() in ff.instruction_en.lower()]
        if leaked:
            raise ValueError(f"first-frame instruction still names {leaked} after a rewrite — not sending it to the image model")
    return ff.instruction_en.strip() + " " + PHYSICS_TAIL, usage


# ── 首幀照景別裁切 ─────────────────────────────────────────────────────────────
#
# 為什麼不叫修圖模型直接構圖：實測兩種寫法（在空景上改、空白畫布＋參考）都推不近 ——
# 寫「close-up」出來還是全身全景，它一定守住圖 1 的構圖。所以順著它：先生全景首幀（它做得最好的事），
# 再拿全景首幀跟空景逐像素比（只差那個人）找出人在哪，照景別裁，最後請它把裁小的圖補回細節（臉對全身卡）。
# 中景、特寫的背景本來就是虛的，放大後的柔焦反而對。
#
# 每個景別要留人身上的哪一段（從頭頂往下，佔全身高的比例）：中景到腰、中近景到胸、特寫到肩。
FRAMING = {"MEDIUM": 0.55, "MEDIUM_CLOSE": 0.40, "CLOSE_UP": 0.30}
HEADROOM = 0.05          # 頭頂上面留的空間（全身高的比例；特寫留 0.07 天花板太多）
REFINE_INSTRUCTION = (
    "Keep the exact framing, composition, pose, expression and background of image 1. "
    "Restore it as a sharp, detailed photograph at full resolution. The person's face, hair and clothes must match "
    "the person in image 2 exactly. The background stays softly out of focus, as in a real lens at this distance."
)


SCALE = 8                # 比對前縮小 8 倍＋模糊：修圖模型會把整張重畫一遍，逐像素比每一格都不一樣（實測）
DIFF_THRESHOLD = 60      # 縮小模糊後還差這麼多才算「這裡多了東西」（實測背景重畫的差異 90% 在 33 以下）


def person_box(plate_png: bytes, frame_png: bytes) -> tuple[int, int, int, int]:
    """全景首幀跟空景比，回傳人的外框 (x0, y0, x1, y1)（首幀的像素座標）。

    兩張只差那個人（修圖守住空景構圖，實測），但修圖會把整張重畫一遍 —— 先縮小、模糊，只留大塊的差異。
    鏡子裡的倒影也會不一樣：把「有差異的直欄」分成一段一段，取差異最多的那一段（人本身比倒影大）。
    """
    import io

    import numpy as np
    from PIL import Image, ImageFilter

    frame = Image.open(io.BytesIO(frame_png)).convert("RGB")
    W, H = frame.size
    small = (max(W // SCALE, 1), max(H // SCALE, 1))
    plate = Image.open(io.BytesIO(plate_png)).convert("RGB").resize(frame.size, Image.LANCZOS)
    a = np.asarray(frame.resize(small, Image.BOX).filter(ImageFilter.GaussianBlur(1)), dtype=np.int16)
    b = np.asarray(plate.resize(small, Image.BOX).filter(ImageFilter.GaussianBlur(1)), dtype=np.int16)
    mask = np.abs(a - b).max(axis=2) > DIFF_THRESHOLD
    h, w = mask.shape
    cols = mask.sum(axis=0)
    active = cols > h * 0.15
    runs, start = [], None
    for x, on in enumerate(list(active) + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            runs.append((start, x))
            start = None
    if not runs:
        raise ValueError("could not find the person: the first frame looks the same as the empty set")
    merged = [runs[0]]                           # 人身上的小縫（手臂與身體之間）併起來
    for a0, b0 in runs[1:]:
        if a0 - merged[-1][1] <= 2:
            merged[-1] = (merged[-1][0], b0)
        else:
            merged.append((a0, b0))
    x0, x1 = max(merged, key=lambda r: cols[r[0]:r[1]].sum())
    rows = np.where(mask[:, x0:x1].sum(axis=1) > (x1 - x0) * 0.2)[0]
    return int(x0 * SCALE), int(rows.min() * SCALE), int(min(x1 * SCALE, W)), int(min((rows.max() + 1) * SCALE, H))


def crop_for_shot(frame_png: bytes, box: tuple[int, int, int, int], shot_type: str,
                  out_size: tuple[int, int] = (1664, 928)) -> bytes | None:
    """照景別裁全景首幀（人置中、頭頂留一點空），放大成 out_size。全景（或不認得的景別）回 None ＝ 不裁。"""
    import io

    from PIL import Image

    frac = FRAMING.get(shot_type)
    if frac is None:
        return None
    img = Image.open(io.BytesIO(frame_png)).convert("RGB")
    W, H = img.size
    x0, y0, x1, y1 = box
    ph = y1 - y0
    top = y0 - HEADROOM * ph
    ch = (frac + HEADROOM) * ph
    cw = ch * out_size[0] / out_size[1]
    if cw > W:                                   # 太寬就整張寬、高照比例
        cw, ch = W, W * out_size[1] / out_size[0]
    cx = (x0 + x1) / 2
    left = min(max(cx - cw / 2, 0), W - cw)
    top = min(max(top, 0), H - ch)
    crop = img.crop((round(left), round(top), round(left + cw), round(top + ch))).resize(out_size, Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    return buf.getvalue()


def _character_names(dscene: dict) -> list[str]:
    """這一場所有角色的名字寫法（代號、名字、中文名），給「指示裡不准有名字」的關卡比對。太短的（1 個字）不比，免得誤判。"""
    names = set()
    for cid, c in (dscene.get("characters") or {}).items():
        for n in (cid, c.get("name"), c.get("name_zh")):
            if n and len(n) >= 2:
                names.add(n)
    return sorted(names)
