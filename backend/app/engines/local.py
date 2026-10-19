"""Greenlight 的生成流程 ↔ 本機 H3 引擎的橋。

試拍、批次生成兩個地方原本都呼叫雲端的 video_gen.generate_clip。選了本機檔位（engine: local）時改走這裡：
1. 提示詞：把 Greenlight 的鏡頭換回導演迴圈的形狀，交給 h3_prompt.render_plan（作者管線的六行寫法）。
   **學習紀錄記的是真的送出去的這份**，不是雲端格式那份 —— 記錯了，之後學到的規律全是錯的。
2. 生成：送使用者自己的 ComfyUI（engines/comfy.py），存進 Greenlight 的媒體庫，回傳跟雲端一樣的 ClipResult。
"""

from __future__ import annotations

import random
from pathlib import Path

from ..services import config_loader, media_store, settings_store

TIER = "h3_local_4step"
MEGAPIXELS = 0.92        # 1312×736：語速公式校準時的解析度（換解析度關卡的秒數就不準了）
SILENCE = Path(__file__).resolve().parent.parent / "machine" / "silence_16s.wav"   # 16 秒數位零，AddGuide 會裁到片長


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
        # 英文版也帶著走：修鏡後整場經 to_greenlight_shots 寫回，沒帶的話英文提示詞就變空（範例 3 s002_11）
        "visual_prompt_en": shot.visual_prompt,
        "dialogue": shot.dialogue,
        "dialogue_lines": [{"speaker": to_id(d.get("speaker")), "text": d.get("text", "")} for d in shot.dialogue_lines],
        "speaker": to_id(shot.speaker) if shot.speaker else None,
        "voice_role": {"narrator": "narration"}.get(shot.voice_role, shot.voice_role),
        "duration_seconds": shot.engine_duration_seconds or shot.base_duration_seconds,
        "first_frame_from": shot.first_frame_from,
    }


def gate_scene(state, dscene: dict) -> dict:
    """給關卡看的場次：加上整部片其他選了角的角色（gates 規則 21 反方向：表演句寫到、這場角色表沒列的人）。"""
    here = {c.get("name") for c in (dscene.get("characters") or {}).values()}
    other = [s.character_name for s in state.casting.sessions if s.character_name not in here]
    return {**dscene, "other_cast": other}


def gate_shots(segment, dscene: dict, shots) -> list[dict]:
    """給關卡看的鏡頭：台詞換回劇本原句。

    導過台詞的鏡，分鏡表裡是送 H3 的字（「おはよう、リアン」：名字換成讀音、口語化）；
    關卡規則 9／24 拿劇本原句（「おはよう、璃安」）比對 → 每一句都被當成「拆在句子中間」。
    第三世試跑：台詞導演跑完，生成前關卡多擋了 3 鏡全是這個。生成前關卡與修鏡都走這裡（同一件事一個家）。"""
    import re

    norm = lambda t: re.sub(r"[\s「」『』。、！？!?,.　]", "", t or "")  # noqa: E731
    lines = {(ln.get("shot_id"), ln.get("k", 0)): ln for ln in segment.lines if ln.get("source")}

    def original(shot_id, k, text):
        # 只有這一句還是台詞導演給的那句，才換回原句：修鏡改過的句子（s003_19 兩鏡合一）
        #    照鏡號查到的是舊的半句，換回去就永遠被擋
        ln = lines.get((shot_id, k))
        if ln and norm(text) in {norm(ln.get("say")), norm(ln.get("spoken")), norm(ln.get("source"))}:
            return ln["source"]
        return text

    out = []
    for s in shots:
        d = _director_shot(s, dscene)
        for k, x in enumerate(d["dialogue_lines"]):
            x["text"] = original(s.shot_id, k, x["text"])
        if d["dialogue_lines"]:
            d["dialogue"] = "".join(x["text"] for x in d["dialogue_lines"])
        elif d["dialogue"]:
            d["dialogue"] = original(s.shot_id, 0, d["dialogue"])
        out.append(d)
    return out


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
        # 畫外音的句型（h3_prompt：「聲音：畫面外傳來 Lisa的聲音，…口白︰「…」（畫面裡的人沒有說話）」）還是點了名
        #    → 範例 2 s004_03：Lisa 的畫外音，畫面裡的深息對嘴、變成女聲。只有外語片換成不點名的寫法；
        #    中文片照作者原句（第 61 場 Lisa 門外那鏡就是這樣成功的）
        if lang:
            prompt = re.sub(r"^聲音：畫面外傳來 .+?，普通話口白︰「(.+)」（畫面裡的人沒有說話）$", rf"{lang}台詞︰「\1」",
                            prompt, flags=re.M)
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
    _drop_missing_refs(dscene, project_id)
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


def _ref_files_exist(project_id: str, names) -> bool:
    names = [names] if isinstance(names, str) else list(names or [])
    return bool(names) and all((u := media_store.find(n, project_id)) and media_store.path_for(u) for n in names)


def _drop_missing_refs(dscene: dict, project_id: str) -> None:
    """參考卡缺檔的人與地方從 refs 拿掉 → 那部分改寫文字（h3_prompt 沒卡就寫文字）。
    自己上傳不夠、模型也只做了一半，也要能拍，剩下的用純文字補 —— 舊版缺一張就整鏡擋下。"""
    refs = (dscene.get("render_zh") or {}).get("refs")
    if not refs or not project_id:
        return
    for group in ("characters", "spots"):
        for k in [k for k, v in (refs.get(group) or {}).items() if not _ref_files_exist(project_id, v.get("image"))]:
            refs[group].pop(k)


