"""查證與診斷的 API（verify/）。

- POST /api/projects/{id}/segments/{n}/clips/{shot_id}/observe  {"mark": "嘴沒動"}   人看截圖點的觀察 → 存進查證 → 重新診斷
- POST /api/projects/{id}/segments/{n}/clips/{shot_id}/fix      {"pick": 0}         照診斷的修法改這一鏡（下次重拍就用改過的）
- POST /api/projects/{id}/segments/{n}/clips/{shot_id}/verify                        對這一鏡重新查證（換過 take 之後）
- GET  /api/symptoms                                                                症狀庫（畫面列出來給人看：系統知道哪些病）
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import config_loader, project_store
from ..verify import service

router = APIRouter()
symptoms_router = APIRouter()


def _clip(project_id: str, order: int, shot_id: str):
    from .projects import _ensure_segments

    state = project_store.get_store().load(project_id)
    if not state:
        raise HTTPException(404, f"no project {project_id}")
    seg = next((s for s in _ensure_segments(state) if s.order == order), None)
    if not seg or not seg.shot_list:
        raise HTTPException(404, f"no shot list in segment {order}")
    shot = next((s for s in seg.shot_list.shots if s.shot_id == shot_id), None)
    # 有影片的那一筆優先：批次頁一打開就會替每一鏡補一筆空的 ClipRecord（not_started），
    #    試拍生的那一鏡在批次裡是空的、真正的檔案在 test_shot（實測：重查找到空的那筆，什麼都沒查）
    rec = next((c for c in seg.clips if c.shot_id == shot_id and c.uri), None)
    if rec is None and seg.test_shot and seg.test_shot.shot_id == shot_id:
        rec = seg.test_shot
    if not shot or rec is None:
        raise HTTPException(404, f"no generated clip for {shot_id}")
    return state, seg, shot, rec


def _save(state, seg, rec) -> None:
    """存檔前把同一支影片的另一份紀錄對齊：試拍那一鏡會同時在 test_shot 和 clips 各有一筆（同一個 uri），
    畫面上試拍卡讀 test_shot、批次卡讀 clips —— 只改一份的話兩張卡講不一樣的話
    （實測：重查後批次那份已是中英雙語診斷，試拍卡還顯示舊的純中文）。"""
    copies = [c for c in seg.clips if c.shot_id == rec.shot_id and c.uri == rec.uri]
    if seg.test_shot and seg.test_shot.shot_id == rec.shot_id and seg.test_shot.uri == rec.uri:
        copies.append(seg.test_shot)
    for c in copies:
        if c is not rec:
            c.verify, c.diagnosis, c.trim_head = rec.verify, rec.diagnosis, rec.trim_head
    project_store.get_store().save(state)


@router.post("/{project_id}/segments/{order}/clips/{shot_id}/observe")
def observe(project_id: str, order: int, shot_id: str, payload: dict):
    mark = payload.get("mark")
    if mark not in service.MARKS:
        raise HTTPException(400, f"mark must be one of {service.MARKS}")
    state, seg, shot, rec = _clip(project_id, order, shot_id)
    if not rec.verify:
        raise HTTPException(409, "this clip has not been checked yet")
    marks = [m for m in rec.verify.get("marks", []) if m not in ("嘴沒動", "嘴有動") or mark not in ("嘴沒動", "嘴有動")]
    rec.verify["marks"] = marks + [mark]
    if mark == "嘴有動":
        rec.diagnosis = {}          # 人看過說沒問題：不診斷
    else:
        cost = service.diagnose_into(state, seg, shot, rec)
        state.spend.llm_usd += cost
    _save(state, seg, rec)
    return {"verify": rec.verify, "diagnosis": rec.diagnosis}


@router.post("/{project_id}/segments/{order}/clips/{shot_id}/fix")
def fix(project_id: str, order: int, shot_id: str, payload: dict):
    state, seg, shot, rec = _clip(project_id, order, shot_id)
    picks = (rec.diagnosis or {}).get("picks") or []
    i = int(payload.get("pick", 0))
    if not (0 <= i < len(picks)):
        raise HTTPException(409, "no such diagnosis to apply")
    if payload.get("text"):              # 人在畫面上改過（或補寫）新的表演句／台詞
        picks[i]["new_text"] = str(payload["text"]).strip()
    if picks[i]["action"] in ("edit_performance", "edit_line") and not (picks[i].get("new_text") or "").strip():
        raise HTTPException(409, "write the new performance / line first — this option has no suggested text yet")
    if picks[i]["action"] == "edit_performance" and any(q in picks[i]["new_text"] for q in "「」『』"):
        # 表演句只寫動作；台詞寫進表演句會被唸兩次（作者 H3 六行寫法：台詞是獨立的一行）
        raise HTTPException(409, "the performance sentence must describe actions only — keep the dialogue out of it")
    note = service.apply_fix(state, seg, shot, rec, picks[i])
    picks[i]["applied"] = note
    rec.diagnosis["chosen"] = i          # 並列的修法只套一個；記下選了哪個（學習迴路看得到人怎麼選）
    state.record_decision(choice=f"套用診斷修法 {shot_id}（{picks[i]['symptom_id']}）", rationale=note["zh"])
    _save(state, seg, rec)
    return {"applied": note, "diagnosis": rec.diagnosis}


@router.post("/{project_id}/segments/{order}/clips/{shot_id}/verify")
def reverify(project_id: str, order: int, shot_id: str):
    state, seg, shot, rec = _clip(project_id, order, shot_id)
    prompt = (rec.verify or {}).get("prompt", "")
    cost = service.after_generate(state, seg, shot, rec, prompt, f"{shot_id}_re")
    state.spend.llm_usd += cost
    _save(state, seg, rec)
    return {"verify": rec.verify, "diagnosis": rec.diagnosis}


@symptoms_router.get("/symptoms")
def symptoms():
    return {"symptoms": config_loader.symptoms(), "marks": service.MARKS}
