"""場面調度：把 Greenlight 拆好的一場戲，整理成導演迴圈（director/）要的場次資料。

為什麼要有這一步：導演迴圈的關卡要知道「這場有哪些站位、道具在哪個世界、角色在哪個世界、穿什麼」
才能擋得住（例如規則 10：不同世界的角色不能同框；規則 15：要選站位；規則 18：道具不能跑到別的世界）。
這些在作者自己的十章動畫裡是人工一場一場寫的（director/scenes/s061_restroom.json）；
給一般使用者就要讓 Nemotron 先提議、使用者在畫面上點頭或修改。

分工（哪些交給模型、哪些不准模型碰）：
  模型提議：每個角色在哪個世界、穿什麼、這場的節拍／走位／情緒弧線／鏡頭意圖、站位（1～4 個）、道具、台詞英譯
  程式照抄：場號、標題、時間、**每一句台詞原文**（關卡規則 9 拿它比對 —— 讓模型「重打一次」台詞會改字）、畫風
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.screenplay import Scene, Screenplay
from ..services import genai_client, rates

ACTION = "場面調度"     # settings_store.ROLES 裡的動作名稱 → 設定頁「場面調度」那一格


class _Char(BaseModel):
    id: str = Field(description="short lowercase ascii id, e.g. luna, lisa, shenxi")
    name: str = Field(description="the name exactly as written in the script")
    kind: str = Field(description="e.g. 'human woman, office worker' or 'AI, a voice from the laptop'")
    costume: str = Field(description="what they wear in this scene, in English")
    location: str = Field(description="id of the world/location this character is physically in")
    appearance_zh: str = Field(description="one sentence describing their look for the video model, in the script's language")


class _Spot(BaseModel):
    id: str = Field(description="short ascii id naming a PLACE, e.g. restroom_sinks, stall_door — never a shot type")
    location: str
    description: str = Field(description="the place where the people stand and what is visible behind them, "
                                         "in the script's language")
    description_en: str


class _Prop(BaseModel):
    name: str = Field(description="the prop's name in the script's language")
    locations: list[str]


class _Line(BaseModel):
    source_line: int = Field(description="the numbered line of the scene text this spoken line comes from")
    speaker_id: str
    text: str = Field(description="the spoken words copied EXACTLY from that line — no quotes, no speaker name, no stage direction")
    offscreen: bool = Field(description="true if the speaker is heard but not seen in this scene")
    en: str = Field(description="English translation of the line")


class _Staging(BaseModel):
    logline_en: str
    locations: dict[str, str] = Field(description="location id -> English description; separate worlds (a room vs. a virtual space) get separate ids")
    characters: list[_Char]
    blocking: str = Field(description="who stands where and how they move through the scene, in English")
    events: list[str] = Field(description="4-8 beats in order, in English")
    emotion_arc: str
    camera_intent: str = Field(description="how the camera should serve this scene: what to use, what to avoid")
    spots: list[_Spot] = Field(description="1-4 camera positions; a person can only appear in spots of their own location")
    props: list[_Prop]
    lines: list[_Line] = Field(description="every spoken line, once, in the order spoken; if the scene lists the same "
                                           "line twice (e.g. in a summary and in a dialogue list) take it once")


_SYSTEM = """You are the first assistant director preparing ONE scene of a script for an AI video model.
You do not write shots. You decide the physical facts the director and the pre-render gates need:
which world each character is in (a real room and a voice inside a laptop are DIFFERENT locations),
what they wear, where the camera can stand (spots), which props exist and in which world,
the beats, the starting blocking, the emotional arc and the camera intent.
Base everything on the script text. Do not invent characters, events or props the script does not support.
Keep ids short lowercase ascii.

SPOTS are physical places inside a location where people stand — NOT shot types or camera angles
(no "wide", "close-up", "over-the-shoulder"). Each spot says where the people are and what is behind them.
Example for an office restroom scene: one spot "restroom_sinks" = "公司的女廁，洗手台前，一整排洗手台和一面長鏡子，
對面是一排廁所隔間。" Most scenes need 1-2 spots; add one only when the action really moves somewhere else.

PROPS are things a character handles or that the story needs (a phone, a letter) — not furniture or fixtures
that are simply part of the room.

DIALOGUE: list every spoken line exactly once. If the scene text has a dedicated dialogue section
(for example a list headed 對白 / Dialogue), take the lines ONLY from that section — summaries and beat lists
elsewhere in the scene often paraphrase the same lines and must be ignored."""


def stage(scene: Scene, screenplay: Screenplay, style_zh: str, series: str = "") -> tuple[dict[str, Any], dict]:
    """一場戲 → 導演迴圈的場次資料（跟 director/scenes/s061_restroom.json 同形狀），附用量。"""
    from google.genai import types

    client = genai_client.client(genai_client.LONG_MS, action=ACTION)
    known = {c.name: (c.visual_description or "") for c in screenplay.characters}
    source = (scene.raw_text or scene.action).splitlines()
    numbered = "\n".join(f"{i}| {ln}" for i, ln in enumerate(source, 1))
    prompt = f"""Script: {screenplay.title}
