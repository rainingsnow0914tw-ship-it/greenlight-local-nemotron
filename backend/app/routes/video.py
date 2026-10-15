"""影片生成的 endpoint：試拍一鏡與批次生成。

流程上試拍是關鍵關卡：

    試拍一鏡   ~$0.80
    批次生成   $9–$72

    用一鏡的錢，換一個「這批到底能不能看」的答案，差距是 10 到 90 倍。

試哪一鏡由 `cost_engine._pick_test_shot` 決定，這裡不重算：
挑最簡單的（空鏡必過、證明不了任何事）與挑最難的（失敗是預期內的，
不代表整套參數有問題）都不對，正確的是有角色、成功率落在中位數的那一鏡。
若這裡另行計算，段落頁與試拍頁會建議不同的鏡頭，使用者不知道該信哪個。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import RedirectResponse, StreamingResponse

from ..schemas.segment import PendingLedgerFacts, Segment, TakeRecord, TestShotRecord
from ..services import (
    config_loader,
    constraint_engine,
    continuity,
    cost_engine,
    frame_extract,
    ledger,
    media_store,
    project_store,
    prompt_export,
    refinery,
    success_predictor,
    video_gen,
)

logger = logging.getLogger(__name__)

from ..services import demo_mode  # noqa: E402
from ..engines import local as local_engine  # noqa: E402  Greenlight Local：本機 H3 引擎
from ..services import exports  # noqa: E402  Greenlight Local：成品另存到使用者選的資料夾

router = APIRouter()


def _load(project_id: str):
    state = project_store.get_store().load(project_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"找不到專案「{project_id}」")
    return state


def _segment(state, order: int) -> Segment:
    segment = next((s for s in state.segments if s.order == order), None)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"找不到第 {order} 段")
    if segment.shot_list is None:
        raise HTTPException(
            status_code=409, detail=f"第 {order} 段還沒分鏡，沒有鏡頭可以試拍。"
        )
    return segment


def _casting_status(state) -> tuple[bool, str, list[str]]:
    """選角關卡的現況。回 (過了沒, 人話原因, 還缺哪些角色)。

    跟 `_require_casting` 分開：一個給 API 回應（讓 UI 在按下去之前就長對），
    一個給 route 守門（真的擋住請求）。只有守門，按鈕看起來能按、按了才跳錯誤；
    只有顯示，前端漏判斷或直接打 API 就穿過去。兩者必須來自同一個判斷，
    否則會出現「畫面說可以、後端說不行」的狀態。
    """
    casting = state.casting
    if casting is None:
        return False, "還沒進行選角。每個角色都要先選定外貌，生成的人才會長得一樣。", []
    if casting.can_proceed:
        return True, "", []

    pending = list(casting.pending_characters)
    who = "、".join(pending[:5]) + ("⋯" if len(pending) > 5 else "")
    return (
        False,
        f"還有 {len(pending)} 個角色沒選定（{who}），先完成選角再生成。",
        pending,
    )


def _require_casting(state) -> None:
    """選角沒鎖完就不准生成任何影片。選角關卡的硬性閘門。

    `CastingGate.can_proceed` 只是狀態，必須有 route 實際查詢它才算閘門，
    否則角色沒鎖完照樣能按下試拍。

    沒選角就生成，等於每一鏡的角色長相都由模型即興發揮：
    實測一次過關率 29% vs 49%，而選角一輪只要 $0.06、一鏡要 $0.40。

    Args:
        state: 專案狀態。

    Raises:
        HTTPException(409): 還有角色沒選定，訊息會列出缺誰。
    """
    casting = state.casting
    if casting is None:
        raise HTTPException(
            status_code=409,
            detail="還沒進行選角。每個角色都要先選定外貌，生成的人才會長得一樣。",
        )
    if casting.can_proceed:
        return

    pending = list(casting.pending_characters)
    who = "、".join(pending[:5]) + ("⋯" if len(pending) > 5 else "")
    raise HTTPException(
        status_code=409,
        detail=(
            f"還有 {len(pending)} 個角色沒選定（{who}），先完成選角再生成。"
            f"沒有參考圖的角色，每一鏡都會長得不一樣 —— "
            f"選角一輪 $0.06，重拍一鏡 $0.40。"
        ),
    )


def _pick_shot(segment: Segment, shot_id: str | None, style_id: str = ""):
    """挑要試拍的鏡頭。

    走 cost_engine 的 `test_shot_candidate`，跟段落頁顯示的是同一個。
    使用者也可以指定特定鏡頭。
    """
    shots = segment.shot_list.shots
    if shot_id:
        found = next((s for s in shots if s.shot_id == shot_id), None)
        if found is None:
            raise HTTPException(status_code=404, detail=f"這一段沒有鏡頭「{shot_id}」")
        return found

    plan = cost_engine.build_plan(
        plan_id=f"{segment.segment_id}-testshot",
        shot_list=segment.shot_list,
        history_lookup=ledger.make_history_lookup(style_id),
    )
    if plan.test_shot_candidate:
        found = next(
            (s for s in shots if s.shot_id == plan.test_shot_candidate), None
        )
        if found:
            return found

    return shots[0]


def _record_payload(record: TestShotRecord | None) -> dict | None:
    if record is None:
        return None
    data = record.model_dump()
    data["video_url"] = f"/api/projects/{{}}/clips/{record.shot_id}"
    return data


@router.get("/{project_id}/segments/{order}/test-shot")
def test_shot_status(project_id: str, order: int) -> dict:
    """試拍狀態與報價。**$0。**"""
    state = _load(project_id)
    segment = _segment(state, order)

    tier = (state.chosen_tier.value if state.chosen_tier else None) or config_loader.default_veo_tier()
    shot = _pick_shot(segment, None, state.style_id or "")
    tier_cfg = config_loader.veo_tier(tier)
    per_second = float(tier_cfg.get("per_second_usd", 0.05))

    record = segment.test_shot
    casting_ok, casting_reason, pending_cast = _casting_status(state)
    return {
        "project_id": project_id,
        "order": order,
        "candidate_shot_id": shot.shot_id,
        "candidate_description": shot.description,
        "duration_seconds": shot.base_duration_seconds,
        "extensions": shot.extensions,
        "tier": tier,
        "tier_label": tier_cfg.get("label", tier),
        # 只算基底那一段 —— 試拍不做續拍，續拍是批次階段的事。
        "estimated_cost_usd": round(shot.base_duration_seconds * per_second, 3),
        "already_tested": record is not None,
        "approved": segment.test_shot_approved,
        # 選角閘門的現況要在按下去之前就給前端，見 _casting_status。
        "casting_ready": casting_ok,
        "casting_blocked_reason": casting_reason,
        "pending_characters": pending_cast,
        "record": (
            {
                **record.model_dump(),
                "video_url": f"/api/projects/{project_id}/clips/{record.shot_id}",
            }
            if record
            else None
        ),
    }


def _uri_stem(uri: str) -> str:
    """從媒體 URI 取檔名主體（不含副檔名），給「存回同一個檔」的呼叫用。

    take 檔名帶次數之後，續拍與混音**必須**存回當下這個 take 的檔案；
    拿 shot_id 存會蓋到別的 take。
    """
    return uri.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _reference_images_for(shot, state, segment_order: int | None = None) -> list[tuple[bytes, str]]:
    """畫面內已選定角色的參考圖，餵給 Veo 當 asset reference。

    只靠 appearance_tags 文字無法鎖住長相；asset reference 鎖的是身份，
    構圖與背景仍由提示詞決定，所以選角圖帶場景背景也沒關係。這是它跟
    「拿圖當首幀」的差別：首幀會把選角圖的背景直接畫進鏡頭開頭。

    只在沒有接幀首幀的鏡頭用得上：referenceImages 與 image 同傳會 400，
    互斥處理在 video_gen.generate_clip。接幀的鏡頭從上一鏡繼承角色長相。

    圖讀不回來就跳過不報錯：參考圖是加分項，
    為了它讓整鏡生不出來是本末倒置（跟抽幀失敗降級同一個原則）。
    """
    if not state.casting or not shot.characters_in_frame:
        return []
    # 帶上角色名與外貌標籤：提示詞裡用 `[character N]` 編號指到第 N 張參考圖，
    # 模型才知道那張圖對應誰。
    refs: list[dict] = []
    for name in shot.characters_in_frame:
        session = state.casting.session_for(name)
        # 段落造型：這一段有覆寫就用覆寫那張、沒有就用全片選定。
        # 規則集中在 schema 的 look_for()，這裡不自己翻 segment_looks。
        chosen = session.look_for(segment_order) if session else None
        if not chosen or not chosen.image_uri:
            continue
        loaded = media_store.load(chosen.image_uri)
        if loaded:
            refs.append(
                {
                    "name": name,
                    "tags": (chosen.appearance_tags or "").strip(),
                    "data": loaded[0],
                    "mime": loaded[1],
                }
            )
        if len(refs) >= 6:
            break
    return refs


@router.post("/{project_id}/segments/{order}/test-shot")
def run_test_shot(project_id: str, order: int, shot_id: str | None = None) -> dict:
    """試拍一鏡。**這會花錢**（Lite 720p 8 秒約 $0.40）。

    同步執行，Veo 生成約需 1–3 分鐘。前端要顯示等待狀態。

    試拍也寫進 ledger：它是整個流程裡第一個花錢的動作，漏掉等於學習迴路
    從第二筆才開始，而且漏掉的正好是「這個風格第一次拍出來長怎樣」那一筆。

    寫入時機與批次完全一致：裁決那一刻。按通過 → accepted；
    重新試拍 → 上一支落成 rejected；生成失敗 → error。
    """
    state = _load(project_id)
    segment = _segment(state, order)
    # 選角硬性閘門。試拍也是生成，也要選完角才准跑。
    _require_casting(state)
    shot = _pick_shot(segment, shot_id, state.style_id or "")

    tier = (state.chosen_tier.value if state.chosen_tier else None) or config_loader.default_veo_tier()
    tier_cfg = config_loader.veo_tier(tier)
    per_second = float(tier_cfg.get("per_second_usd", 0.05))

    # 按下重新試拍 = 對上一支下了「不能用」的判決，
    # 必須在新的生成之前落地，否則新結果會覆蓋掉舊的 pending，
    # 那筆被否決的紀錄就永遠消失，而「什麼樣的鏡頭會被打回來」
    # 正是這個系統最該學的東西。
    previous = segment.test_shot
    attempt = 1
    if previous is not None:
        if previous.shot_id == shot.shot_id:
            attempt = previous.attempts + 1
        prev_findings = (previous.audit or {}).get("findings") or []
        prev_reason = "重新試拍"
        if prev_findings:
            prev_reason += "；看片檢核：" + "；".join(prev_findings)[:150]
        _flush_ledger(state, segment, previous, "rejected", reason=prev_reason)

    # 提示詞從 prompt_export 組，跟導出給使用者看的是同一份。
    # 如果試拍用的提示詞跟導出的不一樣，那份導出就沒有參考價值：
    # 使用者拿去別的工具得到不同結果，會以為是別家模型的問題。
    prompt_data = prompt_export.build_prompt(
        shot, segment.shot_list, state.casting, tier=tier
    )
    prompt = prompt_data["full"]
    # Greenlight Local：本機檔位用作者管線的 H3 六行寫法（學習紀錄記的就是真的送出去的這份）
    if local_engine.is_local(tier):
        prompt, local_refs = local_engine.prompt_for(shot, segment, project_id, state.style_id)

    started_at = time.monotonic()
    try:
        if local_engine.is_local(tier):
            result = local_engine.generate(project_id, f"{shot.shot_id}_t{attempt}", prompt, shot, ref_paths=local_refs)
        else:
            result = video_gen.generate_clip(
                project_id,
                # 試拍也每次獨立檔名，重試不覆蓋上一次
                f"{shot.shot_id}_t{attempt}",
                prompt,
                tier=tier,
                aspect_ratio=segment.shot_list.aspect_ratio,
                duration_seconds=shot.base_duration_seconds,
                reference_images=_reference_images_for(shot, state, segment.order),
            )
    except video_gen.VideoGenError as exc:
        # 失敗也是一筆事實。理由同批次：錯誤率突然上升是配額或審核政策
        # 變了的最早信號，而通過率統計本來就會把 error 排除在外。
        failed = TestShotRecord(
            shot_id=shot.shot_id,
            uri="",
            seconds=0.0,
            tier=tier,
            attempts=attempt,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        failed.pending_ledger = _capture_facts(
            state=state,
            segment=segment,
            shot=shot,
            attempt=attempt,
            tier=tier,
            cost_usd=0.0,
            latency_seconds=int(time.monotonic() - started_at),
            model_id="",
            has_first_frame=False,
            prompt=prompt,
        )
        _flush_ledger(state, segment, failed, "error", reason=str(exc)[:120])
        project_store.get_store().save(state)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    cost = round(result.seconds * per_second, 3)
    record = TestShotRecord(
        shot_id=shot.shot_id,
        uri=result.uri,
        seconds=result.seconds,
        width=result.width,
        height=result.height,
        size_bytes=result.size_bytes,
        tier=tier,
        cost_usd=cost,
        attempts=attempt,
        generated_at=datetime.now(timezone.utc).isoformat(),
        warnings=result.warnings,
    )
    record.pending_ledger = _capture_facts(
        state=state,
        segment=segment,
        shot=shot,
        attempt=attempt,
        tier=tier,
        cost_usd=cost,
        latency_seconds=int(time.monotonic() - started_at),
        model_id=result.model_id,
        has_first_frame=False,
        prompt=prompt,
    )

    # 試拍也走看片檢核：它是「這批能不能看」的第一個判斷點，
    # 畫面異物這類問題在這裡抓到，比批次生到一半才發現省得多。
    if local_engine.is_local(tier):
        # Greenlight Local：本機查證（聽寫、開頭亂語、講完沒、臉部截圖）取代雲端看片；沒過就對照症狀庫診斷
        from ..verify import service as verify_service

        audit_cost = verify_service.after_generate(state, segment, shot, record, prompt, f"{shot.shot_id}_t{attempt}")
    else:
        audit_cost = _audit_generated(state, shot, record)
    cost = round(cost + audit_cost, 3)
    record.cost_usd = cost

    segment.test_shot = record
    segment.test_shot_id = shot.shot_id
    # 重新試拍會清掉先前的通過狀態：通過的是「那一支影片」，
    # 不是「這一段」。換了片子還留著通過旗標，等於用舊的判斷放行新的東西。
    segment.test_shot_approved = False
    segment.spent_usd += cost
    state.spend.video_usd += cost
    state.record_decision(
        choice=f"第 {order} 段試拍 {shot.shot_id}",
        rationale=f"{result.seconds} 秒 · {result.width}×{result.height} · ${cost}",
    )
    project_store.get_store().save(state)

    return {
        "record": {
            **record.model_dump(),
            "video_url": f"/api/projects/{project_id}/clips/{shot.shot_id}",
        },
        "prompt_used": prompt,
        "spent_usd": state.spend.total_usd,
    }


@router.post("/{project_id}/segments/{order}/test-shot/approve")
def approve_test_shot(project_id: str, order: int) -> dict:
    """試拍通過，解鎖批次生成。**$0。**"""
    state = _load(project_id)
    segment = _segment(state, order)

    if segment.test_shot is None:
        raise HTTPException(
            status_code=409, detail="這一段還沒試拍，沒有東西可以通過。"
        )

    segment.test_shot_approved = True

    # 結論產生了，這一刻才是 ledger 該收到紀錄的時候（同批次的 approve）。
    _flush_ledger(state, segment, segment.test_shot, "accepted")

    state.record_decision(
        choice=f"第 {order} 段試拍通過",
        rationale=f"{segment.test_shot.shot_id}，可以開始批次生成",
    )
    project_store.get_store().save(state)

    # 順手把批次的預估帶回去：使用者剛按下「通過」，
    # 下一個念頭一定是「那全部要多少錢」。
    variants = cost_engine.build_plan(
        plan_id=f"{project_id}-seg{order}",
        shot_list=segment.shot_list,
        history_lookup=ledger.make_history_lookup(state.style_id or ""),
    ).variants
    tier = (state.chosen_tier.value if state.chosen_tier else None) or config_loader.default_veo_tier()
    chosen = next((v for v in variants if v.tier.value == tier), None)

    return {
        "approved": True,
        "batch_estimate_usd": chosen.cost.total_usd if chosen else None,
        "batch_shot_count": len(segment.shot_list.shots),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 批次生成（逐鏡確認）
# ═══════════════════════════════════════════════════════════════════════════
#
# 產品決定採用最省錢也最費神的粒度：每一鏡都停下來確認，
# 檢查過沒問題再生成下一個，避免浪費錢。
#
# 這個選擇對架構的要求：流程必須可中斷、可續跑。
# 逐鏡確認天生橫跨很長時間，人一定會中途去忙別的。
# 所以每一鏡的狀態都寫進 segment.clips，而不是放在某個 session 或記憶體裡。
# 重跑一個已經通過的鏡頭，是這個流程裡最貴的錯誤。


def _extend_enabled() -> bool:
    """續拍是否啟用。預設 false。

    關掉的理由不是續拍不能用，是它會重新決定配音音色：
    有台詞的鏡頭續拍後會變成兩個人在講話，而劇本裡有台詞的鏡頭是多數。
    詳見 pricing.yaml 的 extend 區塊。

    保留開關而不是刪掉程式碼：機制本身實測有效（4→11 秒），
    沒有台詞的純畫面長鏡頭仍然可能用得上。
    """
    return bool(
        config_loader.pricing().get("veo", {}).get("extend", {}).get("enabled", False)
    )


def _clip_cost(shot, per_second: float) -> float:
    """一個鏡頭要花多少，含續拍。

    用 `total_seconds` 而不是 `base_duration_seconds`：
    一個 8+1 的鏡頭實際是 15 秒。用基底秒數報價會少報一半，
    而使用者是看著這個數字決定要不要按下去的。

    這裡假設續拍按輸出總秒數計費（保守假設，寧可高估）。
    實際計費方式尚未確認，見 pricing.yaml 的 extend 區塊與
    scripts/verify_extend_chain.py。
    """
    seconds = shot.total_seconds if _extend_enabled() else shot.base_duration_seconds
    return seconds * per_second


def _ensure_clips(segment: Segment) -> list:
    """依鏡頭表初始化 clips（只補不覆蓋）。

    只補沒有的：已經生成或通過的紀錄絕對不能被重置。
    分鏡表若中途改過，新增的鏡頭會自動補上 not_started。
    """
    from ..schemas.segment import ClipRecord, ClipStatus

    existing = {c.shot_id for c in segment.clips}
    for shot in segment.shot_list.shots:
        if shot.shot_id not in existing:
            segment.clips.append(ClipRecord(shot_id=shot.shot_id))

    # 試拍已經生出來的那一鏡，直接併進批次：
    # 否則試拍花的 $0.40 等於丟掉，批次還要再生同一鏡一次。
    rec = segment.test_shot
    # 只在生成條件相同時才併入：試拍不接幀，如果那一鏡在鏡頭表上
    # 標著要接上一鏡，它就是硬切生出來的，條件跟批次不同。
    # 併進去等於在新條件的成片裡混一個舊條件的鏡頭，
    # 而且會讓「接幀機制有沒有用」永遠測不準。
    #
    # 判斷自動做，不靠人記得：那一鏡需要接幀（first_frame_from 有值）
    # 就不併入，讓批次自己重生一次。
    if rec and rec.uri:
        test_shot_obj = next(
            (s for s in segment.shot_list.shots if s.shot_id == rec.shot_id), None
        )
        if test_shot_obj and test_shot_obj.first_frame_from:
            rec = None

    if rec and rec.uri:
        for c in segment.clips:
            # 批次還沒有檔案 → 併入；或試拍重拍過、比批次那條新、批次那條還沒通過 → 換成新的一條
            #    （不換的話試拍卡上的按鈕會作用在舊的那一條：重查、診斷、套修法都對錯對象）
            newer = bool(c.uri) and c.uri != rec.uri and (rec.generated_at or "") > (c.generated_at or "") \
                and c.status is not ClipStatus.APPROVED
            if c.shot_id == rec.shot_id and (not c.uri or newer):
                c.uri = rec.uri
                c.seconds = rec.seconds
                c.width, c.height = rec.width, rec.height
                c.size_bytes = rec.size_bytes
                c.tier = rec.tier
                c.cost_usd = rec.cost_usd
                c.attempts = max(c.attempts, 1)
                c.generated_at = rec.generated_at
                c.warnings = list(rec.warnings)
                # 本機查證跟著試拍那一支走（不然併進批次後查證結果、診斷、剪頭秒數都不見）
                c.verify, c.diagnosis, c.trim_head = dict(rec.verify), dict(rec.diagnosis), rec.trim_head
                # 試拍成品也是一個 take，進列表才有得選
                if not any(t.uri == rec.uri for t in c.takes):
                    from ..schemas.segment import TakeRecord as _TR

                    c.takes.append(
                        _TR(
                            attempt=c.attempts,
                            uri=rec.uri,
                            seconds=rec.seconds,
                            width=rec.width,
                            height=rec.height,
                            size_bytes=rec.size_bytes,
                            tier=rec.tier,
                            generated_at=rec.generated_at,
                        )
                    )
                # 試拍已經通過人工檢查就是 approved，
                # 還沒通過就等同一般的待確認，兩種都不必重生。
                c.status = (
                    ClipStatus.APPROVED
                    if segment.test_shot_approved
                    else ClipStatus.AWAITING_APPROVAL
                )

    order = {s.shot_id: i for i, s in enumerate(segment.shot_list.shots)}
    segment.clips.sort(key=lambda c: order.get(c.shot_id, 999))
    return segment.clips


def _batch_payload(project_id: str, segment: Segment, state) -> dict:
    from ..schemas.segment import ClipStatus

    clips = _ensure_clips(segment)
    tier = (state.chosen_tier.value if state.chosen_tier else None) or config_loader.default_veo_tier()
    tier_cfg = config_loader.veo_tier(tier)
    per_second = float(tier_cfg.get("per_second_usd", 0.05))

    done = [c for c in clips if c.status is ClipStatus.APPROVED]
    pending = [c for c in clips if c.status is ClipStatus.AWAITING_APPROVAL]
    todo = [c for c in clips if c.status is ClipStatus.NOT_STARTED]
    failed = [c for c in clips if c.status is ClipStatus.FAILED]

    # 下一個要生的：先處理待確認的（人還沒看），再往下走
    next_shot = None
    if not pending and todo:
        next_shot = next(
            (s for s in segment.shot_list.shots if s.shot_id == todo[0].shot_id), None
        )

    spent = round(sum(c.cost_usd for c in clips), 3)
    remaining = sum(
        _clip_cost(s, per_second)
        for s in segment.shot_list.shots
        if s.shot_id in {c.shot_id for c in todo}
    )

    casting_ok, casting_reason, pending_cast = _casting_status(state)

    return {
        "project_id": project_id,
        "order": segment.order,
        "tier": tier,
        "tier_label": tier_cfg.get("label", tier),
        "total": len(clips),
        "approved": len(done),
        "awaiting": len(pending),
        "failed": len(failed),
        # 已接的成片也要回：接片結果若只存在前端 state，重新整理之後
        # 整個下載／字幕／燒錄區就消失了，明明成片跟燒錄檔都在雲端。
        # stale = 接完之後有鏡頭重生成過（反悔重拍會造成），
        # 是正常狀態，但 UI 必須講，否則下載到的是舊片。
        "output": (
            {
                "url": f"/api/projects/{project_id}/segments/{segment.order}/download",
                "seconds": segment.output_seconds,
                "built_at": segment.output_built_at,
                "stale": bool(segment.output_built_at)
                and any(
                    c.generated_at and c.generated_at > segment.output_built_at
                    for c in clips
                ),
                "burned": {
                    lang: f"/api/projects/{project_id}/media/{fname}/raw"
                    for lang, fname in segment.burned.items()
                },
            }
            if segment.output_uri
            else None
        ),
        # 兩道閘門，按選角 → 試拍的順序回報。
        # 順序很重要：先講選角，不然使用者會先跑去試拍、
        # 試拍又被選角擋下來，等於白繞一圈。
        "can_start": casting_ok and segment.test_shot_approved,
        "blocked_reason": (
            casting_reason
            if not casting_ok
            else ("" if segment.test_shot_approved else "要先試拍一鏡並確認通過，才能開始批次生成。")
        ),
        # 前端要能在按下去之前就顯示「該去選角」並給一個入口。
        # 只靠 API 回 409 是不夠的：那是按了才知道，而按鈕看起來能按。
        "casting_ready": casting_ok,
        "pending_characters": pending_cast,
        "spent_usd": spent,
        "remaining_estimate_usd": round(remaining, 2),
        "next_shot": (
            {
                "shot_id": next_shot.shot_id,
                "description": next_shot.description,
                "duration_seconds": (
                    next_shot.total_seconds
                    if _extend_enabled()
                    else next_shot.base_duration_seconds
                ),
                "extensions": next_shot.extensions,
                # 報價要含續拍。只算 base_duration_seconds 會把
                # 15 秒的鏡頭報成 8 秒的價，按下去才發現扣更多，
                # 違反「花錢前先報價」的原則。
                "cost_usd": round(_clip_cost(next_shot, per_second), 3),
            }
            if next_shot
            else None
        ),
        "clips": [
            {
                **c.model_dump(),
                "video_url": (
                    f"/api/projects/{project_id}/clips/{c.shot_id}" if c.uri else None
                ),
            }
            for c in clips
        ],
    }


@router.get("/{project_id}/segments/{order}/batch")
def batch_status(project_id: str, order: int) -> dict:
    """批次生成的進度與下一鏡報價。**$0。**"""
    state = _load(project_id)
    segment = _segment(state, order)
    payload = _batch_payload(project_id, segment, state)
    project_store.get_store().save(state)
    return payload


_ROUTE_BUDGET_SECONDS = 780
"""整個「生一鏡」請求的硬性總預算。

