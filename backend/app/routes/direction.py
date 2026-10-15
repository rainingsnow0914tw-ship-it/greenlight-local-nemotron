"""導演工作的 API（Greenlight Local）。

- POST /api/projects/{id}/segments/{order}/direct   開始導這一段（背景跑；已經在跑就回目前狀態）
- GET  /api/projects/{id}/segments/{order}/direct   目前狀態＋進度紀錄（since=N 只拿第 N 筆以後的）＋每一場的調度資料
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

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
    return {
        "status": "running" if direction.is_running(project_id, order) else seg.direction_status,
        "log": seg.direction_log[since:],
        "total": len(seg.direction_log),
        "scenes": seg.director_scenes,
        "shots": seg.shot_list.clip_count if seg.shot_list else 0,
    }


# ── 準備參考卡（全身卡＋正反打空景＋每鏡用哪張）：本機 H3 的參考圖 ─────────────────────
# 背景跑、畫面輪詢進度，跟導演工作同一套（services/ref_jobs.py）。要先導過、選角都鎖定。

@router.post("/{project_id}/segments/{order}/references")
def start_references(project_id: str, order: int):
    from ..services import ref_jobs

    state, seg = _segment(project_id, order)
    if not seg.director_scenes or seg.shot_list is None:
        raise HTTPException(409, f"segment {order} has no director shot list yet — run the director first")
    if not state.casting or not state.casting.can_proceed:
        raise HTTPException(409, "lock every character in casting first — the full-body cards are made from those faces")
    started = ref_jobs.start(project_id, order)
    return {"started": started, "running": ref_jobs.is_running(project_id, order)}


@router.get("/{project_id}/segments/{order}/references")
def references_state(project_id: str, order: int, since: int = 0):
    from ..services import ref_jobs

    state, seg = _segment(project_id, order)
    refs = {sid: (d.get("render_zh") or {}).get("refs") for sid, d in seg.director_scenes.items()}
    return {
        "status": "running" if ref_jobs.is_running(project_id, order) else seg.refs_status,
        "log": seg.refs_log[since:],
        "total": len(seg.refs_log),
        "refs": refs,
    }
