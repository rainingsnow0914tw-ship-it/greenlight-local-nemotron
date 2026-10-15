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
import shutil
import tempfile
import threading
from pathlib import Path

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
        was_study, was_film = st.study_mode, st.film_language
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
        # 台詞導演把導過的外語台詞寫進了分鏡表（送影片模型唸的那份）：切換時要跟著換
        for sg in st.segments:
            if not sg.lines:
                continue
            if st.film_language != was_film:          # 換了語言：舊語言導的全部作廢，分鏡表換回原句
                jobs.write_dialogue(sg, directed=False)
                sg.clear_line_direction()
            elif was_study and not st.study_mode:     # 關掉：換回原句（導過的留著，再打開會寫回去）
                jobs.write_dialogue(sg, directed=False)
            elif st.study_mode and not was_study:     # 再打開：寫回導過的版本
                jobs.write_dialogue(sg, directed=True)

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
        # 存檔寫著 running、執行緒卻不在（跑到一半 app 被關掉）＝中斷：回報 interrupted，畫面才會再給「開始」鈕
        "status": "running" if jobs.is_running(project_id, order) else
                  ("interrupted" if seg.lines_status == "running" else seg.lines_status),
        "log": seg.lines_log[since:], "total": len(seg.lines_log),
        "lines": seg.lines, "notes": seg.study_notes,
        "film_language": state.film_language,
        "learner_languages": [x for x in state.learner_languages if x != state.film_language],
        "has_shot_list": seg.shot_list is not None, "shots_with_dialogue": pending,
        "has_video": bool(seg.output_uri),
        "study_videos": {k: _burn_state(project_id, order, k, v) for k, v in seg.study_videos.items()},
    }


@router.patch("/{project_id}/segments/{order}/lines/{line_id}")
def edit_line(project_id: str, order: int, line_id: str, payload: dict):
    """使用者自己改一句：讀音標記版台詞、字幕、語氣。改完重跑這一句的關卡、寫回分鏡表。"""
    if jobs.is_running(project_id, order):
        # 台詞導演跑完會整份寫回，跑的途中改的會被蓋掉 → 先擋下來、講清楚
        raise HTTPException(409, "the line director is still running — edit after it finishes")
    with _edit_lock:   # 這裡是讀整份 → 改一句 → 整份寫回；兩個請求交錯會互相蓋掉，所以一次一個
        return _edit_line(project_id, order, line_id, payload)


_edit_lock = threading.Lock()


def _edit_line(project_id: str, order: int, line_id: str, payload: dict):
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


def _burn_state(project_id: str, order: int, lang: str, v: dict) -> dict:
    """燒字幕的狀態：存檔寫著 running、執行緒卻不在（燒到一半 app 被關掉）＝中斷，按鈕要再出來。"""
    alive = (project_id, order, lang) in _burning and _burning[(project_id, order, lang)].is_alive()
    if not alive and v.get("status") == "running":
        v = {**v, "status": "interrupted"}
    return {**v, "running": alive}


def _video_cues(seg, film: str) -> list[dict]:
    """這一段現在的成片對應的字幕時間：成片流程做出來的（剪過頭、只含收進去的鏡）用它當時算好的那份；
    一般接片（每一條有檔案的鏡、不剪頭）才照鏡頭長度排。兩種片長不一樣，用錯字幕會整條提早。"""
    if seg.finish_cues and seg.output_uri and seg.output_uri == seg.finish_outputs.get("master"):
        return seg.finish_cues
    return study_subs.cues(seg, film)


@router.post("/{project_id}/segments/{order}/study-video")
def start_study_video(project_id: str, order: int, lang: str):
    state, seg = _segment(project_id, order)
    if not seg.lines:
        raise HTTPException(409, "direct the lines first")
    if lang == state.film_language or lang not in state.learner_languages:
        raise HTTPException(400, f"subtitle language must be one of the learner languages {state.learner_languages}, "
                                 f"not the film language ({state.film_language})")
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
            items = _video_cues(sg, st.film_language)
            # 先燒在暫存資料夾、存進媒體庫後就刪：直接寫在媒體庫旁邊會多留一份一樣大的檔
            work = Path(tempfile.mkdtemp(prefix="gl_study_"))
            try:
                out = work / f"{src.stem}_study_{st.film_language}-{lang}.mp4"
                study_subs.burn(src, items, st.film_language, lang, out)
                uri = media_store.save(project_id, out.stem, out.read_bytes(), "video/mp4")
            finally:
                shutil.rmtree(work, ignore_errors=True)
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
    items = _video_cues(seg, state.film_language)
    if not items:   # 還沒生影片：照分鏡表的秒數排，先給人看字幕長怎樣
        t = 0.0
        for ln in seg.lines:
            sec = float(ln.get("seconds") or 3)
            items.append({"start": t, "end": t + sec - 0.15, "line": ln})
            t += sec
    return PlainTextResponse(study_subs.srt(items, lang), media_type="application/x-subrip",
                             headers={"Content-Disposition": f'attachment; filename="part{order}_{state.film_language}-{lang}.srt"'})