必須框在最外層：`video_gen` 內層雖然已有 420 秒硬預算，但這個 handler
裡有十幾個會阻塞的呼叫（Firestore、ClickHouse、GCS 下載、抽幀、TTS、
影片生成、看片檢核、存檔），逐個框永遠會漏，請求會卡在被框住的範圍之外
直到平台砍掉。框最外層才是唯一能保證「一定在平台砍掉之前收手」的位置。

780 = Cloud Run 的 900 秒上限減 120 秒，留給例外處理、存 clip 狀態、
寫 ledger、回一句人話。被平台砍掉的話這些全部跑不到。
內層的 420 秒仍然在（正常情況先觸發），這一層是兜底。
"""


@router.post("/{project_id}/segments/{order}/batch/next")
def generate_next_clip(
    project_id: str,
    order: int,
    shot_id: str | None = None,
    auto: bool = False,
) -> dict:
    """生下一個鏡頭。這會花錢。詳細說明見 `_generate_next_clip`。

    這一層只做一件事：保證這個請求一定會在 Cloud Run 砍掉它之前收手。
    """
    from ..services import deadline

    try:
        return deadline.run_with_deadline(
            lambda: _generate_next_clip(project_id, order, shot_id, auto),
            _ROUTE_BUDGET_SECONDS,
            f"生成鏡頭 {project_id}/{order}",
        )
    except deadline.DeadlineExceeded as exc:
        # 504 帶人話，而不是讓平台丟一個沒有訊息的 504。
        # 差別在於：這條路徑走得到 except，clip 狀態與 ledger 都存得下來。
        raise HTTPException(
            status_code=504,
            detail=(
                "這一鏡等太久了（超過 13 分鐘），已經停止等待。"
                "可以直接再按一次重拍 —— 通常是影片服務端當下不穩。"
                "⚠️ 對方沒有回應，所以無法確認那邊有沒有已經生成並計費。"
            ),
        ) from exc


def _generate_next_clip(
    project_id: str,
    order: int,
    shot_id: str | None = None,
    auto: bool = False,
) -> dict:
    """生下一個鏡頭。這會花錢（Lite 8 秒約 $0.40）。

    兩種模式並存：

    逐鏡模式（`auto=False`，預設）
        有鏡頭在等確認時擋住，那正是這個流程的重點。
        不擋的話「逐鏡確認」會變成「逐鏡生成、事後一起看」，
        跟省錢效果剛好相反：使用者會在看到第一鏡崩掉之前，
        已經把十二鏡的錢都花完了。

    一鍵模式（`auto=True`）
        放行那道閘門，讓前端可以連續呼叫把整段生完。

        一鍵不等於自動通過。生出來的鏡頭一樣停在
        `AWAITING_APPROVAL`，使用者最後一次過目、逐個裁決。
        自動 approve 會把「沒人看過的生成」寫成 accepted，
        通過率統計立刻失真，而那個數字是這個產品的核心。

        「前端連續呼叫」而不是後端跑迴圈：
        Cloud Run 請求有逾時上限，一鏡 1–3 分鐘，十二鏡必定超時。
        一個 request 一鏡，前端才能顯示「第 N 鏡（共 M 鏡）生成中」，
        而且中途關掉瀏覽器不會留下半段狀態。

    `auto` 只放行「等確認」那道閘門。試拍未通過的檢查不受影響，
    那是花錢前的閘門，一鍵模式也不能跳過。
    """
    from ..schemas.segment import ClipStatus

    state = _load(project_id)
    segment = _segment(state, order)

    # 兩道閘門的順序：選角在前、試拍在後。
    # 順序錯了會讓使用者先被擋在試拍、選完角才發現還要再試拍一次。
    _require_casting(state)

    if not segment.test_shot_approved:
        raise HTTPException(
            status_code=409,
            detail="要先試拍一鏡並確認通過，才能開始批次生成。",
        )

    clips = _ensure_clips(segment)
    waiting = [c for c in clips if c.status is ClipStatus.AWAITING_APPROVAL]
    if waiting and not shot_id and not auto:
        raise HTTPException(
            status_code=409,
            detail=(
                f"鏡頭「{waiting[0].shot_id}」還在等你確認。"
                f"看過之後按通過或重拍，再生下一個。"
            ),
        )

    target = next(
        (c for c in clips if c.shot_id == shot_id),
        next((c for c in clips if c.status is ClipStatus.NOT_STARTED), None),
    )
    if target is None:
        raise HTTPException(status_code=409, detail="這一段所有鏡頭都生完了。")

    return _run_clip(project_id, state, segment, target)


@router.post("/{project_id}/segments/{order}/batch/{shot_id}/approve")
def approve_clip(project_id: str, order: int, shot_id: str) -> dict:
    """這一鏡看過了，OK。**$0。**"""
    from ..schemas.segment import ClipStatus

    state = _load(project_id)
    segment = _segment(state, order)
    clips = _ensure_clips(segment)

    clip = next((c for c in clips if c.shot_id == shot_id), None)
    if clip is None:
        raise HTTPException(status_code=404, detail=f"這一段沒有鏡頭「{shot_id}」")
    if not clip.uri:
        raise HTTPException(status_code=409, detail="這一鏡還沒生成，沒有東西可以通過。")

    clip.status = ClipStatus.APPROVED

    # 結論產生了，這一刻才是 ledger 該收到紀錄的時候。
    _flush_ledger(state, segment, clip, "accepted")

    project_store.get_store().save(state)
    return _batch_payload(project_id, segment, state)


@router.post("/{project_id}/segments/{order}/batch/{shot_id}/take/{attempt}")
def choose_take(project_id: str, order: int, shot_id: str, attempt: int) -> dict:
    """把這一鏡「採用的版本」切到指定的 take。$0 —— 不生成任何東西。

    重拍不保證比上一次好，而每一次生成都是使用者花錢買的資產。
    這條路由把「用哪一次拍的」還給導演：切換只改 uri 等頂層欄位，
    takes 列表與所有檔案原封不動，隨時可以再切回去。

    generated_at 更新為現在 —— 這會讓已接好的段落成片顯示「過期」，
    使用者重接一次（$0）成片才會用新選的 take。
    """
    state = _load(project_id)
    segment = _segment(state, order)
    clip = next((c for c in segment.clips if c.shot_id == shot_id), None)
    if clip is None:
        raise HTTPException(status_code=404, detail=f"這一段沒有鏡頭「{shot_id}」")
    take = next((t for t in clip.takes if t.attempt == attempt), None)
    if take is None:
        raise HTTPException(
            status_code=404,
            detail=f"這一鏡沒有第 {attempt} 次的成品（共 {len(clip.takes)} 個 take）",
        )
    if clip.uri == take.uri:
        return _batch_payload(project_id, segment, state)

    clip.uri = take.uri
    clip.seconds = take.seconds
    clip.width = take.width
    clip.height = take.height
    clip.size_bytes = take.size_bytes
    clip.tier = take.tier or clip.tier
    clip.generated_at = datetime.now(timezone.utc).isoformat()
    # 選了舊 take 等於人工裁決「這個能用」：失敗狀態的鏡頭因此回到待確認
    from ..schemas.segment import ClipStatus

    if clip.status is ClipStatus.FAILED:
        clip.status = ClipStatus.AWAITING_APPROVAL
        clip.error = ""
    clip.warnings = [w for w in clip.warnings if not w.startswith("↺")]
    clip.warnings.append(f"↺ 已改用第 {attempt} 次拍的版本。")
    project_store.get_store().save(state)
    return _batch_payload(project_id, segment, state)


@router.post("/{project_id}/segments/{order}/batch/{shot_id}/retry")
def retry_clip(project_id: str, order: int, shot_id: str, mute: bool = False) -> dict:
    """這一鏡不滿意，重拍。這會花錢。

    累計花費不歸零，`cost_usd` 累加。
    重拍三次的鏡頭要看得出它花了三倍，否則「這一段花了多少」會失真。

    mute=true 是「無聲重拍」，內容安全審查的出路：
    台詞句被 Google Responsible AI 擋下時（實測整鏡被拒、拿掉台詞即過），
    畫面照生、這句台詞不進音軌，改由字幕層呈現，劇本一個字不用改。
    """
    state = _load(project_id)
    segment = _segment(state, order)
    clips = _ensure_clips(segment)

    clip = next((c for c in clips if c.shot_id == shot_id), None)
    if clip is None:
        raise HTTPException(status_code=404, detail=f"這一段沒有鏡頭「{shot_id}」")

    # 按下重拍 = 對上一次生成下了「不能用」的判決。
    # 必須在重新生成之前落地，否則新的一次會覆蓋掉舊的 pending_ledger，
    # 那筆被否決的紀錄就永遠消失了，
    # 而「什麼樣的鏡頭會被打回來」正是這個系統最該學的東西。
    #
    # 看片檢核的發現跟著進 rejection_reason：
    # 提煉層讀 ledger 時才看得到
    # 「被打回的鏡頭常帶著『多出一個人形』的檢核發現」這種模式。
    findings = (clip.audit or {}).get("findings") or []
    reason = "人工重拍"
    if findings:
        reason += "；看片檢核：" + "；".join(findings)[:150]
    from ..verify import service as verify_service

    if verify_service.rejection_note(clip):
        reason += "；" + verify_service.rejection_note(clip)[:200]
    _flush_ledger(state, segment, clip, "rejected", reason=reason)

    return _run_clip(project_id, state, segment, clip, mute_dialogue=mute)


# ---------------------------------------------------------------------------
# 學習迴路：把真實生成寫進 ClickHouse
#
# 真實生成必須寫進 ledger，否則哪些約束套用了、預測準不準、重拍幾次
# 全部丟掉，統計層只剩模擬資料。
#
# 寫入時機見 schemas.segment.PendingLedgerFacts 的說明：
# 裁決的那一刻寫，不是 API 回應的那一刻。
# ---------------------------------------------------------------------------

def _shot_of(segment: Segment, shot_id: str):
    """從鏡頭表撈 Shot。撈不到回 None（分鏡表換過就會這樣）。"""
    return next((s for s in segment.shot_list.shots if s.shot_id == shot_id), None)


def _flush_ledger(state, segment: Segment, clip, outcome: str, reason: str = "") -> None:
    """把 clip 上懸著的事實落成一筆 ledger 紀錄，然後清空。

    任何情況都不能讓這裡的失敗影響使用者的操作。
    人按「通過」是要把鏡頭標記成通過，統計寫不寫得進去是系統內部的事。
    ledger.record_generation 本身已經吞例外了，
    這裡再包一層是為了擋住組裝紀錄時的例外（例如分鏡表換過、撈不到 Shot）。
    """
    facts = getattr(clip, "pending_ledger", None)
    if facts is None:
        return

    try:
        shot = _shot_of(segment, clip.shot_id)
        if shot is not None:
            ledger.record_adjudicated(
                plan_id=f"{state.project_id}:{segment.segment_id}",
                shot=shot,
                facts=facts,
                style_id=state.style_id or "",
                tier=clip.tier or "",
                outcome=outcome,
                rejection_reason=reason,
                output_uri=clip.uri or "",
            )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001 - 統計寫入永遠不能擋住使用者
        logger.error("寫 ledger 失敗（不影響操作）：%s", exc)
    finally:
        # 不管寫成功沒有都要清掉。留著的話下次裁決會把同一次生成再寫一筆，
        # 而重複的紀錄在通過率統計裡是看不出來的，只會讓數字慢慢偏。
        clip.pending_ledger = None


def _correction_block(clip) -> tuple[str, list[str]]:
    """把上一次的看片檢核發現，轉成這一次的修正指令。

    重拍若只是把同一份提示詞原封不動再送一次，同樣的輸入送給同一個模型
    除了隨機性之外沒有任何改變，「一直重拍」就沒用。frame_auditor 已經
    看出的問題（畫面多了人、出現不該有的文字）必須回到重拍的提示詞裡，
    否則「看片 → 記錄」有了、「記錄 → 下次生成」沒有，迴路是斷的。

    用 audit 的結構化欄位組指令，不是把中文 findings 翻過去：
    findings 是寫給人看的（「畫面出現可讀的文字」），
    而模型需要的是可執行的具體要求（「不准出現任何字母、數字、
    可辨識的文字，招牌與螢幕保持空白或模糊」）。
    這跟 _people_boundary 是同一個原則：規則求不住的，用結構化事實說死。

    只在真的有發現時才加。沒問題的鏡頭重拍時提示詞不該變，
    那時候要的就是「同樣的條件再擲一次骰子」。

    Returns:
        (要接在提示詞後面的修正段, 給使用者看的中文說明清單)
    """
    audit = getattr(clip, "audit", None) or {}
    if not audit:
        return "", []

    fixes: list[str] = []
    notes: list[str] = []

    if audit.get("has_readable_text") and not audit.get("allow_text"):
        fixes.append(
            "The previous attempt rendered readable text in the frame. "
            "This time there must be absolutely no letters, words, numbers or "
            "legible characters anywhere: signage, screens and printed matter "
            "stay blank, blurred or out of focus."
        )
        notes.append("上次畫面出現不該有的文字 —— 這次已明確要求所有文字保持空白或模糊")

    expected = audit.get("expected_people")
    got = audit.get("people_count")
    if expected is not None and got is not None:
        if got > expected:
            fixes.append(
                f"The previous attempt showed {got} human figures, but this shot "
                f"must contain exactly {expected}. Remove every extra person: "
                f"no bystanders, no partial figures at the frame edges, "
                f"no people in reflections, mirrors or screens."
            )
            notes.append(f"上次畫面多出人（{got} 人，應該 {expected} 人）—— 這次已逐項禁止")
        elif got < expected:
            fixes.append(
                f"The previous attempt showed only {got} human figures, but this "
                f"shot needs {expected}. Make sure every described character is "
                f"clearly visible in frame."
            )
            notes.append(f"上次角色沒生出來（只有 {got} 人，應該 {expected} 人）—— 這次已要求全部入鏡")

    if not fixes:
        return "", []

    joined = "\n".join(f"- {f}" for f in fixes)
    block = (
        "\n\nCORRECTIONS — this shot was regenerated because the previous "
        "attempt had these specific problems. Fix them:\n" + joined
    )
    return block, notes


def _analyse_rejection(
    state, shot, prompt: str, exc: Exception, *, dialogue_sent: bool = True
) -> dict:
    """一鏡被內容安全審查擋下之後，讀出是哪一段觸發的。

    API 回的是一句籠統的「violates Google's Responsible AI practices」，
    對編劇零指引，尤其在一份幾十鏡的分鏡表裡。失敗時要告訴使用者
    是哪句台詞或哪段描述不過，並給建議。

    只在確實是安全審查時才跑。配額、逾時、網路錯誤跑這個沒有意義，
    還會多花一次 LLM 呼叫。
    分析失敗絕不能蓋掉原本的錯誤：使用者要知道的第一件事是
    「這一鏡沒生出來」，歸因是加值不是必要。
    這是推測。真正的確認是無聲重拍成功與否（那筆錢本來就要花）。

    Args:
        prompt: 被擋的那份完整提示詞（不是重組的，是真的送出去那份）
        dialogue_sent: 這一次有沒有把台詞送出去。無聲重拍又被擋時
            台詞根本不在提示詞裡，還拿台詞去問就會得到「原因是台詞」這種
            不可能的答案，而那正是使用者最需要正確資訊的時候
            （已經試過無聲重拍，需要知道問題出在畫面描述）。

    Returns:
        dict：給 UI 的歸因；判斷不出來或分析失敗時回 {}
    """
    message = str(exc)
    if "內容安全審查" not in message and "Responsible AI" not in message:
        return {}

    from ..agents import content_screener

    try:
        analysis, usage = content_screener.explain_rejection(
            prompt, dialogue=(shot.dialogue or "") if dialogue_sent else ""
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as inner:  # noqa: BLE001
        logger.error("被擋原因分析失敗（不影響錯誤回報）：%s", inner)
        return {}

    state.spend.llm_usd += float(usage.get("cost_usd", 0.0))
    data = analysis.model_dump()
    data["cost_usd"] = round(float(usage.get("cost_usd", 0.0)), 5)
    return data


def _capture_facts(
    *,
    state,
    segment: Segment,
    shot,
    attempt: int,
    tier: str,
    cost_usd: float,
    latency_seconds: int,
    model_id: str,
    has_first_frame: bool,
    prompt: str,
) -> PendingLedgerFacts:
    """抓生成當下的事實。這些只有現在算得準。

    predicted_success_rate 特別重要：它要跟使用者在畫面上看到的那個數字
    是同一個。事後重算會用到這次生成本身寫進去的資料，那是拿答案改考卷，
    校準報告就永遠是準的，也就永遠沒有用。
    """
    style_id = state.style_id or ""
    tier_enum = _tier_enum(tier)
    try:
        similar = ledger.query_similar_shots(shot, tier_enum, style_id)
        estimate = success_predictor.predict_shot(
            shot,
            tier_enum,
            style_id=style_id,
            aspect_ratio=segment.shot_list.aspect_ratio,
            observed_rate=similar.observed_rate,
            sample_size=similar.sample_size,
        )
        predicted = estimate.success_rate
        rules = [c.rule_id for c in constraint_engine.plan_for_shot(shot, style_id).applied]
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001
        # 抓不到就記 0 與空清單。有紀錄總比沒紀錄好，
        # 而 predicted=0 在校準報告裡會落進最低的桶、一眼看得出來是壞資料。
        logger.error("抓生成事實失敗，紀錄仍會寫入但預測值缺失：%s", exc)
        predicted, rules = 0.0, []

    return PendingLedgerFacts(
        attempt=attempt,
        predicted_success_rate=predicted,
        applied_constraints=rules,
        model_id=model_id,
        latency_seconds=latency_seconds,
        cost_usd=cost_usd,
        has_first_frame=has_first_frame,
        prompt_excerpt=prompt[:500],
    )


def _tier_enum(tier: str):
    """字串檔位轉回 QualityTier。認不得就退回 lite，預測用得到它。"""
    from ..schemas.plan import QualityTier

    try:
        return QualityTier(tier)
    except ValueError:
        return QualityTier.LITE_720P


def _run_clip(
    project_id: str, state, segment: Segment, clip, mute_dialogue: bool = False
) -> dict:
    """實際生成一個鏡頭，把結果寫進 clip。

    mute_dialogue：無聲重拍（台詞被內容審查擋下時的出路，見 retry_clip）。
    """
    from datetime import datetime, timezone

    from ..schemas.segment import ClipStatus

    shot = next(
        (s for s in segment.shot_list.shots if s.shot_id == clip.shot_id), None
    )
    if shot is None:
        raise HTTPException(
            status_code=409, detail=f"鏡頭表裡找不到「{clip.shot_id}」，可能分鏡表換過了。"
        )

    tier = (state.chosen_tier.value if state.chosen_tier else None) or config_loader.default_veo_tier()
    per_second = float(config_loader.veo_tier(tier).get("per_second_usd", 0.05))

    # 跟試拍與導出用同一份提示詞組裝。
    # 如果批次用的跟使用者看過的不一樣，那個導出關卡就沒有意義了。
    # ---- 旁白走 TTS，「先念再拍」----
    #
    # 旁白鏡的台詞不進影片模型。三件事一次解決：
    # 畫面裡的人不可能對嘴（影片根本沒有人聲）、音色不會跨鏡漂移
    # （TTS 從頭到尾同一個 voice）、內容審查管不到它。
    #
    # 順序是「先念再拍」：先合成旁白量出秒數，再決定這一鏡生幾秒。
    # 反過來（先拍再念）會遇到旁白比畫面長、話被切掉的問題。
    narration = None
    seconds_override: int | None = None
    _tts_on = bool(
        config_loader.casting().get("narrator", {}).get("tts", {}).get("enabled", True)
    )
    # 每一鏡都記一行「TTS 走了哪條路」。
    # 成功無聲、跳過也無聲的話，事後查不到它到底有沒有被呼叫，
    # 這種分支一定要留痕跡。
    logger.info(
        "TTS 判斷 %s：enabled=%s voice_role=%s 有台詞=%s 無聲重拍=%s",
        shot.shot_id, _tts_on, shot.voice_role, bool(shot.dialogue), mute_dialogue,
    )
    if _tts_on and shot.voice_role == "narrator" and shot.dialogue and not mute_dialogue:
        from ..services import tts as tts_service

        try:
            narration = tts_service.synthesize(shot.dialogue)
            seconds_override = tts_service.clip_seconds_for(narration.seconds)
            state.spend.llm_usd += narration.cost_usd
            logger.info(
                "TTS 成功 %s：%.2fs voice=%s 影片將生 %ss",
                shot.shot_id, narration.seconds, narration.voice, seconds_override,
            )
        except demo_mode.DemoBlocked:
            # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
            raise
        except Exception as exc:  # noqa: BLE001
            # TTS 掛掉不擋生成，退回讓影片模型處理台詞。
            # 沒有旁白的成品仍然可用，整鏡生不出來就什麼都沒有。
            logger.error("旁白合成失敗，退回由影片模型處理：%s", exc)

    prompt = prompt_export.build_prompt(
        shot,
        segment.shot_list,
        state.casting,
        # 旁白已經另外合成的話，影片只生畫面與環境音
        include_dialogue=not mute_dialogue and narration is None,
        tier=tier,
    )["full"]

    # ---- 接首尾幀 ----
    #
    # 分鏡表裡標「接」的鏡頭，拿上一鏡的末幀當這一鏡的起點。
    # 沒有這步的話每一鏡都從零猜，同一個物件會在鏡與鏡之間變色。
    # appearance_tags 鎖得住文字描述，鎖不住色溫與角度。
    #
    # 抽幀失敗不阻斷生成：沒有首幀圖只是接縫比較明顯，
    # 為此讓整鏡生不出來是本末倒置。降級並記進 warnings。
    # 執行期防呆：`first_frame_from` 是 agent 的意圖，
    # 不是可以直接執行的指令。跨場次接幀會把上一場的背景帶進新場景、
    # 鏈拉太長會累積漂移，兩種都在 services/continuity.py 擋掉並說明。
    # 放執行期而不是分鏡後處理，是為了讓已經存好的分鏡表不必重跑就受益。
    decision = continuity.decide(
        shot,
        segment.shot_list.shots,
        max_chain=int(config_loader.veo_tier(tier).get("max_continuity_chain", 4)),
    )
    first_frame = None
    frame_note = decision.note
    # 同場次被鏈長上限強制斷開時，提示詞要明講「場景不變」。
    # 參考圖只鎖角色長相、不鎖場景，斷開之後模型會自己想像一個場景，
    # 這一句是唯一能補回場景延續的地方。
    # 重拍要帶著上一次的失敗證據：否則重拍只是把同一份提示詞
    # 原封不動再送一次，除了隨機性之外什麼都沒變。
    # 看片檢核已經看出的問題必須回到提示詞裡。
    correction, correction_notes = _correction_block(clip)
    local = local_engine.is_local(tier)
    if local:
        # Greenlight Local：本機檔位整份換成作者管線的 H3 六行寫法。
        #    下面兩段是給雲端模型補的英文句子，接在已驗證的中文六行後面只會汙染它 —— 本機不加。
        prompt, local_refs = local_engine.prompt_for(shot, segment, project_id, state.style_id)
    if correction and not local:
        prompt = prompt + correction
        logger.info("重拍帶上次檢核發現 %s：%s", clip.shot_id, "；".join(correction_notes))

    if decision.same_scene_break and not local:
        prompt = (
            prompt
            + "\nThis shot takes place in the exact same location as the "
            "previous shot in this scene: the same room, the same set dressing, "
            "the same lighting direction and the same colour grade. "
            "Do not move to a different place."
        )
    reference_images = _reference_images_for(shot, state, segment.order)
    if decision.allowed:
        prev = next(
            (c for c in segment.clips if c.shot_id == decision.source_shot_id), None
        )
        if prev and prev.uri:
            source = media_store.load(prev.uri)
            if source:
                try:
                    first_frame = frame_extract.last_frame(source[0])
                except frame_extract.FrameExtractError as exc:
                    frame_note = f"抽不到 {decision.source_shot_id} 的末幀，改用硬切：{exc}"
            else:
                frame_note = f"讀不回 {decision.source_shot_id} 的影片，改用硬切。"
        else:
            frame_note = f"上一鏡 {decision.source_shot_id} 還沒生成，這一鏡改用硬切。"
    elif decision.scene_anchor_source:
        # 斷鏈但同場次：來源鏡的末幀降級當「場景參考圖」跟角色卡一起送。
        # 臉靠卡（人手一張）、場景靠這張末幀 —— 光靠提示詞一句
        # 「場景不變」是文字錨，實際觀察過模型仍會換場景。
        # 抽不到就算了：場景參考是加分項，同樣不為它擋整鏡。
        anchor = next(
            (c for c in segment.clips if c.shot_id == decision.scene_anchor_source),
            None,
        )
        if anchor and anchor.uri:
            source = media_store.load(anchor.uri)
            if source:
                try:
                    scene_frame = frame_extract.last_frame(source[0])
                    reference_images = reference_images + [
                        {
                            "name": "",
                            "tags": "",
                            "data": scene_frame,
                            "mime": "image/jpeg",
                            "scene": True,
                        }
                    ]
                    frame_note = (
                        frame_note
                        + f" 已附 {decision.scene_anchor_source} 的末幀當場景參考。"
                    )
                except frame_extract.FrameExtractError:
                    pass

    # take 功能上線前生成的鏡頭：現任版本不在 takes 裡，重拍前先補記，
    # 否則第一次重拍之後舊版選不回去（檔案還在媒體庫，選擇列上卻沒有它）。
    if clip.uri and not clip.takes:
        clip.takes.append(
            TakeRecord(
                attempt=max(clip.attempts, 1),
                uri=clip.uri,
                seconds=clip.seconds,
                width=clip.width,
                height=clip.height,
                size_bytes=clip.size_bytes,
                tier=clip.tier,
                generated_at=clip.generated_at,
            )
        )

    clip.attempts += 1
    started_at = time.monotonic()
    # 每個 take 獨立檔名（s014_02_a3）：重拍不覆蓋上一次的成品。
    # 每一次生成都是使用者花錢買的資產，導演要有選回舊 take 的權利。
    take_name = f"{clip.shot_id}_a{clip.attempts}"

    # 已經有基底、只是還沒續拍完 → 只補續拍，不重生。
    # Extend 吃任意 mp4（實測過），所以已存的那段 8 秒可以直接往後接。
    # 重生基底要 $0.40，只補續拍是 $0.35，而且重生會讓已經看過
    # 覺得 OK 的前 8 秒變成另一段影片，那才是真的浪費。
    existing = media_store.load(clip.uri) if clip.uri else None
    resume_extend = bool(
        _extend_enabled()
        and existing
        and shot.extensions
        and clip.seconds > 0
        and clip.seconds < shot.total_seconds - 1
    )

    try:
        if resume_extend:
            result = video_gen.ClipResult(
                uri=clip.uri,
                seconds=clip.seconds,
                width=clip.width,
                height=clip.height,
                size_bytes=clip.size_bytes,
                tier=tier,
            )
        elif local:
            # Greenlight Local：本機 ComfyUI（首幀接末幀還沒接進來，先只用提示詞）
            result = local_engine.generate(project_id, take_name, prompt, shot, ref_paths=local_refs)
        else:
            result = video_gen.generate_clip(
                project_id,
                take_name,
                prompt,
                tier=tier,
                aspect_ratio=segment.shot_list.aspect_ratio,
                duration_seconds=shot.base_duration_seconds,
                first_frame=first_frame,
                # 接幀與參考圖互斥（兩個引擎都是），一律照傳，
                # 由 video_gen 依「這一鏡到底有沒有接幀」決定丟掉哪一邊，
                # 並在 warnings 說明少帶了什麼。
                # 斷鏈鏡的列表尾端可能多一張 scene=True 的場景參考
                # （上面 scene_anchor 那段補的）。
                reference_images=reference_images,
                # 旁白鏡的長度由 TTS 的實際秒數決定（見上面「先念再拍」）
                seconds_override=seconds_override,
            )

        # 續拍必須在這裡跑：只生 base_duration_seconds 就結束的話，
        # `shot.extensions` 完全沒被處理，規劃 15 秒的鏡頭只生出 8 秒，
        # 而且不會有任何錯誤，鏡頭表寫 15s、成品是 8s，兩邊沒人對照。
        # 續拍關閉時，鏡頭表裡殘留的 extensions 一律忽略：
        # 舊的分鏡表是在關閉之前規劃的，不該讓它繼續生出壞掉的長鏡。
        planned_extends = shot.extensions if _extend_enabled() else 0

        done_extends = 0
        if resume_extend:
            # 已經接過幾次，從長度反推：每次 +7 秒
            add = int(
                config_loader.pricing().get("veo", {}).get("extend", {}).get(
                    "seconds_per_extension", 7
                )
            )
            done_extends = max(
                0, round((clip.seconds - shot.base_duration_seconds) / add)
            )

        for i in range(done_extends, planned_extends):
            source = media_store.load(result.uri)
            if not source:
                result.warnings.append(f"第 {i + 1} 次續拍前讀不回上一段，續拍中止。")
                break
            result = video_gen.extend_clip(
                project_id,
                # 續拍存回「這一個 take」的檔案（resume 時是舊 take 的檔名，
                # 新生成時是本次 take_name），不動別的 take
                _uri_stem(result.uri),
                source[0],
                prompt,
                tier=tier,
            )
    except video_gen.VideoGenError as exc:
        # api_retry 已經替暫時性錯誤重試過了，走到這裡代表真的不行。
        clip.status = ClipStatus.FAILED
        clip.error = str(exc)[:400]

        # 被內容安全審查擋下時，指出是哪一段觸發的。
        # 只回「被 Google 內容安全審查擋下」在一份幾十鏡的分鏡表裡
        # 等於什麼都沒說。
        clip.rejection = _analyse_rejection(
            state, shot, prompt, exc, dialogue_sent=not mute_dialogue
        )

        # 失敗也要進 ledger，outcome='error'。
        # ClickHouse 的統計查詢會把 error 排除在通過率之外（那是基礎設施
        # 問題不是鏡頭難度），但排除不等於不記：
        # 「這個檔位這種鏡頭常常直接失敗」本身就是要學的東西，
        # 而且錯誤率突然上升是配額或審核政策變了的最早信號。
        clip.pending_ledger = _capture_facts(
            state=state,
            segment=segment,
            shot=shot,
            attempt=clip.attempts,
            tier=tier,
            cost_usd=0.0,
            latency_seconds=int(time.monotonic() - started_at),
            model_id="",
            has_first_frame=first_frame is not None,
            prompt=prompt,
        )
        _flush_ledger(state, segment, clip, "error", reason=str(exc)[:120])

        project_store.get_store().save(state)
        raise HTTPException(status_code=502, detail=f"{clip.shot_id} 生成失敗：{exc}") from exc

    # ---- 把旁白混進音軌 ----
    #
    # 影片模型生的是畫面與環境音，旁白是 TTS 另外合成的，
    # 在這裡合成一支。環境音會被壓低讓旁白浮上來（比例在 config）。
    #
    # 混音失敗不擋流程：沒有旁白的成品仍然可用（畫面與環境音都在），
    # 為了配音失敗把整鏡作廢是本末倒置。失敗記進 warnings 讓人看得到。
    if narration is not None:
        from ..services import audio_mix

        try:
            source = media_store.load(result.uri)
            if source:
                mixed = audio_mix.mix_narration(
                    source[0], narration.wav, narration.seconds
                )
                # 混音存回同一個 take 的檔案（不是 shot_id —— 那會蓋到別的 take）
                media_store.save(project_id, _uri_stem(result.uri), mixed, "video/mp4")
                result.warnings.append(
                    f"✓ 旁白由 {narration.voice} 配音（{narration.seconds}s）"
                    f"，已混進音軌 —— 畫面裡沒有人對嘴。"
                )
                # 旁白比一鏡的上限還長時要講。畫面會凍結最後一幀
                # 陪它念完，話不會斷，但那幾秒是靜止的；使用者
                # 有權知道，而且可能寧可回去把那句旁白拆成兩句。
                if narration.seconds > result.seconds + 0.2:
                    frozen = round(narration.seconds - result.seconds, 1)
                    result.warnings.append(
                        f"這句旁白要念 {narration.seconds}s，但一鏡最長 "
                        f"{result.seconds}s —— 畫面已自動凍結最後一幀 {frozen}s "
                        f"陪它念完（話沒有被切斷）。想避免靜止畫面的話，"
                        f"把這句旁白拆成兩句、重新分鏡。"
                    )
            else:
                result.warnings.append("讀不回剛生成的影片，旁白沒有混進去。")
        except demo_mode.DemoBlocked:
            # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("旁白混音失敗（影片仍可用）：%s", exc)
            result.warnings.append(f"旁白混音失敗，這一鏡只有環境音：{exc}")

    cost = round(result.seconds * per_second, 3)
    clip.status = ClipStatus.AWAITING_APPROVAL
    clip.uri = result.uri
    clip.seconds = result.seconds
    clip.width = result.width
    clip.height = result.height
    clip.size_bytes = result.size_bytes
    clip.tier = tier
    clip.cost_usd = round(clip.cost_usd + cost, 3)
    clip.generated_at = datetime.now(timezone.utc).isoformat()
    # 這次成品記成一個 take（resume 續拍延長的是同一個檔，更新而不是多記一筆）
    existing_take = next((t for t in clip.takes if t.uri == result.uri), None)
    if existing_take is None:
        clip.takes.append(
            TakeRecord(
                attempt=clip.attempts,
                uri=result.uri,
                seconds=result.seconds,
                width=result.width,
                height=result.height,
                size_bytes=result.size_bytes,
                tier=tier,
                generated_at=clip.generated_at,
            )
        )
    else:
        existing_take.seconds = result.seconds
        existing_take.size_bytes = result.size_bytes
        existing_take.generated_at = clip.generated_at
    clip.warnings = list(result.warnings)
    # 修正了什麼要講出來，不講的話「重拍會學習」對使用者就是一句空話
    for note in correction_notes:
        clip.warnings.append(f"↻ {note}")
    if frame_note:
        clip.warnings.append(frame_note)
    if mute_dialogue and shot.dialogue:
        clip.warnings.append(
            "這一鏡以無聲版重拍（台詞被內容安全審查擋下）——"
            "台詞會由字幕呈現，劇本原文未動。"
        )
    clip.error = ""

    # ---- 音色鎖定 ----
    #
    # 角色的音色第一次出現後就鎖定，不能每鏡隨機生成。
    #
    # Veo 沒有音色 ID 可以指定，所以做法是：第一次生出有台詞的鏡頭之後，
    # 讓 Gemini 聽那段影片，把實際的音色描述回寫成角色屬性。
    # 之後每一鏡都注入同一串描述，那是這個系統能給的最強一致性保證。
    #
    # 只鎖一次。每一鏡都重新分析等於每一鏡都可能換個說法，
    # 那就回到隨機了。
    voice_cost = _lock_voice_if_needed(state, segment, shot, clip)

    # ---- 看片檢核（雙向迴路的回向）----
    if local:
        # Greenlight Local：本機查證取代雲端看片（同一個位置、同一個迴路：查證 → 診斷 → 重拍時帶修法、退件原因進紀錄）
        from ..verify import service as verify_service

        audit_cost = verify_service.after_generate(state, segment, shot, clip, prompt, take_name)
    else:
        audit_cost = _audit_generated(state, shot, clip)

    # ---- 抓事實，等人裁決 ----
    #
    # 記的是影片生成的錢，不含上面那筆音色鎖定。
    # ledger 的 cost_usd 是用來回答「這種鏡頭要花多少錢才生得出來」，
    # 而音色鎖定是每個角色只做一次、不隨鏡頭數成長。
    # 混進去會讓後面每一鏡的成本預測都被墊高。
    # 段落與專案的累計花費仍然含它，那邊要的是「真的花了多少」。
    clip.pending_ledger = _capture_facts(
        state=state,
        segment=segment,
        shot=shot,
        attempt=clip.attempts,
        tier=tier,
        cost_usd=cost,
        latency_seconds=int(time.monotonic() - started_at),
        model_id=result.model_id,
        has_first_frame=first_frame is not None,
        prompt=prompt,
    )

    cost = round(cost + voice_cost + audit_cost, 4)

    segment.spent_usd = round(segment.spent_usd + cost, 3)
    state.spend.video_usd = round(state.spend.video_usd + cost, 3)
    state.record_decision(
        choice=f"第 {segment.order} 段生成 {clip.shot_id}"
        + (f"（第 {clip.attempts} 次）" if clip.attempts > 1 else ""),
        rationale=f"{result.seconds} 秒 · ${cost}",
    )
    project_store.get_store().save(state)

    return _batch_payload(project_id, segment, state)


@router.delete("/{project_id}/segments/{order}/batch")
def reset_batch(project_id: str, order: int, keep_files: bool = True) -> dict:
    """把這一段的批次進度清空，從頭重來。$0。

    改了生成條件（例如接幀、音色鎖定）之後，舊鏡頭是在不同條件下
    生出來的。混在一起看，既判斷不了新機制有沒有用，成片的風格也會
    前後不一。使用者換了風格、換了角色、改了分鏡之後，同樣會想把
    這一段整個重來。

    一併解除角色的音色鎖定：音色是從「第一個生成的鏡頭」聽出來的，
    重來之後那一鏡會不存在。留著舊鎖等於用一支已經不在的影片
    決定整片的聲音。

    Args:
        keep_files: 保留已生成的影片檔（預設保留）。
            檔案已經花錢生出來了，清掉的是進度不是成果；
            使用者事後想比對「重來前後」時會需要它們。
    """
    state = _load(project_id)
    segment = _segment(state, order)

    summary = reset_segment_progress(state, segment)

    state.record_decision(
        choice=f"第 {order} 段批次重置",
        rationale=f"清掉 {summary['cleared_clips']} 個鏡頭進度"
        + ("、成片與字幕" if summary["had_output"] else "")
        + (
            f"，解除音色鎖定：{'、'.join(summary['unlocked_voices'])}"
            if summary["unlocked_voices"]
            else ""
        ),
    )
    project_store.get_store().save(state)

    return {
        "cleared_clips": summary["cleared_clips"],
        "unlocked_voices": summary["unlocked_voices"],
        "files_kept": keep_files,
        "batch": _batch_payload(project_id, segment, state),
    }


def reset_segment_progress(state, segment) -> dict:
    """清空一段的生成進度：批次、試拍、成片、字幕、音色鎖。影片檔不刪。

    抽成共用函式：reset_batch 與「重新分鏡」（plan_shots force）
    都要做完全相同的清理，分兩份寫的話，之後補清理項目一定有一邊漏掉。

    試拍一起清：試拍就是批次的第一鏡，
    重置後留著「已通過」等於用一支已經作廢的影片放行新批次。

    檔案一律不刪：生成物沉到媒體庫，由使用者逐項決定去留。
    """
    from ..schemas.segment import ClipStatus

    cleared = len([c for c in segment.clips if c.status is not ClipStatus.NOT_STARTED])
    segment.clips = []
    segment.spent_usd = 0.0

    # 成片與字幕：清的是「這一段有成品」這個宣稱，不是檔案本身。
    had_output = bool(segment.output_uri)
    segment.output_uri = None
    segment.output_seconds = 0.0
    segment.output_built_at = ""
    segment.subtitles = {}
    segment.burned = {}

    if segment.test_shot or segment.test_shot_approved:
        segment.test_shot = None
        segment.test_shot_id = None
        segment.test_shot_approved = False

    unlocked = []
    if state.casting and segment.shot_list:
        shot_ids = {s.shot_id for s in segment.shot_list.shots}
        for session in state.casting.sessions:
            if session.voice_locked_from in shot_ids:
                session.voice_locked_from = None
                chosen = session.selected
                if chosen:
                    chosen.voice_tags = ""
                unlocked.append(session.character_name)

    return {
        "cleared_clips": cleared,
        "had_output": had_output,
        "unlocked_voices": unlocked,
    }


def _audit_generated(state, shot, record) -> float:
    """生完看一次片（frame_auditor），把發現存進三個地方。回傳花費。

    這是雙向迴路的回向，發現要留紀錄、可翻查：

        1. `record.audit`：每一鏡的檢核結果永久存在專案裡，隨時翻查
        2. `record.warnings`：鏡頭卡上直接看得到
        3. 專案決策日誌：有發現就記一筆，時間軸查得到；
           之後人按重拍時，發現會跟著 rejection_reason 進 ClickHouse，
           提煉層就「記得」這種鏡頭容易出什麼問題

    檢核不裁決，只標出來，通過與否永遠是人按的。
    模型看片也會看錯（倒影、雕像），自動打回會把誤判變成白花的錢。
    失敗不阻斷，跟音色鎖定同一個原則，人已經花錢拿到影片了。
    """
    from ..schemas.casting import CharacterKind
    from ..schemas.shot import ContentFlag

    if not record.uri:
        return 0.0
    source = media_store.load(record.uri)
    if not source:
        return 0.0

    # 期望人數只算「人形」角色：以光帶等抽象形體出現的非人角色，
    # 出現在畫面裡不算「人」，Gemini 的清點規則也同樣排除抽象形體。
    expected: int | None
    if ContentFlag.CROWD in shot.content_flags:
        expected = None  # 人群戲本來就該有背景人物，不查人數
    else:
        expected = 0
        for name in shot.characters_in_frame or []:
            session = state.casting.session_for(name) if state.casting else None
            if session is None or session.kind is not CharacterKind.NON_HUMAN:
                expected += 1

    allow_text = ContentFlag.TEXT_IN_FRAME in shot.content_flags

    try:
        from ..agents import frame_auditor

        audit, findings, usage = frame_auditor.audit_clip(
            source[0],
            expected_people=expected,
            allow_text=allow_text,
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001
        record.warnings.append(f"看片檢核失敗（不影響生成）：{exc}")
        return 0.0

    record.audit = {
        "people_count": audit.people_count,
        "expected_people": expected,
        "has_readable_text": audit.has_readable_text,
        "allow_text": allow_text,
        "note": audit.note,
        "findings": findings,
        "model": usage.get("model", ""),
        "cost_usd": usage.get("cost_usd", 0.0),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    for f in findings:
        record.warnings.append(f"⚠ 看片檢核：{f}。")

    if findings:
        state.record_decision(
            choice=f"看片檢核 {record.shot_id}：{len(findings)} 項發現",
            rationale="；".join(findings)[:200],
            followed_recommendation=False,
        )
        logger.warning("frame_audit %s: %s", record.shot_id, "；".join(findings))
    else:
        logger.info(
            "frame_audit %s: OK（人形 %s/期望 %s）",
            record.shot_id,
            audit.people_count,
            expected,
        )

    return float(usage.get("cost_usd", 0.0))


def _lock_voice_if_needed(state, segment: Segment, shot, clip) -> float:
    """第一次聽到某個角色說話時，把音色鎖進那個角色。回傳花費。

    只在三個條件同時成立時才做：
      · 這一鏡有台詞（沒聲音就沒東西可聽）
      · 這一鏡有明確的說話角色
      · 那個角色還沒鎖過音色

    失敗不影響生成：音色描述缺了只是一致性差一點，
    為此讓已經生好的鏡頭失敗是本末倒置。
    """
    if not shot.dialogue or not clip.uri:
        return 0.0

    # 用 speaker 不用 characters_in_frame：音色要鎖給說話的人。
    # 沒指明說話者就不鎖：鎖錯人會讓那個角色之後每一鏡都用別人的聲音，
    # 比不鎖更難救（不鎖只是隨機，鎖錯是穩定地錯）。
    if not shot.speaker or not state.casting:
        return 0.0

    session = state.casting.session_for(shot.speaker)
    if session is None or session.voice_locked_from:
        return 0.0

    chosen = session.selected
    if chosen is None:
        return 0.0

    source = media_store.load(clip.uri)
    if not source:
        return 0.0

    try:
        from ..agents import voice_profiler

        profile, usage = voice_profiler.profile_voice(source[0])
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001
        clip.warnings.append(f"音色分析失敗，這個角色的音色尚未鎖定：{exc}")
        return 0.0

    if not profile.has_speech or not profile.voice_tags.strip():
        # 沒聽到人聲就不要鎖，鎖一個空的等於永遠不會再試
        return float(usage.get("cost_usd", 0.0))

    chosen.voice_tags = profile.voice_tags.strip()
    session.voice_locked_from = shot.shot_id
    state.record_decision(
        choice=f"{session.character_name} 音色鎖定（來自 {shot.shot_id}）",
        rationale=profile.note or profile.voice_tags[:60],
    )
    return float(usage.get("cost_usd", 0.0))


# ═══════════════════════════════════════════════════════════════════════════
# 接片與字幕
# ═══════════════════════════════════════════════════════════════════════════
#
# 字幕外掛不燒錄：影片只生一次，字幕想出幾種語言都行。
# 燒進畫面的話，換一種語言就要把整支重新輸出。
# 這也是 constraints.yaml 的 no_subtitle_burn_in 一路在阻止 Veo
# 自己燒字幕的原因：把決定權留到最後一刻。


@router.post("/{project_id}/segments/{order}/assemble")
def assemble_segment(project_id: str, order: int) -> dict:
    """把已生成的片段接成一支完整影片。$0（純本機處理，不呼叫任何模型）。"""
    from ..schemas.segment import ClipStatus
    from ..services import assembler

    state = _load(project_id)
    segment = _segment(state, order)
    clips = _ensure_clips(segment)

    usable = [c for c in clips if c.uri]
    if not usable:
        raise HTTPException(status_code=409, detail="這一段還沒有任何生成好的鏡頭。")

    pending = [c for c in clips if c.status is ClipStatus.NOT_STARTED]
    # 還沒被確認的鏡頭也要點名：接片收的是「有檔案的」，包含還在等確認的。
    # 整個產品的原則是「每一鏡都有人看過才算數」，接片卻默默把沒看過的
    # 接進去，兩邊打架。不擋（先睹為快是合理需求），但要講。
    unapproved = [c for c in usable if c.status is not ClipStatus.APPROVED]
    try:
        uri, total = assembler.concat(project_id, usable, f"segment{order}_final")
    except assembler.AssembleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    segment.output_uri = uri
    segment.output_seconds = total
    segment.output_built_at = datetime.now(timezone.utc).isoformat()
    # 舊燒錄檔對應的是上一版成片：清掉「已燒好」的宣稱，檔案留在媒體庫。
    segment.burned = {}
    project_store.get_store().save(state)

    # ---- 提煉：每完成一段提煉一次 ----
    #
    # 一段做完才提煉，是因為那時才有足夠樣本；
    # 每一鏡都提煉一次會拿 1、2 筆資料去歸納，生出來的是雜訊。
    #
    # 三條紀律：
    #    1. 失敗不能拖死接片。人已經拿到成片了，統計是系統內部的事。
    #    2. 不靜默，結果放進回應裡。否則提煉 job 空轉、回 HTTP 200
    #       卻什麼都沒做，沒有人會發現。
    #    3. 樣本不足時 refinery 自己會拒絕並回一個說得出理由的失敗，
    #       這裡不用先判斷。
    refine_result: dict = {"ok": False, "reason": "未執行"}
    try:
        refine_result = refinery.refine(state.style_id or "")
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("提煉失敗（不影響接片）：%s", exc)
        refine_result = {"ok": False, "reason": f"提煉例外：{exc}"}

    return {
        "output_url": f"/api/projects/{project_id}/segments/{order}/download",
        "seconds": total,
        "clip_count": len(usable),
        # 系統這一段學到了什麼。ok=False 帶著理由，不是靜靜地什麼都沒發生。
        "memory": {
            "refined": refine_result.get("ok", False),
            "reason": refine_result.get("reason", ""),
            "sample_size": refine_result.get("sample_size", 0),
            "cost_usd": refine_result.get("cost_usd", 0.0),
        },
        # 誠實回報缺了哪幾鏡：接片會靜靜地把沒生成的跳過，
        # 使用者拿到一支「短了兩鏡」的成片卻不知道。
        "missing_shots": [c.shot_id for c in pending],
        "unapproved_shots": [c.shot_id for c in unapproved],
        "warning": " ".join(
            w
            for w in [
                (
                    f"有 {len(pending)} 個鏡頭還沒生成，成片會少這幾段。"
                    if pending
                    else ""
                ),
                (
                    f"有 {len(unapproved)} 個鏡頭還沒按通過，也一起接進去了"
                    f"（{'、'.join(c.shot_id for c in unapproved)}）。"
                    if unapproved
                    else ""
                ),
            ]
            if w
        ),
    }


def _build_srt(state, segment, lang: str) -> str:
    """組出某個語言的 SRT 字串。需要翻譯時翻一次並存進專案。

    字幕有兩個出口：下載 .srt 檔、以及燒進影片。
    兩邊都需要「拿到某語言的 SRT」，而那件事本身有成本
    （非原文語言要呼叫翻譯、要記帳、要快取）。
    分兩份寫的話，快取一定會有一邊漏掉，同一種語言被收兩次錢。

    翻譯結果寫進 `segment.subtitles[lang]` 快取並立刻存檔，
    同一種語言不重複付費。呼叫端不用自己再存一次。

    原文語言（`state.film_language`）走 $0 路徑，不進翻譯。

    Args:
        state: 專案狀態（要讀 film_language、寫 spend 與 subtitles 快取）。
        segment: 段落。
        lang: 目標語言碼（zh / en / ja）。

    Returns:
        SRT 格式字串。

    Raises:
        HTTPException(409): 這一段沒有任何台詞。
        HTTPException(502): 翻譯失敗。
    """
    from ..agents import subtitle_translator
    from ..services import assembler

    clips = [c for c in _ensure_clips(segment) if c.uri]
    cues = assembler.build_cues(clips, segment.shot_list.shots)
    if not cues:
        raise HTTPException(status_code=409, detail="這一段沒有任何台詞。")

    film_lang = state.film_language or "zh"
    if lang == film_lang:
        return assembler.to_srt(cues)

    cached = segment.subtitles.get(lang)
    if cached:
        return cached

    try:
        mapping, usage = subtitle_translator.translate_cues(
            [(c.index, c.text) for c in cues], lang
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"字幕翻譯失敗：{exc}") from exc

    for cue in cues:
        cue.text = mapping.get(cue.index, cue.text)
    body = assembler.to_srt(cues)

    # 存起來，同一種語言不重複付費
    segment.subtitles[lang] = body
    state.spend.llm_usd += usage.get("cost_usd", 0.0)
    project_store.get_store().save(state)
    return body


@router.post("/{project_id}/assemble-film")
def assemble_film(project_id: str) -> dict:
    """把所有段落的成片接成一部完整的片。$0（純本機 ffmpeg，`-c copy`）。

    接片只到「段落」為止的話，一部分成三章的片，使用者會拿到三支 mp4，
    然後要自己想辦法接起來。段落接成片才是最終成品。

    只接已經接過片的段落。沒有 output_uri 的段落會被跳過，
    並在 `missing_segments` 誠實回報，否則接片會靜靜地少幾段，
    使用者拿到一支短的片卻不知道為什麼。

    全片與段落成片兩個都留著。重接某一段之後全片會過期，
    那是正常狀態不是錯誤；留著段落成片才能只重接那一段。

    走 `-c copy` 不重新編碼：段落成片都是同一條路產出的，
    編碼一致。真的不一致時 concat_sources 會自動退回重編碼。

    Returns:
        {download_url, seconds, segment_count, missing_segments[], warning}。
    """
    from ..services import assembler

    state = _load(project_id)
    if not state.segments:
        raise HTTPException(status_code=409, detail="這個專案還沒有分段。")

    ready = [s for s in state.segments if s.output_uri]
    missing = [s for s in state.segments if not s.output_uri]

    if not ready:
        raise HTTPException(
            status_code=409,
            detail="還沒有任何段落接過片。先把段落一個一個接好，再合成全片。",
        )

    sources = [
        (s.output_uri or "", float(s.output_seconds or 0), f"第 {s.order} 段")
        for s in ready
    ]

    try:
        uri, total = assembler.concat_sources(project_id, sources, "film_final")
    except assembler.AssembleError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    state.film_output_uri = uri
    state.film_output_seconds = total
    state.film_built_at = datetime.now(timezone.utc).isoformat()
    # 段落內容變了、全片字幕就過期了，一起清掉，下次要用會重算。
    state.film_subtitles = {}
    # 舊燒錄檔對應舊全片，清宣稱不清檔案（理由同段落層）。
    state.film_burned = {}
    state.record_decision(
        choice=f"合成全片（{len(ready)} 段）",
        rationale=f"{total:.0f} 秒"
        + (f"，缺 {len(missing)} 段還沒接片" if missing else ""),
    )
    project_store.get_store().save(state)

    return {
        "download_url": f"/api/projects/{project_id}/film/download",
        "seconds": total,
        "segment_count": len(ready),
        "missing_segments": [s.order for s in missing],
        "warning": (
            f"有 {len(missing)} 段還沒接片（第 "
            f"{'、'.join(str(s.order) for s in missing)} 段），全片會少這幾段。"
            if missing
            else ""
        ),
    }


def _serve_media(uri: str, *, filename: str, missing_detail: str) -> Response:
    """把一個媒體 URI 送給瀏覽器。雲端轉去 GCS 直供，本機自己分塊傳。

    「讀進記憶體 → 整包回傳」在雲端有三個代價：撞 Cloud Run 的 32 MiB
    非串流上限（成片 36.6 MB，實測直接失敗）、把整支影片塞進 512 MiB
    的容器、以及每次播放都吃服務端的頻寬。
    檔案本來就在 Cloud Storage，讓瀏覽器直接去拿，服務端只負責授權。

    三個下載 route 共用這一支。改回各自 `Response(content=...)`
    會讓大檔案在雲端再次靜默失敗。

    Args:
        uri: 媒體 URI（`gs://` 或 `local://`）。
        filename: 給瀏覽器的下載檔名。
        missing_detail: 檔案讀不到時的錯誤訊息（各 route 語意不同）。

    Returns:
        302 轉址到 GCS 簽名網址，或本機檔案的分塊串流。
    """
    from .media import stream_bytes

    url = media_store.signed_url(uri)
    if url:
        return RedirectResponse(url, status_code=302)

    loaded = media_store.load(uri)
    if not loaded:
        raise HTTPException(status_code=404, detail=missing_detail)

    data, mime = loaded
    return StreamingResponse(
        stream_bytes(data),
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
            # 讓瀏覽器可以拖時間軸，而不是整支載完才能播
            "Accept-Ranges": "bytes",
        },
    )


@router.get("/{project_id}/film/download")
def download_film(project_id: str) -> Response:
    """下載整部片。"""
    state = _load(project_id)
    if not state.film_output_uri:
        raise HTTPException(
            status_code=409, detail="這個專案還沒合成全片。先呼叫 assemble-film。"
        )

    return _serve_media(
        state.film_output_uri,
        filename=f"{project_id}-film.mp4",
        missing_detail="全片檔案不見了，請重新合成。",
    )


def _build_film_srt(state, lang: str) -> str:
    """組出全片的 SRT：各段字幕接起來，時間軸帶累計偏移。

    直接把各段的 .srt 檔頭尾相接是錯的：每一段的時間軸都從
    00:00:00 開始。第二段的第一句字幕會標在全片的第 0 秒，
    跟第一段的開頭重疊，之後每一句都對不上嘴。

    偏移量用前面所有段落的實測 output_seconds 累加，
    不是用規劃時請求的秒數，那個誤差會一段一段累積下去。

    只算已接片的段落（有 output_uri）。沒接片的段落不在全片裡，
    它的字幕當然也不能算進偏移，否則後面全部往後推。

    結果快取在 `state.film_subtitles[lang]`；重新合成全片時會清空
    （assemble_film 有做），所以不會拿到過期的時間軸。

    Args:
        state: 專案狀態。
        lang: 目標語言碼。

    Returns:
        全片 SRT 字串。
    """
    from ..services import assembler

    cached = state.film_subtitles.get(lang)
    if cached:
        return cached

    all_cues: list = []
    offset = 0.0
    index = 1

    for segment in state.segments:
        if not segment.output_uri:
            continue
        # 每段各自翻譯（走既有的快取與記帳），再把時間軸推後。
        srt = _build_srt(state, segment, lang)
        clips = [c for c in _ensure_clips(segment) if c.uri]
        cues = assembler.build_cues(clips, segment.shot_list.shots)

        # _build_srt 可能回的是翻譯過的文字，要把它套回 cues。
        # 直接重解析 SRT 比較穩，翻譯的快取就是以 SRT 字串存的。
        texts = _srt_texts(srt)
        for i, cue in enumerate(cues):
            cue.index = index
            cue.start += offset
            cue.end += offset
            if i < len(texts):
                cue.text = texts[i]
            all_cues.append(cue)
            index += 1

        offset += float(segment.output_seconds or 0)

    if not all_cues:
        raise HTTPException(status_code=409, detail="全片沒有任何台詞。")

    body = assembler.to_srt(all_cues)
    state.film_subtitles[lang] = body
    project_store.get_store().save(state)
    return body


def _srt_texts(srt: str) -> list[str]:
    """從 SRT 字串抽出每一句的文字（照順序）。

    字幕翻譯的快取是以整份 SRT 字串的形式存的
    （`segment.subtitles[lang]`）。組全片字幕時需要的是
    「每一句的文字」好套回帶偏移的 cue 上，這是唯一
    能同時吃到翻譯快取又重算時間軸的路。

    依賴 SRT 的區塊格式（序號 / 時間 / 一到多行文字 / 空行）。
    `to_srt()` 產生的一定符合；外部匯入的 SRT 沒有保證。
    """
    texts: list[str] = []
    for block in srt.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) >= 3:
            texts.append("\n".join(lines[2:]))
    return texts


@router.get("/{project_id}/film/subtitles")
def get_film_subtitles(project_id: str, lang: str = "zh") -> Response:
    """整部片的字幕檔（SRT）。時間軸已帶各段偏移。"""
    state = _load(project_id)
    body = _build_film_srt(state, lang)
    return Response(
        content=body,
        media_type="application/x-subrip; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}-film.{lang}.srt"'
        },
    )


@router.post("/{project_id}/film/burn")
def burn_film_subtitles(project_id: str, lang: str = "zh") -> dict:
    """把字幕燒進整部片。$0（本機 ffmpeg，會重新編碼）。

    整片重新編碼比單段慢很多。段落級實測 35MB / 90 秒影片
    94.7 秒（`preset veryfast`，Cloud Run 1 CPU）。
    整片按長度等比例放大，三倍長約 280 秒。
    Cloud Run 請求逾時已調到 900 秒，還有餘裕。
    """
    from ..services import assembler

    state = _load(project_id)
    if not state.film_output_uri:
        raise HTTPException(
            status_code=409, detail="這個專案還沒合成全片。先按「合成全片」。"
        )

    loaded = media_store.load(state.film_output_uri)
    if not loaded:
        raise HTTPException(status_code=404, detail="全片檔案不見了，請重新合成。")

    srt = _build_film_srt(state, lang)
    video_bytes, _mime = loaded

    try:
        burned, font_broken = assembler.burn_subtitles(video_bytes, srt)
    except assembler.AssembleError as exc:
        # 也記 log：HTTPException 的 detail 不會進 Cloud Logging，
        # 只回給那一個客戶端。少了這行，事後查是一片空白的 500。
        logger.error("燒字幕失敗：%s", exc)
        raise HTTPException(status_code=500, detail=f"燒字幕失敗：{exc}") from exc

    name = f"film_{lang}_burned"
    uri = media_store.save(project_id, name, burned, "video/mp4")
    exports.publish(project_id, name, burned)      # Greenlight Local：另存一份到使用者選的成品資料夾
    state.film_burned[lang] = f"{name}.mp4"
    state.record_decision(
        choice=f"全片燒進 {lang} 字幕",
        rationale=f"另存 {name}.mp4，原始全片保留",
    )
    project_store.get_store().save(state)

    return {
        "download_url": f"/api/projects/{project_id}/media/{name}.mp4/raw",
        "filename": f"{name}.mp4",
        "lang": lang,
        "size_bytes": len(burned),
        "uri": uri,
        # 字型缺失時影片照給，但一定要說，不然使用者拿到的是一支
        # 「看起來成功」卻一個字都沒有的成品。
        "subtitles_rendered": not font_broken,
        "warning": (
            "⚠️ 伺服器缺少字型，字幕沒有畫進畫面。影片本身是好的，"
            "外掛字幕檔仍然可以下載使用。"
            if font_broken else ""
        ),
    }


@router.get("/{project_id}/segments/{order}/subtitles")
def get_subtitles(project_id: str, order: int, lang: str = "zh") -> Response:
    """取得某個語言的字幕檔（SRT）。

    原文語言 $0；其他語言第一次會翻譯（約 $0.002），之後從快取取。
    """
    state = _load(project_id)
    segment = _segment(state, order)
    body = _build_srt(state, segment, lang)

    filename = f"{project_id}-seg{order}.{lang}.srt"
    return Response(
        content=body,
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{project_id}/segments/{order}/assemble/burn")
def burn_segment_subtitles(project_id: str, order: int, lang: str = "zh") -> dict:
    """把字幕燒進成片，產出一支自帶字幕的 mp4。$0（純本機 ffmpeg）。

    只提供 .srt 下載的話，使用者要自己拿去別的軟體跟影片合起來；
    這裡直接產出中／英／日任一語言的自帶字幕版本。

    燒進去是不可逆的（畫素層面），所以另存一支
    `segment{order}_{lang}_burned.mp4`，不覆蓋原始成片。
    使用者想換語言、或想要無字幕版，原檔都還在。

    每種語言各存一支。三語就是三個檔案，都會出現在媒體庫裡
    讓使用者自己挑要留哪個（配合檔案不自動刪除的策略）。

    Args:
        project_id: 專案 ID。
        order: 段落序號。
        lang: 字幕語言（zh / en / ja）。非原文語言第一次會翻譯（約 $0.002）。

    Returns:
        {download_url, filename, lang, size_bytes}。
    """
    from ..services import assembler

    state = _load(project_id)
    segment = _segment(state, order)

    if not segment.output_uri:
        raise HTTPException(
            status_code=409, detail="這一段還沒接片。先按「接片」再燒字幕。"
        )

    loaded = media_store.load(segment.output_uri)
    if not loaded:
        raise HTTPException(status_code=404, detail="成片檔案不見了，請重新接片。")

    srt = _build_srt(state, segment, lang)
    video_bytes, _mime = loaded

    try:
        burned, font_broken = assembler.burn_subtitles(video_bytes, srt)
    except assembler.AssembleError as exc:
        # 也記 log：HTTPException 的 detail 不會進 Cloud Logging，
        # 只回給那一個客戶端。少了這行，事後查是一片空白的 500。
        logger.error("燒字幕失敗：%s", exc)
        raise HTTPException(status_code=500, detail=f"燒字幕失敗：{exc}") from exc

    name = f"segment{order}_{lang}_burned"
    uri = media_store.save(project_id, name, burned, "video/mp4")
    exports.publish(project_id, name, burned)      # Greenlight Local：另存一份到使用者選的成品資料夾

    # 記進 state：燒錄狀態只放前端的話，重新整理之後「燒好了」就消失，
    # 使用者會對著媒體庫裡明明存在的檔案再燒一次。
    segment.burned[lang] = f"{name}.mp4"

    state.record_decision(
        choice=f"第 {order} 段燒進 {lang} 字幕",
        rationale=f"另存 {name}.mp4，原始成片保留",
    )
    project_store.get_store().save(state)

    return {
        "download_url": f"/api/projects/{project_id}/media/{name}.mp4/raw",
        "filename": f"{name}.mp4",
        "lang": lang,
        "size_bytes": len(burned),
        "uri": uri,
        # 字型缺失時影片照給，但一定要說，不然使用者拿到的是一支
        # 「看起來成功」卻一個字都沒有的成品。
        "subtitles_rendered": not font_broken,
        "warning": (
            "⚠️ 伺服器缺少字型，字幕沒有畫進畫面。影片本身是好的，"
            "外掛字幕檔仍然可以下載使用。"
            if font_broken else ""
        ),
    }


@router.get("/{project_id}/segments/{order}/download")
def download_segment(project_id: str, order: int) -> Response:
    """下載接好的完整影片。"""
    state = _load(project_id)
    segment = _segment(state, order)

    if not segment.output_uri:
        raise HTTPException(
            status_code=409, detail="這一段還沒接片。先呼叫 assemble。"
        )

    return _serve_media(
        segment.output_uri,
        filename=f"{project_id}-seg{order}.mp4",
        missing_detail="成片檔案不見了，請重新接片。",
    )


@router.get("/{project_id}/clips/{clip_id}")
def get_clip(project_id: str, clip_id: str) -> Response:
    """播放一段生成好的影片。

    不接受任意路徑：clip_id 來自 URL，直接拿去組路徑會有路徑穿越風險，
    改由 media_store 在該專案範圍內比對檔名。

    雲端的片段不在本機磁碟上，只 glob 本機目錄在 Cloud Run 上永遠找不到，
    生成的片段都在 GCS。雲端改走簽名網址直供。
    """
    # 走 media_store.find()，本機與 GCS 的統一入口。
    # 不要自己 glob 本機目錄：那在 Cloud Run 上永遠找不到東西，
    # 而且本機測不出來（見 media_store.find 的說明）。
    uri = media_store.find(clip_id, project_id)
    if uri is None:
        raise HTTPException(status_code=404, detail="找不到這段影片")

    return _serve_media(
        uri,
        filename=uri.rsplit("/", 1)[-1],
        missing_detail="找不到這段影片",
    )
