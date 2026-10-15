"""Greenlight 的生成流程 ↔ 本機 H3 引擎的橋。

試拍、批次生成兩個地方原本都呼叫雲端的 video_gen.generate_clip。選了本機檔位（engine: local）時改走這裡：
1. 提示詞：把 Greenlight 的鏡頭換回導演迴圈的形狀，交給 h3_prompt.render_plan（作者管線的六行寫法）。
   **學習紀錄記的是真的送出去的這份**，不是雲端格式那份 —— 記錯了，之後學到的規律全是錯的。
2. 生成：送使用者自己的 ComfyUI（engines/comfy.py），存進 Greenlight 的媒體庫，回傳跟雲端一樣的 ClipResult。
"""

from __future__ import annotations

import random

from ..services import config_loader, media_store, settings_store

TIER = "h3_local_4step"
MEGAPIXELS = 0.92        # 1312×736：語速公式校準時的解析度（換解析度關卡的秒數就不準了）


def is_local(tier: str | None) -> bool:
    try:
        return config_loader.veo_tier(tier or "").get("engine") == "local"
    except Exception:  # noqa: BLE001
        return False


def _director_shot(shot, dscene: dict) -> dict:
    """Greenlight Shot → 導演迴圈的鏡頭 dict（角色名字換回代號）。"""
    ids = {c.get("name"): cid for cid, c in (dscene.get("characters") or {}).items()}
    to_id = lambda n: ids.get(n, n)  # noqa: E731
    return {
        "shot_id": shot.shot_id,
        "shot_type": shot.shot_type.value,
        "camera_move": shot.engine_camera_move or "BREATHE",
        "characters_in_frame": [to_id(c) for c in shot.characters_in_frame],
        "spot": shot.spot,
        "blocking": shot.blocking,
        "visual_prompt": shot.performance or shot.description,
        "dialogue": shot.dialogue,
        "dialogue_lines": [{"speaker": to_id(d.get("speaker")), "text": d.get("text", "")} for d in shot.dialogue_lines],
        "speaker": to_id(shot.speaker) if shot.speaker else None,
        "voice_role": {"narrator": "narration"}.get(shot.voice_role, shot.voice_role),
        "duration_seconds": shot.engine_duration_seconds or shot.base_duration_seconds,
    }


def _spoken_line(prompt: str, shot, project_id: str, film: str) -> str:
    """把台詞那一行改成第三世實測會對嘴的句型：「<誰>用<什麼聲音>說日語台詞︰「…」」。

    實測 s001_06：只把「普通話口白」換成「日語台詞」（沒有主詞、沒有「說」）→ 聲音是日文，
    但 Lisa 的嘴整鏡閉著，像旁白。對照組＝作者另一部動畫的生成腳本：
    `{名字}用{聲音}說日語台詞︰「…」` 每鏡都對嘴 → 照抄這個句型。聲音用選角鎖定的音色描述。
    """
    import re

    from ..services import project_store

    lang = {"ja": "日語", "en": "英語", "ko": "韓語"}.get(film, "")
    voices = {}
    st = project_store.get_store().load(project_id) if project_id else None
    if st and st.casting:
        for s in st.casting.sessions:
            if s.selected and s.selected.voice_tags:
                voices[s.character_name] = s.selected.voice_tags
    if shot.voice_role in ("narrator", "offscreen"):
        # 心聲／旁白／畫外音：故意不寫「誰在說」→ 有聲音、畫面裡的人嘴不動（同提示詞三個種子 3/3；
        #    作者規則庫 rules/08_招式「旁白鏡」段、症狀表 C 段第 89 行已收）
        return prompt.replace("普通話口白", f"{lang}台詞")
    who = shot.speaker or ""
    voice = voices.get(who)
    said = f"{who}用{voice}的聲音說" if who and voice else (f"{who}說" if who else "說")
    prompt = re.sub(r"^普通話口白︰", f"{said}{lang}台詞︰", prompt, flags=re.M)              # 一個人講
    prompt = re.sub(r"^(.+?)的普通話口白︰", rf"\1說{lang}台詞︰", prompt, flags=re.M)        # 同框兩人：用衣服標誰講
    return prompt.replace("普通話口白", f"{lang}台詞")                                       # 畫面外的聲音