Scene {scene.scene_number}: {scene.slugline or scene.location or ''} — {scene.time_of_day.value}
Characters present: {', '.join(scene.characters_present)}
Known character notes: {known}

Scene text (numbered lines):
{numbered}"""
    config = types.GenerateContentConfig(system_instruction=_SYSTEM, response_mime_type="application/json",
                                         response_schema=_Staging, temperature=0.3)
    # 回覆格式壞掉就把錯誤講給它聽、再問一次 —— 跟導演迴圈同一招（實測：第一次 JSON 第 176 行壞掉）
    usage_in = usage_out = 0
    model = ""
    st = None
    for attempt in range(2):
        response = client.models.generate_content(model="", contents=prompt, config=config)
        meta = response.usage_metadata
        model = rates.model_used(meta, model)
        usage_in += meta.prompt_token_count
        usage_out += meta.candidates_token_count
        try:
            st = response.parsed or _Staging.model_validate_json(response.text)
            break
        except ValueError as exc:
            prompt += (f"\n\nYour previous reply was not valid JSON ({str(exc).splitlines()[0][:200]}). "
                       "Return the JSON object again, valid this time.")
    if st is None:
        raise ValueError("staging reply was not valid JSON twice")
    usage = {"model": model, "input_tokens": usage_in, "output_tokens": usage_out,
             "cost_usd": rates.cost(model, usage_in, usage_out)}
    return to_director_scene(scene, st, style_zh, series, source), usage


def to_director_scene(scene: Scene, st: _Staging, style_zh: str, series: str = "",
                      source: list[str] | None = None) -> dict[str, Any]:
    """組成導演迴圈要的形狀。台詞：模型說「第幾行、誰講、原文」，程式核對原文真的一字不差在劇本裡。

    為什麼要核對：台詞原文是關卡規則 9 的比對基準。模型「抄」的時候會順手改字、補標點、合併兩句 ——
    基準本身錯了，後面每一回合的比對都跟著錯（作者的管線踩過：基準跟正典對不上，五版八小時白做）。
    對不上的標 unverified，畫面上請使用者看一眼，不默默收下。
    """
    full = re.sub(r"\s+", "", "".join(source or (scene.raw_text or "").splitlines()))
    dialogue = []
    for ln in sorted(st.lines, key=lambda x: x.source_line):
        text = ln.text.strip().strip("「」『』\"")
        entry = {"speaker": ln.speaker_id, "line": text, "en": ln.en}
        if ln.offscreen:
            entry["note"] = "off screen"
        if re.sub(r"\s+", "", text) not in full:
            # 對不上：回到它說的那一行，由程式自己把引號裡的原文抽出來（最像的那一段）。
            #    實測兩種：模型抄錯字（「剛剛」→「剭剛」）、把『』換成‘’ —— 原文都在那一行裡。
            fixed = _quote_from_source(text, source or [], ln.source_line)
            if fixed:
                entry["line"] = fixed
                entry["repaired_from_source"] = True
            else:
                entry["unverified"] = True      # 程式也找不到：請使用者確認
        dialogue.append(entry)
    return {
        "scene_id": f"s{scene.scene_number:03d}",
        "title": scene.slugline or scene.location or f"Scene {scene.scene_number}",
        "series": series,
        "logline_en": st.logline_en,
        "time": scene.time_of_day.value,
        "locations": st.locations,
        "characters": {c.id: {"name": c.name, "name_zh": c.name, "kind": c.kind, "costume": c.costume,
                              "location": c.location} for c in st.characters},
        "blocking": st.blocking,
        "events": st.events,
        "dialogue": dialogue,
        "emotion_arc": st.emotion_arc,
        "camera_intent": st.camera_intent,
        "props": {p.name: p.locations for p in st.props},
        "render_zh": {
            "style": style_zh,
            "characters": {c.id: c.appearance_zh for c in st.characters},
            "spots": {s.id: {"location": s.location, "zh": s.description, "en": s.description_en} for s in st.spots},
        },
    }


def _quote_from_source(text: str, source: list[str], line_no: int) -> str | None:
    """在第 line_no 行（前後各多看一行，模型行號偶爾差一）找引號裡跟 text 最像的那段原文；夠像（≥0.8）才回傳。"""
    from difflib import SequenceMatcher

    best, best_ratio = None, 0.0
    for i in (line_no - 1, line_no - 2, line_no):
        if 0 <= i < len(source):
            for q in re.findall(r"「(.+?)」(?!』)", source[i]) or [re.split(r"[：:]", source[i], 1)[-1].strip()]:
                r = SequenceMatcher(None, text, q).ratio()
                if r > best_ratio:
                    best, best_ratio = q, r
    return best if best_ratio >= 0.8 else None


def _ascii_id(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "char"
