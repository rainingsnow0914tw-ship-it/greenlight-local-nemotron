"""導演工作的 API（Greenlight Local）。

- POST /api/projects/{id}/segments/{order}/direct   開始導這一段（背景跑；已經在跑就回目前狀態）
- GET  /api/projects/{id}/segments/{order}/direct   目前狀態＋進度紀錄（since=N 只拿第 N 筆以後的）＋每一場的調度資料
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, UploadFile

from ..services import direction, project_store

router = APIRouter()


def _segment(project_id: str, order: int):
    from .projects import _ensure_segments

    state = project_store.get_store().load(project_id)
    if not state:
        raise HTTPException(404, f"no project {project_id}")
    seg = next((s for s in _ensure_segments(state) if s.order == order), None)
    if not seg:
        raise HTTPException(404, f"no segment {order}")
    return state, seg


@router.post("/{project_id}/segments/{order}/direct")
def start_direction(project_id: str, order: int, force: bool = False):
    state, seg = _segment(project_id, order)
    if seg.shot_list is not None and not force and not direction.is_running(project_id, order):
        raise HTTPException(409, f"segment {order} already has a shot list ({seg.shot_list.clip_count} shots); "
                                 "pass force=true to direct it again")
    started = direction.start(project_id, order)
    return {"started": started, "running": direction.is_running(project_id, order)}


@router.get("/{project_id}/segments/{order}/direct")
def direction_state(project_id: str, order: int, since: int = 0):
    state, seg = _segment(project_id, order)
    # 存檔寫著 running、執行緒卻不在（跑到一半 app 被關掉）＝中斷，畫面要再給「開始」鈕（跟成片同一套，routes/finish.py）
    running = direction.is_running(project_id, order)
    return {
        "status": "running" if running else ("interrupted" if seg.direction_status == "running" else seg.direction_status),
        "log": seg.direction_log[since:],
        "total": len(seg.direction_log),
        "scenes": seg.director_scenes,
        "shots": seg.shot_list.clip_count if seg.shot_list else 0,
    }


@router.post("/{project_id}/segments/{order}/shots/{shot_id}/fix")
def fix_shot(project_id: str, order: int, shot_id: str):
    """被生成前關卡擋下的一鏡 → Nemotron 只改這一鏡（必要時連前後鄰鏡），重跑關卡，變好才寫回分鏡表。

    為什麼要有：生成前關卡擋下時畫面說「先改分鏡再生」，但 app 沒有改單一鏡的地方，
    唯一的路是整段重導（第三世試跑：71 分鐘）。同步執行，約 30 秒～2 分鐘。
    """
    from ..director import director
    from ..director.bridge import to_greenlight_shots
    from ..engines import local as local_engine
    from ..language import jobs as line_jobs

    state, seg = _segment(project_id, order)
    if direction.is_running(project_id, order) or line_jobs.is_running(project_id, order):
        raise HTTPException(409, "the director or the line director is still running on this part")
    shot = next((s for s in (seg.shot_list.shots if seg.shot_list else []) if s.shot_id == shot_id), None)
    dscene = (seg.director_scenes or {}).get((shot.director_scene_id or "") if shot else "")
    if not shot or not dscene:
        raise HTTPException(404, f"no directed shot {shot_id}")
    same = [s for s in seg.shot_list.shots if s.director_scene_id == shot.director_scene_id]
    res = director.fix_shot(local_engine.gate_scene(state, dscene), local_engine.gate_shots(seg, dscene, same), shot_id)
    if res["status"] == "failed":
        return {k: v for k, v in res.items() if k != "shots"}
    scene_no = int(re.sub(r"\D", "", shot.director_scene_id) or 0)
    fresh = {s.shot_id: s for s in to_greenlight_shots(dscene, res["shots"], scene_no)}
    touched = set(res["changed"]) | set(res["removed"])

    def write(st):
        sg = next(s for s in st.segments if s.order == order)
        old = {s.shot_id: s for s in sg.shot_list.shots}
        scene_ids = [s["shot_id"] for s in res["shots"]]
        rebuilt = [fresh[i] if i in res["changed"] else old[i] for i in scene_ids]
        out, placed = [], False
        for s in sg.shot_list.shots:            # 這一場的鏡換成新的一版，其他場原封不動
            if s.director_scene_id == shot.director_scene_id:
                if not placed:
                    out += rebuilt
                    placed = True
                continue
            out.append(s)
        sg.shot_list.shots = out
        # 改過／併掉的鏡：舊影片作廢（檔案留在媒體庫）
        sg.clips = [c for c in sg.clips if c.shot_id not in touched]
        line_jobs.remap_lines(sg, res["changed"])
        st.record_decision(choice=f"第 {order} 段 {shot_id} 生成前關卡沒過 → Nemotron 修這一鏡（{res['status']}）",
                           rationale=f"改了 {', '.join(res['changed']) or '—'}；併掉 {', '.join(res['removed']) or '—'}")

    project_store.get_store().mutate(project_id, write)
    return {k: v for k, v in res.items() if k != "shots"}


@router.post("/{project_id}/segments/{order}/shots/{shot_id}/first-frame")
async def upload_first_frame(project_id: str, order: int, shot_id: str, file: UploadFile):
    """這一鏡用自己準備的首幀（原圖釘在第 0 格，優先於接幀）。"""
    from ..services import media_store

    data = await file.read()
    if not data or not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "upload an image file (png or jpg)")
    uri = media_store.save(project_id, f"ff_user_{shot_id}", data, file.content_type)

    def put(st):
        sg = next(s for s in st.segments if s.order == order)
        shot = next((s for s in (sg.shot_list.shots if sg.shot_list else []) if s.shot_id == shot_id), None)
        if not shot:
            raise HTTPException(404, f"no shot {shot_id}")
        shot.user_first_frame = uri
        st.record_decision(choice=f"第 {order} 段 {shot_id} 用自己上傳的首幀", rationale="使用者自備首幀")

    project_store.get_store().mutate(project_id, put)
    return {"shot_id": shot_id, "user_first_frame": uri}


@router.delete("/{project_id}/segments/{order}/shots/{shot_id}/first-frame")
def clear_first_frame(project_id: str, order: int, shot_id: str):
    """不用自己的首幀了（檔案留在媒體庫）。"""
    def clear(st):
        sg = next(s for s in st.segments if s.order == order)
        for s in sg.shot_list.shots if sg.shot_list else []:
            if s.shot_id == shot_id:
                s.user_first_frame = None

    project_store.get_store().mutate(project_id, clear)
    return {"shot_id": shot_id, "user_first_frame": None}


@router.get("/{project_id}/segments/{order}/gate-issues")
def gate_issues(project_id: str, order: int):
    """哪幾鏡生成時會被生成前關卡擋（跟 routes/video._preflight_gates 同一套檢查）。**$0。**

    範例 3：導演寫「沒在回合內過關，交給你看」，畫面卻沒有地方能修 —— 修鏡按鈕要等生成被擋才出現。
    導演一跑完就列出來，每一鏡旁邊給「讓 Nemotron 修這一鏡」。人按過「照樣生成」的鏡不列。
    """
    from ..director import gates
    from ..engines import local as local_engine

    state, seg = _segment(project_id, order)
    out = []
    for sid, dscene in (seg.director_scenes or {}).items():
        same = [s for s in (seg.shot_list.shots if seg.shot_list else []) if s.director_scene_id == sid]
        if not same:
            continue
        scene, shots = local_engine.gate_scene(state, dscene), local_engine.gate_shots(seg, dscene, same)
        for s in same:
            if s.gate_waived:
                continue
            errors = [i for i in gates.check_shot(scene, shots, s.shot_id) if i["level"] == "ERROR"]
            if errors:
                out.append({"shot_id": s.shot_id, "error": f"{s.shot_id} 沒過生成前關卡，先改分鏡再生：" +
                            "；".join(f"[{i['rule']}] {i['msg']}" for i in errors)})
    return {"issues": out}


@router.post("/{project_id}/segments/{order}/shots/{shot_id}/waive")
def waive_gate(project_id: str, order: int, shot_id: str):
    """生成前關卡擋下、修也修不好的一鏡：使用者看過，決定照樣生成。

    為什麼要有：批次照鏡號一鏡一鏡生，被擋的那鏡沒過，後面全部生不了。關卡的規則是統計來的
    （短句一成六唸糊），有些情況人一看就知道不適用（貓叫不是台詞）。決定記進決策紀錄。"""
    def waive(st):
        sg = next(s for s in st.segments if s.order == order)
        shot = next((s for s in (sg.shot_list.shots if sg.shot_list else []) if s.shot_id == shot_id), None)
        if not shot:
            raise HTTPException(404, f"no shot {shot_id}")
        shot.gate_waived = True
        st.record_decision(choice=f"第 {order} 段 {shot_id}：生成前關卡沒過，使用者看過後決定照樣生成",
                           rationale="關卡的規則是統計來的，這一鏡人判斷不適用")

    project_store.get_store().mutate(project_id, waive)
    return {"shot_id": shot_id, "gate_waived": True}


# ── 準備參考卡（全身卡＋正反打空景＋每鏡用哪張）：本機 H3 的參考圖 ─────────────────────
# 背景跑、畫面輪詢進度，跟導演工作同一套（services/ref_jobs.py）。要先導過、選角都鎖定。

@router.post("/{project_id}/segments/{order}/references")
def start_references(project_id: str, order: int, plates: bool = True):
    """plates=False：只準備人物卡，空景不生（使用者上傳過的照用；沒上傳的地點用文字描述，畫面會警告很隨機）。
    範例 3：自備人物卡、沒有空景的使用者，以前只有「全部一起生」或「全部不做（連人物卡都不用）」兩條路。"""
    from ..services import ref_jobs

    state, seg = _segment(project_id, order)
    if not seg.director_scenes or seg.shot_list is None:
        raise HTTPException(409, f"segment {order} has no director shot list yet — run the director first")
    if not state.casting or not state.casting.can_proceed:
        raise HTTPException(409, "lock every character in casting first — the full-body cards are made from those faces")
    started = ref_jobs.start(project_id, order, plates)
    return {"started": started, "running": ref_jobs.is_running(project_id, order)}


@router.get("/{project_id}/segments/{order}/references")
def references_state(project_id: str, order: int, since: int = 0):
    from ..services import ref_jobs

    state, seg = _segment(project_id, order)
    refs = {sid: (d.get("render_zh") or {}).get("refs") for sid, d in seg.director_scenes.items()}
    # 選場景畫面：每張空景（地點、哪一面、遠或中）＋正反兩張太像的警告（ref_jobs 的 plate_similar 紀錄）
    from ..services import media_store

    def place(d, loc):
        """地點的人話名字（場景卡第一句，例「公司廁所洗手台區域」），沒有就用代號。"""
        zh = next((v.get("zh") for v in ((d.get("render_zh") or {}).get("spots") or {}).values()
                   if v.get("location") == loc and v.get("zh")), "")
        return re.split(r"[，,。.]", zh)[0].strip() or loc

    # 每個地點四格都列出來（還沒生、還沒傳的 file 是空的）：分鏡做好就看得到「這一段要幾張空景」，
    #    可以先自己上傳，剩下的按「準備參考卡」由模型生
    from ..services.ref_jobs import PLATE_VIEWS

    plates = []
    for sid, d in seg.director_scenes.items():
        meta = (d.get("render_zh") or {}).get("plates") or {}
        for loc in (d.get("locations") or {}):
            for v in PLATE_VIEWS:
                name = f"ref_plate_{sid}_{loc}_{v}"
                p = meta.get(name) or {}
                plates.append({"scene": sid, "name": name, "location": loc, "place": place(d, loc), "view": v,
                               "faces_en": p.get("faces_en", ""), "own": bool(p.get("own")),
                               "file": (media_store.find(name, project_id) or "").rsplit("/", 1)[-1]})
    # 人物卡：同一個人跨場共用一組（ref_jobs 用選角的人當檔名），這裡一人列一次
    cards, seen = [], set()
    for d in seg.director_scenes.values():
        for c in (((d.get("render_zh") or {}).get("refs") or {}).get("characters") or {}).values():
            card = c.get("card") or (c.get("image") or [None])[0]
            if card and card not in seen:
                seen.add(card)
                imgs = c.get("image") or []
                cards.append({"card": card, "name": re.split(r"[，,]", c.get("zh") or "")[0].strip(),
                              "file": (media_store.find(card, project_id) or "").rsplit("/", 1)[-1],
                              "face_file": (media_store.find(imgs[1], project_id) or "").rsplit("/", 1)[-1] if len(imgs) > 1 else ""})
    similar = [e for e in seg.refs_log if e.get("kind") == "plate_similar" and not e.get("retry")]
    people = [e for e in seg.refs_log if e.get("kind") == "plate_people" and not e.get("retry")]
    return {
        "status": "running" if ref_jobs.is_running(project_id, order) else seg.refs_status,
        "log": seg.refs_log[since:],
        "total": len(seg.refs_log),
        "refs": refs,
        "plates": plates,
        "cards": cards,
        "similar": similar,
        "people": people,
    }


def _plate(seg, name: str) -> dict:
    for d in seg.director_scenes.values():
        p = ((d.get("render_zh") or {}).get("plates") or {}).get(name)
        if p:
            return p
    raise HTTPException(404, f"no empty-set plate {name}")


@router.post("/{project_id}/segments/{order}/references/plates/{name}/regenerate")
def regenerate_plate(project_id: str, order: int, name: str, body: dict | None = None):
    """重生一張空景（同一份提示詞換種子；note＝使用者補一句，例如「門要在左邊」，接在提示詞後面）。"""
    from ..services import media_store, ref_jobs

    state, seg = _segment(project_id, order)
    if ref_jobs.is_running(project_id, order):
        raise HTTPException(409, "reference cards are being prepared — wait for it to finish")
    p = _plate(seg, name)
    if not p.get("prompt"):
        raise HTTPException(409, "this plate was made before 2026-10-17 and has no saved prompt — press “Prepare the cards again”")
    note = str((body or {}).get("note") or "").strip()
    prompt = p["prompt"] + (f" {note}" if note else "")
    data, mime = ref_jobs._plate_image(project_id, order, prompt, state.aspect_ratio, name, "", p["location"], p["view"])
    media_store.save(project_id, name, data, mime)
    return {"name": name, "regenerated": True}


@router.post("/{project_id}/segments/{order}/references/plates/{name}/upload")
async def upload_plate(project_id: str, order: int, name: str, file: UploadFile):
    """換成自己拍的照片（空景沒有人）。之後每一鏡就用這張。"""
    from ..services import media_store

    from ..services.ref_jobs import PLATE_VIEWS

    state, seg = _segment(project_id, order)
    data = await file.read()
    if not data or not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "upload an image file (png or jpg)")
    # 準備參考卡之前也能傳（分鏡做好就知道要幾張參考圖，自己上傳或用模型生都可以）
    #    名字＝ref_plate_<場>_<地點>_<A|A_near|B|B_near>，場和地點要在導演資料裡
    m = re.match(r"ref_plate_(s\d{3})_(.+)_(A_near|B_near|A|B)$", name)
    dscene = (seg.director_scenes or {}).get(m.group(1)) if m else None
    if not dscene or m.group(2) not in (dscene.get("locations") or {}) or m.group(3) not in PLATE_VIEWS:
        raise HTTPException(404, f"no empty-set slot {name}")
    media_store.save(project_id, name, data, file.content_type)

    def mark(st):
        sg = next(s for s in st.segments if s.order == order)
        rz = sg.director_scenes[m.group(1)].setdefault("render_zh", {})
        old = (rz.get("plates") or {}).get(name) or {}
        rz.setdefault("plates", {})[name] = {**old, "location": m.group(2), "view": m.group(3),
                                             "faces_en": old.get("faces_en", ""), "prompt": old.get("prompt", ""), "own": True}

    project_store.get_store().mutate(project_id, mark)
    return {"name": name, "uploaded": True}


def _card_owner(state, seg, card: str):
    """這張全身卡是誰的：回 (選角 session, 臉的檔案路徑)。"""
    from ..services import media_store

    for d in seg.director_scenes.values():
        for cid, c in (((d.get("render_zh") or {}).get("refs") or {}).get("characters") or {}).items():
            if (c.get("card") or (c.get("image") or [None])[0]) == card:
                info = (d.get("characters") or {}).get(cid, {})
                session = state.casting.session_for(info.get("name") or "")
                face = session.identity_reference_uri if session else None
                path = media_store.path_for(face) if face else None
                if not path:
                    raise HTTPException(409, "this character has no locked casting image")
                return session, str(path)
    raise HTTPException(404, f"no character card {card}")


@router.post("/{project_id}/segments/{order}/references/cards/{card}/regenerate")
def regenerate_card(project_id: str, order: int, card: str, body: dict | None = None):
    """重生一張全身卡（從選角那張臉重新改；note＝補一句，例如「保留金屬盔甲」）。同一個人每一場都用這張。

    為什麼要有：第三世試跑，機器人塔列昂的全身卡被改成條紋貓人，畫面上看得到卻沒有地方重生 ——
    「重新準備參考卡」會沿用已經做好的卡。"""
    from ..services import ref_jobs

    state, seg = _segment(project_id, order)
    if ref_jobs.is_running(project_id, order):
        raise HTTPException(409, "reference cards are being prepared — wait for it to finish")
    session, face_path = _card_owner(state, seg, card)
    ref_jobs.make_card(project_id, session, face_path, card, str((body or {}).get("note") or "").strip())
    return {"card": card, "regenerated": True}


@router.post("/{project_id}/segments/{order}/references/cards/{card}/upload")
async def upload_card(project_id: str, order: int, card: str, file: UploadFile):
    """換成自己準備的全身圖。同一個人每一場都用這張。"""
    from ..services import media_store

    state, seg = _segment(project_id, order)
    _card_owner(state, seg, card)
    data = await file.read()
    if not data or not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "upload an image file (png or jpg)")
    media_store.save(project_id, card, data, file.content_type)
    return {"card": card, "uploaded": True}
