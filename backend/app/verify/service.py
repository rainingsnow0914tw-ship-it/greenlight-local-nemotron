"""查證接進生成流程的那一層（routes/video.py 生完一鏡呼叫 after_generate；routes/verify.py 給畫面用）。

一鏡生完：查證 → 沒過就診斷 → 結果存在鏡頭紀錄（verify、diagnosis、trim_head）、警告上鏡頭卡、決策日誌記一筆。
人按重拍時：沒過的查證項目與診斷的症狀編號跟著退件原因進生成紀錄（ledger）→ 提煉層學得到
「什麼樣的鏡會出什麼症狀」；重拍的提示詞照診斷的修法改（apply_fix），不是同一份再擲一次骰子。
"""

from __future__ import annotations

import logging

from ..services import media_store

logger = logging.getLogger(__name__)

# 人可以點的觀察（截圖程式量不出來的）→ 症狀庫 detect.human 用同一組字
MARKS = ["嘴沒動", "嘴有動", "動作重複", "語言不對"]


def spoken_text(shot) -> str:
    """送去生成的台詞（學語言模式下名字已換成讀音）。"""
    if shot.dialogue_lines:
        return "　".join(d.get("text", "") for d in shot.dialogue_lines)
    return shot.dialogue or ""


def after_generate(state, segment, shot, record, prompt: str, take_name: str) -> float:
    """生完一鏡就查證；沒過就診斷。回傳花費（診斷的 AI 錢；查證本身是本機、$0）。失敗不擋生成。"""
    from . import checks, doctor

    if not record.uri:
        return 0.0
    path = media_store.path_for(record.uri)
    if not path or not path.exists():
        return 0.0
    film = (segment.shot_list.film_language if segment.shot_list else None) or state.film_language or "zh"
    # 新的一次（重拍沿用同一筆紀錄）先清掉上一次的結果：舊診斷、舊剪頭秒數、舊查證警告都不能帶到新的這一條
    #    （不清的話：新的過了還掛著舊診斷；新的查證壞了，成片會照舊秒數剪新的這一條）
    record.verify, record.diagnosis, record.trim_head = {}, {}, 0.0
    record.warnings = [w for w in record.warnings if "查證" not in w]
    try:
        v = checks.run(str(path), say=spoken_text(shot), film=film, voice_role=shot.voice_role,
                       save=lambda name, data, mime: media_store.save(state.project_id, f"{take_name}_{name}", data, mime))
    except Exception as exc:  # noqa: BLE001 - 查證壞了要講，但不能讓已經花了 8 分鐘的影片跟著失敗
        logger.exception("verify failed")
        record.warnings.append(f"本機查證失敗（不影響生成）：{type(exc).__name__}: {exc}")
        return 0.0
    v["prompt"] = prompt
    v["marks"] = []
    record.verify = v
    record.trim_head = v.get("trim_head", 0.0)
    for c in v["checks"]:
        if c["level"] in ("warn", "fail"):
            record.warnings.append(("✗ " if c["level"] == "fail" else "⚠ ") + "查證：" + c["zh"])
    cost = 0.0
    if not v["passed"] or any(c["level"] == "warn" for c in v["checks"]):
        cost = diagnose_into(state, segment, shot, record)
    return cost


def diagnose_into(state, segment, shot, record) -> float:
    from . import doctor

    v = record.verify or {}
    try:
        dx = doctor.diagnose({"shot_id": shot.shot_id, "speaker": shot.speaker, "voice_role": shot.voice_role,
                              "seconds": shot.total_seconds, "performance": shot.performance},
                             v.get("prompt", ""), v, v.get("marks", []), doctor.controls_from(segment, shot.shot_id))
    except Exception as exc:  # noqa: BLE001 - 診斷壞了不能讓已經生好的這一鏡跟著丟掉：記下來、照常存檔
        logger.exception("diagnosis failed")
        record.diagnosis = {"picks": [], "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        record.warnings.append(f"查證診斷失敗（影片已保存）：{type(exc).__name__}")
        return 0.0
    # 現在的表演句與台詞一起存：並列的修法裡「改表演句／改台詞」那幾個，畫面要拿來預填讓人改
    dx["current"] = {"performance": shot.performance or shot.description or "", "line": spoken_text(shot)}
    record.diagnosis = dx
    if dx.get("picks"):
        state.record_decision(choice=f"查證診斷 {shot.shot_id}：" + "、".join(p["symptom_id"] for p in dx["picks"]),
                              rationale="；".join(p["cause"]["zh"] if isinstance(p["cause"], dict) else p["cause"]
                                                 for p in dx["picks"])[:200], followed_recommendation=False)
    return float(dx.get("cost_usd") or 0.0)


def rejection_note(record) -> str:
    """重拍時寫進生成紀錄的退件原因（學習迴路讀這個）。"""
    v = record.verify or {}
    bad = [f"{c['id']}={c['value']}" for c in v.get("checks", []) if c["level"] in ("warn", "fail")]
    marks = v.get("marks") or []
    dx = [p["symptom_id"] for p in (record.diagnosis or {}).get("picks", [])]
    parts = []
    if bad or marks:
        parts.append("本機查證：" + "、".join(bad + marks))
    if dx:
        parts.append("診斷：" + "、".join(dx))
    return "；".join(parts)


def apply_fix(state, segment, shot, record, pick: dict) -> dict:
    """照診斷的修法改這一鏡（下次重拍就用改過的）。回傳給人看的一句話（中英各一份，畫面照語言挑）。"""
    from app.verify.doctor import _tr

    action = pick.get("action")
    new = (pick.get("new_text") or "").strip()
    if action == "trim_head":
        record.trim_head = float((record.verify or {}).get("trim_head") or 0.0) or record.trim_head
        return _tr(f"成片時剪掉開頭 {record.trim_head} 秒（不用重生）",
                   f"The first {record.trim_head} s will be cut when finishing (no re-render)",
                   f"仕上げで最初の {record.trim_head} 秒を切ります（再生成なし）")
    if action == "edit_voice_role":
        shot.voice_role = "character"
        return _tr("這一鏡標成角色講話：台詞行會寫「<名字>用<聲音>說」，重拍就對嘴",
                   "Marked as the character's line: the prompt names who speaks in which voice, so the retake lip-syncs",
                   "キャラクターのセリフに指定：誰がどの声で話すかを書くので、撮り直すと口が合います")
    if action == "edit_performance" and new:
        shot.performance = new
        return _tr(f"表演句改成：{new}", f"Performance changed to: {new}", f"演技の指示を変更：{new}")
    if action == "edit_line" and new:
        if shot.dialogue_lines:
            shot.dialogue_lines[0]["text"] = new
        shot.dialogue = new
        return _tr(f"台詞改成：{new}", f"Line changed to: {new}", f"セリフを変更：{new}")
    if action == "lengthen_shot":
        shot.engine_duration_seconds = round(float(shot.engine_duration_seconds or shot.total_seconds) + 1.0, 1)
        return _tr(f"這一鏡加長到 {shot.engine_duration_seconds} 秒", f"Shot lengthened to {shot.engine_duration_seconds} s",
                   f"このカットを {shot.engine_duration_seconds} 秒に延長")
    if action == "reroll":
        return _tr("提示詞不改，重拍換種子", "Same prompt, retake with a new seed", "プロンプトはそのまま、シードを変えて撮り直し")
    return _tr("請人確認，不用重生", "A person checks it; no re-render", "人が確認します（再生成なし）")