def prompt_for(shot, segment, project_id: str = "", style_id: str | None = None) -> tuple[str, list[str]]:
    """(H3 提示詞, 參考圖檔案路徑)。這一鏡要有導演迴圈的場次資料（segment.director_scenes）。

    風格句跟著專案**現在**的風格（style_id），不用導演那時存下來的 ——
    第 61 場：導演跑的時候還是動畫，之後改成真人版，存下來的風格句還寫「日本動畫」。
    參考卡（準備參考卡做的全身卡＋正反打空景，services/ref_jobs.py）：邏輯名稱換成媒體庫裡的檔案；
    提示詞寫了「參考圖 <Picture N>」卻找不到那張圖 → 停下來講清楚，不送一份指著空氣的提示詞。
    """
    import copy

    from fastapi import HTTPException

    from .h3_prompt import render_plan

    dscene = segment.director_scenes.get(shot.director_scene_id or "")
    if not dscene:
        # 409 不是 500：這是「還沒做前一步」，畫面要能直接告訴使用者去按導演
        raise HTTPException(409, f"{shot.shot_id}: no staging data — run the director on this segment first "
                                 "(the local engine writes prompts from it)")
    dscene = copy.deepcopy(dscene)
    if style_id:
        for p in config_loader.styles().get("presets", []):
            if p.get("id") == style_id and (p.get("prompt_prefix_zh") or p.get("prompt_prefix")):
                dscene.setdefault("render_zh", {})["style"] = p.get("prompt_prefix_zh") or p.get("prompt_prefix")
    prompt, names = render_plan(dscene, _director_shot(shot, dscene))
    # 學語言模式：原聲不是中文時，「普通話口白」要換成那個語言（h3_prompt 照作者管線一字不改，所以在這裡換）。
    film = segment.shot_list.film_language if segment.shot_list else "zh"
    if film and film != "zh":
        prompt = _spoken_line(prompt, shot, project_id, film)
    paths = []
    for name in names:
        uri = media_store.find(name, project_id) if project_id else None
        p = media_store.path_for(uri) if uri else None
        if not p or not p.exists():
            raise HTTPException(409, f"{shot.shot_id}: reference card '{name}' is missing — "
                                     "press “Prepare reference cards” on this segment first")
        paths.append(str(p))
    return prompt, paths


def generate(project_id: str, clip_id: str, prompt: str, shot, ref_paths: list[str] | None = None,
             on_progress=None):
    """送本機 ComfyUI 生一鏡，存進媒體庫，回傳 video_gen.ClipResult。"""
    from ..services import video_gen
    from . import comfy

    url = settings_store.load().get("comfy_url", "http://127.0.0.1:8189")
    refs = comfy.upload_refs(url, ref_paths or [])
    seconds = float(shot.engine_duration_seconds or shot.base_duration_seconds)
    wf = comfy.build_workflow(prompt, refs, seed=random.randint(1, 2**48), duration_seconds=seconds,
                              megapixels=MEGAPIXELS, prefix=f"greenlight/{project_id}/{clip_id}")
    try:
        video, info = comfy.render(url, wf, on_progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - 轉成 Greenlight 認得的錯誤，原本的失敗紀錄流程照走
        raise video_gen.VideoGenError(f"{clip_id}: local render failed: {exc}") from exc
    uri = media_store.save(project_id, clip_id, video, "video/mp4")
    p = video_gen.probe(video)
    return video_gen.ClipResult(uri=uri, seconds=p.get("seconds", seconds), width=p.get("width", 0),
                                height=p.get("height", 0), size_bytes=len(video),
                                requested_seconds=int(round(seconds)), tier=TIER, model_id="minimax-h3-ref2v-4step")
