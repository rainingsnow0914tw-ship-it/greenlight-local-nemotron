"""準備參考卡（背景工作）：一個段落 → 每個角色的全身卡、每個地點的正反打空景、每一鏡用哪張空景。

作者定的做法：不做首幀，給本機 H3「人物全身卡＋空景卡」，動作與站位用文字（導演寫好的表演句）；
空景要有正反打兩張，每一鏡照鏡頭朝向挑一張。跟本機管線的規矩一致：
- 空景卡每鏡都給（不給整個空間會變）；人物不分景別一律給全身卡（不給它會亂編）。
- 換機位用文字整段重生，不拿別的機位的圖去改（services/ref_images.py 有實測）。

做出來的東西寫在哪（一件事一個家）：
- 圖：媒體庫，名字固定（ref_card_<選角那個人的雜湊>（同一個人跨場共用）、ref_plate_<場>_<地點>_<A|B>），重跑會覆蓋同名。
- 用法：director_scenes[場]["render_zh"]["refs"] —— engines/h3_prompt.render_plan 本來就讀這份，
  格式照正式製作（director/scenes/s029_first_words.json）。正反打＝每個站位多兩個「<站位>_A／_B」，
  每一鏡的 spot 改指向其中一個；提示詞產生器不用改。

在背景跑、一路寫 segment.refs_log（畫面輪詢顯示進度），跟導演工作同一套（services/direction.py）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any

from ..schemas.casting import CharacterKind
from . import config_loader, media_store, project_store

logger = logging.getLogger(__name__)
_running: dict[tuple[str, int], threading.Thread] = {}
_lock = threading.Lock()
VIEW_SUFFIXES = ("_A", "_B", "_A_near", "_B_near")
NEAR_SHOTS = {"CLOSE_UP", "MEDIUM_CLOSE"}
PLATE_VIEWS = ("A", "A_near", "B", "B_near")   # 每個地點四張：正打／反打 × 遠／中


def _visible_tag(project_id: str, card: str, name: str) -> str | None:
    """兩人同框時用來標「誰講這句」的看得到的特徵：「穿黑色西裝外套的 Lisa」。

    為什麼要有：模型不認得名字，只認得畫面上看得到的東西。正式製作第 61 場每句都寫
    「穿黃色毛衣的月音的口白」「戴黑框眼鏡、棕紅色短髮的 Lisa 的口白」（看片時抓到同框鏡嘴動錯人）。
    特徵從**全身卡本身**看（ref_images.outfit_zh）：改 —— 舊版拿選角的文字標籤，標籤寫粉紅、卡是淡藍，
    文字壓過參考卡，整條鏈的裙子跟著變粉紅。看不出來就不標（只寫名字）。
    """
    from . import ref_images

    path = _media_path(project_id, card)
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            zh = ref_images.outfit_zh(fh.read())
    except OSError:
        return None
    return f"穿{zh}的{name}" if zh else None


def _base_spot(spot: str) -> str:
    """「sink_area_B_near」→「sink_area」：重跑時不要疊出 sink_area_B_near_A。"""
    for suffix in VIEW_SUFFIXES[::-1]:
        if spot.endswith(suffix):
            return spot[: -len(suffix)]
    return spot


TIME_ZH = {"DAY": "白天", "NIGHT": "夜晚", "DAWN": "清晨", "DUSK": "黃昏", "MORNING": "早上", "EVENING": "傍晚"}


def plate_spots(rz: dict, refs: dict, loc: str, plates: dict[str, str], when: str) -> None:
    """替地點 loc 的每個站位寫場景卡：地名＋正反兩張空景圖，照正式製作第 61 場的寫法：
    「公司的女廁，洗手台前，就是參考圖 <Picture 3> 裡的那個地方；同一間…往反方向看是參考圖 <Picture 4> 的樣子」。

    兩樣都不能省（s061_07 實證）：只寫「就是參考圖裡的那個地方」、只餵一張圖，
    沒有首幀的鏡頭整鏡跑到戶外樹下。地名是錨，反方向那張是轉身、換角度時圖管得到的範圍。
    遠景／中景（_A、_B）配兩張遠空景、加「擺設位置照圖」；近景（_A_near、_B_near）配兩張中空景、不列擺設 ——
    緊景別配長清單，模型會退到廣角把清單拍完（本機管線 00_症狀表、01_寫提示詞）。
    plates：{"A": 名, "A_near": 名, "B": 名, "B_near": 名}（舊專案只有 A／B 時，近景就用遠的那張）。
    """
    for spot_key, spot in list(rz.get("spots", {}).items()):
        if spot.get("location") != loc or spot_key.endswith(VIEW_SUFFIXES):
            continue
        place = re.split(r"[，,。.]", spot.get("zh") or "")[0].strip()
        head = "，".join(x for x in (when, place) if x)
        for v, o in (("A", "B"), ("B", "A")):
            if v not in plates:
                continue
            for near, tail in (("", "，牆面、門和擺設的位置都照那張圖"), ("_near", "")):
                mine, theirs = plates.get(v + near, plates[v]), plates.get(o + near, plates.get(o))
                back = "；同一個地方往反方向看是參考圖 <Picture {n1}> 的樣子" if theirs else ""
                rz["spots"][f"{spot_key}_{v}{near}"] = dict(spot)
                refs["spots"][f"{spot_key}_{v}{near}"] = {
                    "image": [mine, *([theirs] if theirs else [])],
                    "zh": (f"{head}，就是參考圖 <Picture {{n0}}> 裡的那個地方{tail}{back}。"
                           + ("淺景深，背景柔焦。" if near else "")).lstrip("，")}


def _plate_image(project_id: str, order: int, prompt: str, aspect: str | None, name: str, sid: str, loc: str,
                 view: str) -> tuple[bytes, str]:
    """生一張空景，畫面裡有人（含鏡子倒影）就重生，最多再兩次；還有人就記一條 plate_people 給選場景畫面看。"""
    from ..engines import local_image
    from . import ref_images

    for attempt in range(3):
        data, mime, _ = local_image.generate(prompt, aspect=aspect or "16:9", prefix=f"greenlight/{project_id}/{name}")
        where = ref_images.plate_people(data)
        if not where:
            return data, mime
        _log(project_id, order, "plate_people", scene=sid, location=loc, view=view, where=where, retry=attempt < 2)
    return data, mime


def _name_map(dscene: dict) -> dict[str, str]:
    """角色的各種叫法（代號、英文名、中文名，小寫）→ 分鏡上用的名字。模型回的名字不一定跟分鏡同一種寫法。"""
    out = {}
    for cid, c in (dscene.get("characters") or {}).items():
        shown = c.get("name") or c.get("name_zh") or cid
        for k in (cid, c.get("name"), c.get("name_zh"), c.get("name_en")):
            if k:
                out[str(k).lower()] = shown
    return out


def assign_sides(shots: list, backs_to_a: set[str], names: dict[str, str]) -> dict[str, str]:
    """每一鏡用 A 還是 B 面空景：固定規則，一場戲裡同一個人的背景前後一致。

    ① 表演句寫了「X 正面對著鏡頭」→ X 背後那面（backs_to_a 裡的人背後是 A）
    ② 接上一鏡末幀 → 跟上一鏡同一面（鏡頭沒動）
    ③ 單人鏡 → 那個人背後那面
    ④ 都沒有（側臉雙人鏡）→ 第一個講話的人背後那面；再沒有就 A
    """
    from . import continuity

    side_of = lambda who: "A" if names.get(str(who).lower(), who) in backs_to_a else "B"  # noqa: E731
    picks: dict[str, str] = {}
    for s in shots:
        who = continuity._facing(s)
        chars = s.characters_in_frame or []
        if who:
            picks[s.shot_id] = side_of(who)
        elif s.first_frame_from in picks:
            picks[s.shot_id] = picks[s.first_frame_from]
        elif len(chars) == 1:
            picks[s.shot_id] = side_of(chars[0])
        else:
            first = (s.dialogue_lines[0].get("speaker") if s.dialogue_lines else None) or s.speaker
            picks[s.shot_id] = side_of(first) if first else "A"
    return picks


def _log(project_id: str, order: int, kind: str, **data: Any) -> None:
    entry = {"t": round(time.time(), 1), "kind": kind, **data}

    def fn(state):
        next(s for s in state.segments if s.order == order).refs_log.append(entry)

    project_store.get_store().mutate(project_id, fn)


def is_running(project_id: str, order: int) -> bool:
    t = _running.get((project_id, order))
    return bool(t and t.is_alive())


def start(project_id: str, order: int, plates: bool = True) -> bool:
    """開始準備這一段的參考卡；已經在跑就不重開（回 False）。"""
    with _lock:
        if is_running(project_id, order):
            return False

        def reset(state):
            seg = next(s for s in state.segments if s.order == order)
            seg.refs_log = []
            seg.refs_status = "running"

        project_store.get_store().mutate(project_id, reset)
        t = threading.Thread(target=_run, args=(project_id, order, plates), daemon=True, name=f"refs-{project_id}-{order}")
        _running[(project_id, order)] = t
        t.start()
        return True


def make_card(project_id: str, session, face_path: str, card: str, note: str = "") -> None:
    """從選角那張臉改出全身卡（人／非人兩種說法）。準備參考卡與畫面上的「重生」共用這一個（一件事一個家）。
    note＝使用者補一句（例如「保留盔甲」），接在說法後面。"""
    from ..engines import local_image
    from . import ref_images

    non_human = session is not None and session.kind == CharacterKind.NON_HUMAN
    instruction = ref_images.NON_HUMAN_FULL_BODY_INSTRUCTION if non_human else ref_images.FULL_BODY_INSTRUCTION
    data, mime, _ = local_image.edit([face_path], instruction + (f" {note}" if note else ""),
                                     aspect="3:4", prefix=f"greenlight/{project_id}/{card}")
    media_store.save(project_id, card, data, mime)


def _media_path(project_id: str, name: str) -> str | None:
    uri = media_store.find(name, project_id)
    p = media_store.path_for(uri) if uri else None
    return str(p) if p and p.exists() else None


def _run(project_id: str, order: int, plates: bool = True) -> None:
    from ..engines import local_image
    from . import ref_images

    store = project_store.get_store()
    try:
        state = store.load(project_id)
        seg = next(s for s in state.segments if s.order == order)
        if not seg.director_scenes or not seg.shot_list:
            raise RuntimeError("run the director on this segment first — reference cards follow its staging")
        if not state.casting or not state.casting.can_proceed:
            raise RuntimeError("lock every character in casting first — the full-body cards are made from those faces")
        style = config_loader.style_preset(state.style_id or "photoreal")
        style_en = style.get("prompt_prefix", "")
        _log(project_id, order, "start", scenes=len(seg.director_scenes))

        made: dict[str, str] = {}   # 這一輪已經做好卡的角色 → 用的是哪張臉（同一個人跨場共用一組卡）
        tags: dict[str, str | None] = {}   # 這一輪看過卡的角色 → 同框鏡標「誰講」用的衣服
        for sid, dscene in seg.director_scenes.items():
            rz = dscene.setdefault("render_zh", {})
            refs = rz.setdefault("refs", {"characters": {}, "spots": {}, "order": {}})

            # ① 全身卡：從選角鎖定的那張臉改（臉只生一次）
            for cid, c in (dscene.get("characters") or {}).items():
                name = c.get("name_zh") or c.get("name") or cid
                session = state.casting.session_for(c.get("name") or name) or state.casting.session_for(name)
                face = session.identity_reference_uri if session else None
                face_path = str(media_store.path_for(face)) if face and media_store.path_for(face) else None
                if not face_path:
                    # 沒選角的角色（第三世第 7 場：場面調度把「上位AI評議會」列成角色，它只是一則通知的聲音）
                    #    不能讓整個工作停掉、前面六場白做 → 跳過、記下來給人看（畫面上要它出現，再去選角）
                    _log(project_id, order, "card_skipped", scene=sid, character=name)
                    continue
                # 卡的檔名跟著「選角的那個人」，不跟場次代號：第三世，場面調度每場給塔列昂不同代號
                #    （talang／talion／tarlan…）→ 六場六張全身卡、六個長相。同一個人一組卡，跨場共用。
                key = "c" + hashlib.md5(session.character_name.encode("utf-8")).hexdigest()[:8]
                card = f"ref_card_{key}"
                have = refs["characters"].get(cid, {})
                if session.selected and "-own" in session.selected.candidate_id:
                    # 使用者自備的人物卡（選角「直接用這張」）：原圖就是卡，模型不碰、不另做臉卡
                    #    （參考圖不能被我們的模型重畫，要照使用者自己的）
                    if made.get(key) != face:
                        data, mime = media_store.load(face)
                        media_store.save(project_id, card, data, mime)
                        _log(project_id, order, "card_own", scene=sid, character=name)
                    line = f"{name}，就是參考圖 <Picture {{n0}}> 裡的那個人，臉、髮型和衣服都照這張。"
                    refs["characters"][cid] = {"image": [card], "zh": line, "pair_image": [card], "pair_zh": line,
                                               "from": face, "card": card, "own": True}
                    made[key] = face
                    if key not in tags:
                        tags[key] = _visible_tag(project_id, card, name)
                    if tags[key]:
                        refs["characters"][cid]["voice_tag"] = tags[key]
                    continue
                if made.get(key) == face or (have.get("from") == face and have.get("card") == card and _media_path(project_id, card)):
                    _log(project_id, order, "card_kept", scene=sid, character=name)
                else:
                    _log(project_id, order, "card", scene=sid, character=name)
                    t = time.time()
                    make_card(project_id, session, face_path, card)
                    _log(project_id, order, "card_done", scene=sid, character=name, seconds=round(time.time() - t))
                # 臉卡＝選角鎖定的那張原圖（本機管線的做法：不分景別一律全身卡＋臉卡，
                #    只給大頭卡的特寫，她一低頭身體就被重畫）
                face_card = f"ref_face_{key}"
                if made.get(key) != face and (not _media_path(project_id, face_card) or have.get("from") != face
                                              or not have.get("face_plain") or have.get("card") != card):
                    t = time.time()
                    try:   # 背景換素色（ref_images.FACE_CARD_INSTRUCTION）；本機改圖失敗才退回原圖
                        data, mime, _ = local_image.edit([face_path], ref_images.FACE_CARD_INSTRUCTION,
                                                         aspect="1:1", prefix=f"greenlight/{project_id}/{face_card}")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("face card edit failed, using the casting image: %s", exc)
                        data, mime = media_store.load(face)
                    media_store.save(project_id, face_card, data, mime)
                    _log(project_id, order, "face_card", scene=sid, character=name, seconds=round(time.time() - t))
                # 卡管臉、髮型、衣服 —— 文字只指圖，不再描述一次（一件事一個定義者）
                line = f"{name}，臉和髮型就是參考圖 <Picture {{n1}}> 裡的那個人，全身和衣服照參考圖 <Picture {{n0}}>。"
                imgs = [card, face_card]
                refs["characters"][cid] = {"image": imgs, "zh": line, "pair_image": imgs, "pair_zh": line, "from": face,
                                           "face_plain": True, "card": card}
                made[key] = face
                if key not in tags:          # 同一個人一張卡 → 看一次就好
                    tags[key] = _visible_tag(project_id, card, name)
                tag = tags[key]
                if tag:
                    refs["characters"][cid]["voice_tag"] = tag

            # ② 正反打空景：每個地點四張（A／B 兩面牆 × 遠／中），設施只寫在自己那一面（ref_images.plate_views）
            when = TIME_ZH.get(str(dscene.get("time", "")).upper(), "")
            backs_by_loc: dict[str, list[str]] = {}
            for loc in (dscene.get("locations") or {}):
                # 上次已經做好的一組（四張都在）就沿用：第三世，第 7 場出錯整個停掉，重按一次要把 32 張空景全部重生
                kept = (rz.get("plate_sets") or {}).get(loc)
                # 使用者先上傳的空景（選場景畫面「換成我的照片」，準備之前也可以傳）：原圖照用，只生缺的那幾張
                own = {v for v in PLATE_VIEWS if ((rz.get("plates") or {}).get(f"ref_plate_{sid}_{loc}_{v}") or {}).get("own")
                       and _media_path(project_id, f"ref_plate_{sid}_{loc}_{v}")}
                if own == set(PLATE_VIEWS):
                    kept = {"backs": (kept or {}).get("backs", []), "views": list(PLATE_VIEWS)}
                    rz.setdefault("plate_sets", {})[loc] = kept
                if kept and all(_media_path(project_id, f"ref_plate_{sid}_{loc}_{v}") for v in kept["views"]):
                    _log(project_id, order, "plate_kept", scene=sid, location=loc)
                    backs_by_loc[loc] = kept["backs"]
                    plate_spots(rz, refs, loc, {v: f"ref_plate_{sid}_{loc}_{v}" for v in kept["views"]}, when)
                    refs["order"][loc] = ["person", "spot"]
                    continue
                if not plates and not own:
                    # 只準備人物卡：這個地點沒有空景卡 → 提示詞的場景行寫文字（h3_prompt 有退路），生成時畫面警告很隨機
                    _log(project_id, order, "plate_skipped", scene=sid, location=loc)
                    refs["order"][loc] = ["person", "spot"]
                    continue
                _log(project_id, order, "plate_plan", scene=sid, location=loc)
                views, _ = ref_images.plate_views(dscene, loc, style_en)
                backs_by_loc[loc] = views.pop("_backs_to_a", [])
                rz.setdefault("plate_sets", {})[loc] = {"backs": backs_by_loc[loc], "views": list(views)}
                got: dict[str, bytes] = {}
                for v, spec in views.items():
                    name = f"ref_plate_{sid}_{loc}_{v}"
                    if v in own:
                        got[v] = open(_media_path(project_id, name), "rb").read()
                        _log(project_id, order, "plate_own", scene=sid, location=loc, view=v)
                        continue
                    t = time.time()
                    data, mime = _plate_image(project_id, order, spec["prompt"], state.aspect_ratio, name, sid, loc, v)
                    # 關卡：反打那張跟正打太像＝同一面牆（第 61 場舊版 0.447）→ 重生一次；還是像就記下來給人看
                    if v == "B" and "A" in got:
                        sim = ref_images.plate_similarity(got["A"], data)
                        if sim > ref_images.PLATE_TOO_SIMILAR:
                            _log(project_id, order, "plate_similar", scene=sid, location=loc, similarity=round(sim, 2), retry=True)
                            data, mime = _plate_image(project_id, order, spec["prompt"], state.aspect_ratio, name, sid, loc, v)
                            sim = ref_images.plate_similarity(got["A"], data)
                            if sim > ref_images.PLATE_TOO_SIMILAR:
                                _log(project_id, order, "plate_similar", scene=sid, location=loc, similarity=round(sim, 2), retry=False)
                    got[v] = data
                    media_store.save(project_id, name, data, mime)
                    # 選場景畫面要的：哪個地點、哪一面、遠或中、用什麼提示詞生的（重生照這份畫）
                    rz.setdefault("plates", {})[name] = {"location": loc, "view": v, "faces_en": spec["faces_en"],
                                                         "prompt": spec["prompt"]}
                    _log(project_id, order, "plate_done", scene=sid, location=loc, view=v, faces=spec["faces_en"],
                         seconds=round(time.time() - t))
                plate_spots(rz, refs, loc, {v: f"ref_plate_{sid}_{loc}_{v}" for v in views}, when)
                refs["order"][loc] = ["person", "spot"]

            # ③ 每一鏡朝哪邊：固定規則（不再逐鏡問模型 —— 舊版逐鏡猜，同一個人正面三鏡分到 B、A、B）
            scene_shots = [s for s in seg.shot_list.shots if s.director_scene_id == sid]
            names = _name_map(dscene)
            for loc, backs in backs_by_loc.items():
                here = [s for s in scene_shots
                        if (rz["spots"].get(s.spot or "", {}).get("location") or loc) == loc]
                picks = assign_sides(here, {names.get(n.lower(), n) for n in backs}, names)
                for s in here:
                    base = _base_spot(s.spot or "") or next(
                        k for k, v in rz["spots"].items() if v.get("location") == loc and not k.endswith(VIEW_SUFFIXES))
                    near = "_near" if str(getattr(s.shot_type, "value", s.shot_type)) in NEAR_SHOTS else ""
                    s.spot = f"{base}_{picks[s.shot_id]}{near}"
                _log(project_id, order, "views", scene=sid, location=loc,
                     a=sum(1 for v in picks.values() if v == "A"), b=sum(1 for v in picks.values() if v == "B"))

            def keep(st, sid=sid, dscene=dscene, shots=seg.shot_list.shots):
                sg = next(s for s in st.segments if s.order == order)
                sg.director_scenes[sid] = dscene
                sg.shot_list.shots = shots

            store.mutate(project_id, keep)

        def finish(st):
            next(s for s in st.segments if s.order == order).refs_status = "done"

        store.mutate(project_id, finish)
        _log(project_id, order, "done")
    except Exception as exc:  # noqa: BLE001 - 背景工作：錯誤寫進紀錄給畫面看
        logger.exception("reference cards failed")
        _log(project_id, order, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")

        def failed(st):
            next(s for s in st.segments if s.order == order).refs_status = "failed"

        store.mutate(project_id, failed)
