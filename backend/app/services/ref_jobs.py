"""準備參考卡（背景工作）：一個段落 → 每個角色的全身卡、每個地點的正反打空景、每一鏡用哪張空景。

做法：不做首幀，給本機 H3「人物全身卡＋空景卡」，動作與站位用文字（導演寫好的表演句）；
空景要有正反打兩張，每一鏡照鏡頭朝向挑一張。跟本機管線的規矩一致：
- 空景卡每鏡都給（不給整個空間會變）；人物不分景別一律給全身卡（不給它會亂編）。
- 換機位用文字整段重生，不拿別的機位的圖去改（services/ref_images.py 有實測）。

做出來的東西寫在哪（一件事一個家）：
- 圖：媒體庫，名字固定（ref_card_<角色代號>、ref_plate_<場>_<地點>_<A|B>），重跑會覆蓋同名。
- 用法：director_scenes[場]["render_zh"]["refs"] —— engines/h3_prompt.render_plan 本來就讀這份，
  格式照正式製作（director/scenes/s029_first_words.json）。正反打＝每個站位多兩個「<站位>_A／_B」，
  每一鏡的 spot 改指向其中一個；提示詞產生器不用改。

在背景跑、一路寫 segment.refs_log（畫面輪詢顯示進度），跟導演工作同一套（services/direction.py）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config_loader, media_store, project_store

logger = logging.getLogger(__name__)
_running: dict[tuple[str, int], threading.Thread] = {}
_lock = threading.Lock()
VIEW_SUFFIXES = ("_A", "_B", "_A_near", "_B_near")
NEAR_SHOTS = {"CLOSE_UP", "MEDIUM_CLOSE"}


def _base_spot(spot: str) -> str:
    """「sink_area_B_near」→「sink_area」：重跑時不要疊出 sink_area_B_near_A。"""
    for suffix in VIEW_SUFFIXES[::-1]:
        if spot.endswith(suffix):
            return spot[: -len(suffix)]
    return spot


TIME_ZH = {"DAY": "白天", "NIGHT": "夜晚", "DAWN": "清晨", "DUSK": "黃昏", "MORNING": "早上", "EVENING": "傍晚"}


def _log(project_id: str, order: int, kind: str, **data: Any) -> None:
    entry = {"t": round(time.time(), 1), "kind": kind, **data}

    def fn(state):
        next(s for s in state.segments if s.order == order).refs_log.append(entry)

    project_store.get_store().mutate(project_id, fn)


def is_running(project_id: str, order: int) -> bool:
    t = _running.get((project_id, order))
    return bool(t and t.is_alive())


def start(project_id: str, order: int) -> bool:
    """開始準備這一段的參考卡；已經在跑就不重開（回 False）。"""
    with _lock:
        if is_running(project_id, order):
            return False

        def reset(state):
            seg = next(s for s in state.segments if s.order == order)
            seg.refs_log = []
            seg.refs_status = "running"

        project_store.get_store().mutate(project_id, reset)
        t = threading.Thread(target=_run, args=(project_id, order), daemon=True, name=f"refs-{project_id}-{order}")
        _running[(project_id, order)] = t
        t.start()
        return True


def _media_path(project_id: str, name: str) -> str | None:
    uri = media_store.find(name, project_id)
    p = media_store.path_for(uri) if uri else None
    return str(p) if p and p.exists() else None


def _run(project_id: str, order: int) -> None:
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
                    raise RuntimeError(f"{name}: no locked casting image to build the full-body card from")
                card = f"ref_card_{cid}"
                have = refs["characters"].get(cid, {})
                if have.get("from") == face and _media_path(project_id, card):
                    _log(project_id, order, "card_kept", scene=sid, character=name)
                else:
                    _log(project_id, order, "card", scene=sid, character=name)
                    t = time.time()
                    data, mime, _ = local_image.edit([face_path], ref_images.FULL_BODY_INSTRUCTION, aspect="3:4",
                                                     prefix=f"greenlight/{project_id}/{card}")
                    media_store.save(project_id, card, data, mime)
                    _log(project_id, order, "card_done", scene=sid, character=name, seconds=round(time.time() - t))
                # 臉卡＝選角鎖定的那張原圖（本機管線的做法：不分景別一律全身卡＋臉卡，
                #    只給大頭卡的特寫，她一低頭身體就被重畫）
                face_card = f"ref_face_{cid}"
                if not _media_path(project_id, face_card) or have.get("from") != face:
                    loaded = media_store.load(face)
                    media_store.save(project_id, face_card, loaded[0], loaded[1])
                # 卡管臉、髮型、衣服 —— 文字只指圖，不再描述一次（一件事一個定義者）
                line = f"{name}，臉和髮型就是參考圖 <Picture {{n1}}> 裡的那個人，全身和衣服照參考圖 <Picture {{n0}}>。"
                imgs = [card, face_card]
                refs["characters"][cid] = {"image": imgs, "zh": line, "pair_image": imgs, "pair_zh": line, "from": face}

            # ② 正反打空景：每個地點兩張（文字整段重生，共用同一段房間描述）
            when = TIME_ZH.get(str(dscene.get("time", "")).upper(), "")
            faces_by_loc: dict[str, dict[str, str]] = {}
            for loc in (dscene.get("locations") or {}):
                _log(project_id, order, "plate_plan", scene=sid, location=loc)
                views, _ = ref_images.plate_views(dscene, loc, style_en)
                faces_by_loc[loc] = {v: views[v]["faces_en"] for v in views}
                for v, spec in views.items():
                    name = f"ref_plate_{sid}_{loc}_{v}"
                    t = time.time()
                    data, mime, _ = local_image.generate(spec["prompt"], aspect=state.aspect_ratio or "16:9",
                                                         prefix=f"greenlight/{project_id}/{name}")
                    media_store.save(project_id, name, data, mime)
                    _log(project_id, order, "plate_done", scene=sid, location=loc, view=v, faces=spec["faces_en"],
                         seconds=round(time.time() - t))
                    for spot_key, spot in list(rz.get("spots", {}).items()):
                        if spot.get("location") != loc or spot_key.endswith(VIEW_SUFFIXES):
                            continue
                        # 遠景／中景：場景照圖、擺設位置照圖；近景（中近景、特寫）用短卡：只講「就是那個地方＋淺景深」，
                        #    不列擺設 —— 緊景別配長清單，模型會退到廣角把清單拍完（本機管線 00_症狀表、01_寫提示詞）
                        for suffix, zh in ((f"_{v}", f"{when}，就是參考圖 <Picture {{n}}> 裡的那個地方，牆面、門和擺設的位置都照那張圖。"),
                                           (f"_{v}_near", f"{when}，就是參考圖 <Picture {{n}}> 裡的那個地方。淺景深，背景柔焦。")):
                            rz["spots"][spot_key + suffix] = dict(spot)
                            refs["spots"][spot_key + suffix] = {"image": name, "zh": zh.lstrip("，")}
                refs["order"][loc] = ["person", "spot"]

            # ③ 每一鏡朝哪邊：Nemotron 照這一鏡在看誰／誰在說話判斷，改這一鏡的 spot 指向 _A／_B
            scene_shots = [s for s in seg.shot_list.shots if s.director_scene_id == sid]
            for loc, faces in faces_by_loc.items():
                here = [s for s in scene_shots
                        if (rz["spots"].get(s.spot or "", {}).get("location") or loc) == loc]
                picks, _ = ref_images.assign_views([s.model_dump(mode="json") for s in here], faces,
                                                   dscene.get("blocking", ""))
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
