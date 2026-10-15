"""學語言模式的 API（language/）。

- GET/POST /api/projects/{id}/languages                        原聲語言、字幕語言、學語言模式開關
- POST     /api/projects/{id}/segments/{n}/lines               開始台詞導演（背景跑；要先有分鏡表）
- GET      /api/projects/{id}/segments/{n}/lines               狀態＋進度紀錄＋每一句結果＋學習筆記
- PATCH    /api/projects/{id}/segments/{n}/lines/{line_id}     使用者自己改一句（改完重跑關卡、寫回分鏡表）
- POST     /api/projects/{id}/segments/{n}/study-video?lang=zh 燒學習字幕（背景跑；要先接過片）
- GET      /api/projects/{id}/segments/{n}/study-video/{lang}  下載學習字幕版
- GET      /api/projects/{id}/segments/{n}/study-subs.srt?lang=zh  雙語 SRT
"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from ..language import gates, jobs, study_subs
from ..services import demo_mode, media_store, project_store

logger = logging.getLogger(__name__)
router = APIRouter()

FILM_LANGS = ["ja", "zh", "en", "ko"]       # 原聲：H3 會講的語言
LEARNER_LANGS = ["zh", "en", "ja", "ko"]    # 字幕下行
_burning: dict[tuple[str, int, str], threading.Thread] = {}


def _state(project_id: str):
    state = project_store.get_store().load(project_id)
    if not state:
        raise HTTPException(404, f"no project {project_id}")
    return state


def _segment(project_id: str, order: int):
    from .projects import _ensure_segments

    state = _state(project_id)
    seg = next((s for s in _ensure_segments(state) if s.order == order), None)
    if not seg:
        raise HTTPException(404, f"no segment {order}")
    return state, seg


@router.get("/{project_id}/languages")
def get_languages(project_id: str):
    st = _state(project_id)
    return {"study_mode": st.study_mode, "film_language": st.film_language, "learner_languages": st.learner_languages,
            "name_glossary": st.name_glossary, "film_options": FILM_LANGS, "learner_options": LEARNER_LANGS}


@router.post("/{project_id}/languages")
def set_languages(project_id: str, payload: dict):
    film = payload.get("film_language")
    learners = payload.get("learner_languages")
    if film is not None and film not in FILM_LANGS:
        raise HTTPException(400, f"film_language must be one of {FILM_LANGS}")
    if learners is not None:
        learners = [x for x in dict.fromkeys(learners) if x in LEARNER_LANGS]
        if not learners:
            raise HTTPException(400, "pick at least one subtitle language")

    def fn(st):
        if "study_mode" in payload:
            st.study_mode = bool(payload["study_mode"])
        if film is not None:
            st.film_language = film
        if learners is not None:
            st.learner_languages = learners
        if "name_glossary" in payload:
            st.name_glossary = str(payload["name_glossary"] or "")[:4000]
        # 字幕語言跟原聲一樣沒有意義（日文片配日文字幕＝上下兩行一樣）
        st.learner_languages = [x for x in st.learner_languages if x != st.film_language] or (
            ["en"] if st.film_language != "en" else ["zh"])

    project_store.get_store().mutate(project_id, fn)
    return get_languages(project_id)


@router.post("/{project_id}/segments/{order}/lines")
def start_lines(project_id: str, order: int):
    state, seg = _segment(project_id, order)
    if seg.shot_list is None:
        raise HTTPException(409, "this part has no shot list yet — direct it first, then direct the lines")
    started = jobs.start(project_id, order)
    return {"started": started, "running": jobs.is_running(project_id, order)}


@router.get("/{project_id}/segments/{order}/lines")
def lines_state(project_id: str, order: int, since: int = 0):
    state, seg = _segment(project_id, order)
    pending = 0
    if seg.shot_list is not None:
        pending = sum(1 for s in seg.shot_list.shots if s.dialogue or s.dialogue_lines)
    return {
        "study_mode": state.study_mode,
        "status": "running" if jobs.is_running(project_id, order) else seg.lines_status,
        "log": seg.lines_log[since:], "total": len(seg.lines_log),
        "lines": seg.lines, "notes": seg.study_notes,
        "film_language": state.film_language,
        "learner_languages": [x for x in state.learner_languages if x != state.film_language],
        "has_shot_list": seg.shot_list is not None, "shots_with_dialogue": pending,
        "has_video": bool(seg.output_uri),
        "study_videos": {k: {**v, "running": (project_id, order, k) in _burning and _burning[(project_id, order, k)].is_alive()}
                         for k, v in seg.study_videos.items()},
    }


@router.patch("/{project_id}/segments/{order}/lines/{line_id}")
def edit_line(project_id: str, order: int, line_id: str, payload: dict):
    """使用者自己改一句：讀音標記版台詞、字幕、語氣。改完重跑這一句的關卡、寫回分鏡表。"""
    state, seg = _segment(project_id, order)
    film = state.film_language
    learners = [x for x in state.learner_languages if x != film]
    cur = next((ln for ln in seg.lines if ln["id"] == line_id), None)
    if not cur:
        raise HTTPException(404, f"no line {line_id}")
    new = {**cur, "subs": {**(cur.get("subs") or {}), **(payload.get("subs") or {})}}
    if "register" in payload:
        new["register"] = payload["register"]
    if "ruby" in payload:
        new["spoken"] = gates.strip_ruby(payload["ruby"])
        # 中文自己打、沒標拼音 → 程式補（備案，可能有多音字錯，關卡會叫）
        new["ruby"] = gates.zh_ruby(new["spoken"]) if film == "zh" and "[" not in payload["ruby"] else payload["ruby"]
    probe_line = {"id": line_id, "shot_id": cur["shot_id"], "seconds": cur.get("seconds") or 0}
    if film == "ja":
        new["ruby"], new["filled"] = gates.fill_ruby(new["ruby"])
    probe_line["source"] = cur.get("source")
    probe_line["speaker"] = cur.get("speaker")
    issues = gates.check([probe_line], [new], film, learners, jobs.people(state, seg), jobs.glossary_en(state),
                         jobs.glossary(state))
    new["issues"] = [{"level": i["level"], "rule": i["rule"], "zh": i["zh"]} for i in issues]
    new["hints"] = gates.hints(new["spoken"], film)
    new["edited"] = True
    results = [new if ln["id"] == line_id else ln for ln in seg.lines]
    jobs.apply(project_id, order, results, seg.study_notes, {"cost_usd": 0.0})
    return new


@router.post("/{project_id}/segments/{order}/study-video")
def start_study_video(project_id: str, order: int, lang: str):
    state, seg = _segment(project_id, order)
    if not seg.lines:
        raise HTTPException(409, "direct the lines first")
    if not seg.output_uri:
        raise HTTPException(409, "this part has no assembled video yet — generate and assemble the shots first")
    key = (project_id, order, lang)
    if key in _burning and _burning[key].is_alive():
        return {"started": False}

    def mark(**v):
        project_store.get_store().mutate(
            project_id, lambda st: next(s for s in st.segments if s.order == order).study_videos.__setitem__(lang, v))

    def run():
        try:
            st = project_store.get_store().load(project_id)
            sg = next(s for s in st.segments if s.order == order)
            src = media_store.path_for(sg.output_uri)
            if not src or not src.exists():
                raise study_subs.StudySubError("找不到這一段的成片檔")
            items = study_subs.cues(sg, st.film_language)
            out = src.with_name(f"{src.stem}_study_{st.film_language}-{lang}.mp4")
            study_subs.burn(src, items, st.film_language, lang, out)
            uri = media_store.save(project_id, out.name, out.read_bytes(), "video/mp4")
            mark(status="done", uri=uri, cues=len(items))
        except demo_mode.DemoBlocked:
            # 專案規矩（接線檢查會查）：籠統 except 之前一律先放行 DemoBlocked
            mark(status="failed", error="blocked in demo mode")
            raise
        except Exception as exc:  # noqa: BLE001 - 錯誤寫進專案給畫面看
            logger.exception("study video failed")
            mark(status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")

    mark(status="running")
    t = threading.Thread(target=run, daemon=True, name=f"study-{project_id}-{order}-{lang}")
    _burning[key] = t
    t.start()
    return {"started": True}


@router.get("/{project_id}/segments/{order}/study-video/{lang}")
def download_study_video(project_id: str, order: int, lang: str):
    state, seg = _segment(project_id, order)
    v = seg.study_videos.get(lang) or {}
    p = media_store.path_for(v.get("uri") or "") if v.get("uri") else None
    if not p or not p.exists():
        raise HTTPException(404, "no study video for this language yet")
    return FileResponse(p, media_type="video/mp4", filename=f"part{order}_{state.film_language}-{lang}.mp4")


@router.get("/{project_id}/segments/{order}/study-subs.srt")
def study_srt(project_id: str, order: int, lang: str):
    state, seg = _segment(project_id, order)
    if not seg.lines:
        raise HTTPException(409, "direct the lines first")
    items = study_subs.cues(seg, state.film_language)
    if not items:   # 還沒生影片：照分鏡表的秒數排，先給人看字幕長怎樣
        t = 0.0
        for ln in seg.lines:
            sec = float(ln.get("seconds") or 3)
            items.append({"start": t, "end": t + sec - 0.15, "line": ln})
            t += sec
    return PlainTextResponse(study_subs.srt(items, lang), media_type="application/x-subrip",
                             headers={"Content-Disposition": f'attachment; filename="part{order}_{state.film_language}-{lang}.srt"'})