def missing_refs(shot, segment, project_id: str) -> list[str]:
    """這一鏡缺參考圖的人／地方（給生成結果標警告：只靠文字畫，長相和場景每次可能都不一樣）。"""
    dscene = (segment.director_scenes or {}).get(shot.director_scene_id or "") or {}
    rz = dscene.get("render_zh") or {}
    refs = rz.get("refs") or {}
    ids = {c.get("name"): cid for cid, c in (dscene.get("characters") or {}).items()}
    out = [n for n in shot.characters_in_frame
           if not _ref_files_exist(project_id, ((refs.get("characters") or {}).get(ids.get(n, n)) or {}).get("image"))]
    spot = shot.spot or ""
    if not _ref_files_exist(project_id, ((refs.get("spots") or {}).get(spot) or {}).get("image")):
        out.append("場景")
    return out


def clean_frame(project_id: str, clip_id: str, frame: bytes, anime: bool) -> str:
    """上一鏡的末幀修過畫質再拿來接（RealESRGAN，畫風對應的那一個），存進媒體庫（{clip_id}_ff.png，看得到接的是哪張），
    回傳本機路徑。修不了（模型沒裝）就照原圖存 —— 接幀照接，只是會比較糊。"""
    import tempfile
    from pathlib import Path

    from ..services.finish import UPSCALE_MODEL, UPSCALE_MODEL_REAL, _has_upscale_model
    from . import comfy

    url = settings_store.comfy_url()
    model = UPSCALE_MODEL if anime else UPSCALE_MODEL_REAL
    out = frame
    if _has_upscale_model(url, model):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"{project_id}_{clip_id}_lastframe.jpg"
            src.write_bytes(frame)
            name = comfy.upload_refs(url, [str(src)])[0]
            w, h = _render_size()
            out, _ = comfy.render(url, comfy.build_clean_frame(name, model, w, h, f"greenlight/{project_id}/ff"),
                                  want=(".png",))
    uri = media_store.save(project_id, f"{clip_id}_ff", out, "image/png" if out is not frame else "image/jpeg")
    return str(media_store.path_for(uri))


def _silence(seconds: float) -> Path:
    """幾秒的絕對靜音檔：16 秒用內建檔，其他長度從它裁一段寫到暫存（同一個長度只寫一次）。

    取樣全是 0（-91dB 的數位零），不是「很小聲」—— 小聲的底噪模型會當成聲音特徵學進去。"""
    import tempfile
    import wave

    if seconds >= 16:
        return SILENCE
    out = Path(tempfile.gettempdir()) / f"greenlight_silence_{seconds:g}s.wav"
    if not out.exists():
        with wave.open(str(SILENCE)) as src, wave.open(str(out), "wb") as dst:
            dst.setparams(src.getparams())
            dst.writeframes(src.readframes(int(src.getframerate() * seconds)))
    return out


def _render_size() -> tuple[int, int]:
    """生成尺寸（0.92 百萬像素 16:9 ＝ 1312×736），接幀的首幀要跟它一樣大。"""
    return 1312, 736


def generate(project_id: str, clip_id: str, prompt: str, shot, ref_paths: list[str] | None = None,
             on_progress=None, first_frame: str | None = None):
    """送本機 ComfyUI 生一鏡，存進媒體庫，回傳 video_gen.ClipResult。first_frame：修過的上一鏡末幀（本機路徑）。"""
    from ..services import video_gen
    from . import comfy

    url = settings_store.comfy_url()
    refs = comfy.upload_refs(url, ref_paths or [])
    ff = comfy.upload_refs(url, [first_frame])[0] if first_frame else None
    # 有人、沒台詞的鏡釘絕對靜音（正式管線 seed_for 的 SILENCE_SEED）：不釘的話開頭會冒呢喃 ——
    # s061_17b 人聲分離實測：不釘，0～2 秒人聲軌 -15～-19dB、Lisa 嘴在動。心聲／旁白要聲音，不釘。
    silent = bool(shot.characters_in_frame) and not (shot.dialogue or shot.dialogue_lines)
    seed_audio = comfy.upload_refs(url, [str(_silence(config_loader.veo_tier(TIER).get("silence_seed_seconds", 16)))])[0] \
        if silent else None
    seconds = float(shot.engine_duration_seconds or shot.base_duration_seconds)
    from ..services import project_store

    st = project_store.get_store().load(project_id)
    wf = comfy.build_workflow(prompt, refs, seed=random.randint(1, 2**48), duration_seconds=seconds,
                              megapixels=MEGAPIXELS, prefix=f"greenlight/{project_id}/{clip_id}", first_frame=ff,
                              audio_seed=seed_audio, aspect=(st.aspect_ratio if st else None) or "16:9")
    try:
        video, info = comfy.render(url, wf, on_progress=on_progress)
    except Exception as exc:  # noqa: BLE001 - 轉成 Greenlight 認得的錯誤，原本的失敗紀錄流程照走
        raise video_gen.VideoGenError(f"{clip_id}: local render failed: {exc}") from exc
    uri = media_store.save(project_id, clip_id, video, "video/mp4")
    p = video_gen.probe(video)
    return video_gen.ClipResult(uri=uri, seconds=p.get("seconds", seconds), width=p.get("width", 0),
                                height=p.get("height", 0), size_bytes=len(video),
                                requested_seconds=int(round(seconds)), tier=TIER, model_id="minimax-h3-ref2v-4step")
