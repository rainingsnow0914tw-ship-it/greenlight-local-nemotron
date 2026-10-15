"""成片流程 API（services/finish.py）：剪頭 → 放大 → 轉場 → 接片 → 燒字幕。

- POST /api/projects/{id}/segments/{n}/finish        開始（背景跑）
- GET  /api/projects/{id}/segments/{n}/finish        狀態＋進度紀錄＋產出
- GET  /api/projects/{id}/segments/{n}/finish/{key}  下載產出（master、ja-zh…）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import finish, media_store, project_store

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


@router.post("/{project_id}/segments/{order}/finish")
def start_finish(project_id: str, order: int):
    state, seg = _segment(project_id, order)
    if not any(c.uri for c in seg.clips):
        raise HTTPException(409, "no generated shots in this part yet")
    return {"started": finish.start(project_id, order), "running": finish.is_running(project_id, order)}


@router.get("/{project_id}/segments/{order}/finish")
def finish_state(project_id: str, order: int, since: int = 0):
    state, seg = _segment(project_id, order)
    return {"status": "running" if finish.is_running(project_id, order) else seg.finish_status,
            "log": seg.finish_log[since:], "total": len(seg.finish_log), "outputs": list(seg.finish_outputs),
            "shots_ready": sum(1 for c in seg.clips if c.uri)}


@router.get("/{project_id}/segments/{order}/finish/{key}")
def finish_download(project_id: str, order: int, key: str):
    state, seg = _segment(project_id, order)
    uri = seg.finish_outputs.get(key)
    p = media_store.path_for(uri) if uri else None
    if not p or not p.exists():
        raise HTTPException(404, f"no output '{key}' yet")
    return FileResponse(p, media_type="video/mp4", filename=f"part{order}_{key}.mp4")
