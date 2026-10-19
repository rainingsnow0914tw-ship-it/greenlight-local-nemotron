"""選角關卡的 endpoint。

第 5 關，硬性閘門：
    候選人物圖相對便宜，可以反覆重抽；使用者選定滿意的候選之後，
    才允許進入影片生成 —— 影片貴得多，不能在角色沒定案時就燒。

這一層要守住的三件事：
    1. 花錢之前先報價。`/estimate` 是 $0 的，UI 用它把價格印在按鈕上。
    2. 閘門在資料層。鎖定後不能重抽是 casting.add_round 擋的，
       不是靠前端記得把按鈕禁用。
    3. 零輸入。所有 endpoint 都不接受自由文字 —— 重抽只能挑
       config 裡定義好的方向 id。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import RedirectResponse

from ..schemas.casting import CastingGate, CastingSession, CharacterKind
from ..schemas.project import Stage
from ..services import demo_mode, casting, config_loader, media_store, project_store

router = APIRouter()


def _load(project_id: str):
    state = project_store.get_store().load(project_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"找不到專案「{project_id}」")
    return state


def _ensure_gate(state) -> CastingGate:
    """拿到（必要時建立）這個專案的選角關卡。

    第一次進選角畫面時，依劇本裡的角色清單一次把所有 session 建好 ——
    這樣 UI 一進來就看得到「總共要選幾個角色、還差幾個」，
    不需要等使用者一個一個點開才知道全貌。
    """
    if state.casting is None:
        state.casting = CastingGate(plan_id=state.project_id)

    gate = state.casting
    screenplay = state.screenplay
    if not screenplay:
        return gate

    non_human = set(state.non_human_characters or [])
    existing = {s.character_name for s in gate.sessions}

    for character in screenplay.characters:
        if character.name in existing:
            continue
        gate.sessions.append(
            CastingSession(
                character_name=character.name,
                kind=(
                    CharacterKind.NON_HUMAN
                    if character.name in non_human
                    else CharacterKind.HUMAN
                ),
                is_principal=character.is_principal,
                source_description=character.visual_description,
            )
        )

    return gate


def _session_or_404(gate: CastingGate, character: str) -> CastingSession:
    session = gate.session_for(character)
    if session is None:
        raise HTTPException(status_code=404, detail=f"這個專案沒有角色「{character}」")
    return session


def _candidate_payload(cand, project_id: str, segment_order: int | None = None) -> dict:
    """一張候選圖給前端的樣子。

    image_url 是 API 路徑不是檔案路徑 —— 前端不需要知道圖存在本機還是 GCS。
    生成失敗的候選 image_url 為 None，UI 該顯示為「這張沒生出來」
    而不是破圖。
    """
    return {
        "candidate_id": cand.candidate_id,
        "round_number": cand.round_number,
        "interpretation": cand.interpretation,
        "appearance_tags": cand.appearance_tags,
        # 音色。空字串代表這個角色還沒有鏡頭生成過 ——
        # 它是從第一支有台詞的成品裡聽出來的，不是事先猜的。
        "voice_tags": cand.voice_tags,
        "inference_source": cand.inference_source.value,
        "inference_note": cand.inference_note,
        "cost_usd": cand.cost_usd,
        # 這張候選屬於哪一段的造型輪（None = 一般選角輪）
        "segment_order": segment_order,
        "image_url": (
            f"/api/projects/{project_id}/casting-image/{cand.candidate_id}"
            if cand.image_uri
            else None
        ),
        "has_image": bool(cand.image_uri),
        # 文字版候選（沒有生圖模型）：沒有圖但可以選定
        "image_skipped": cand.image_skipped,
    }


def _session_payload(session: CastingSession, project_id: str) -> dict:
    notice = casting.budget_notice(session)
    return {
        "character_name": session.character_name,
        "kind": session.kind.value,
        "is_principal": session.is_principal,
        "source_description": session.source_description,
        # 誠實原則：劇本沒寫外貌的角色，UI 要標明「以下是系統推測」。
        "has_explicit_description": bool(session.source_description),
        "is_locked": session.is_locked,
        "selected_candidate_id": session.selected_candidate_id,
        # 音色鎖定在哪一鏡。有值 = 這個角色的聲音已經定了，
        # 之後每一鏡都用同一串描述 —— 音色是固定的，不能每鏡隨機生成。
        "voice_locked_from": session.voice_locked_from,
        "rounds_used": session.rounds_used,
        "total_spent_usd": session.total_spent_usd,
        "budget_notice": notice.model_dump(),
        "directions": [
            d.model_dump() for d in casting.available_directions(session.kind)
        ],
        # 所有輪次的候選都攤平回去 —— 使用者常常抽了五輪之後
        # 覺得「還是第二輪那張好」，不保留歷史等於逼他重抽。
        "candidates": [
            _candidate_payload(c, project_id, rnd.segment_order)
            for rnd in session.rounds
            for c in rnd.candidates
        ],
        # ── 身份參考（臉）與段落造型 ──
        "identity_reference_url": (
            f"/api/projects/{project_id}/casting-image/{identity_name(session.character_name)}"
            if session.identity_source == "uploaded" and session.identity_reference_uri
            else (
                # 自動取用、或身份參考功能之前鎖定的舊角色（退回選定那張）—— 見 casting.identity_uri
                f"/api/projects/{project_id}/casting-image/{session.selected_candidate_id}"
                if casting.identity_uri(session) and session.selected_candidate_id
                else None
            )
        ),
        "identity_source": (
            session.identity_source
            or ("generated" if casting.identity_uri(session) else None)
        ),
        "identity_consented_at": (session.identity_consent or {}).get("accepted_at"),
        # {segment_order(str): candidate_id}
        "segment_looks": dict(session.segment_looks),
    }


def identity_name(character_name: str) -> str:
    """身份參考圖在 media_store 裡的名字。走 casting-image route 用同一個名字取回。"""
    from ..agents.character_designer import _slug
    return f"identity-{_slug(character_name)}"


@router.get("/{project_id}/casting")
def get_casting(project_id: str) -> dict:
    """選角關卡的完整狀態。$0。"""
    state = _load(project_id)
    if not state.screenplay:
        raise HTTPException(status_code=409, detail="這個專案還沒解析劇本")

    gate = _ensure_gate(state)
    project_store.get_store().save(state)

    return {
        "project_id": project_id,
        "gate": casting.gate_status(gate),
        # 定調（第 4 關）是選角的前置。前端要能在按下去之前
        #    就說明「先去選風格」並給入口 —— 見 generate_round 的說明。
        "look_ready": state.stage.order >= Stage.LOOK_CHOSEN.order,
        "style_id": state.style_id,
        "aspect_ratio": state.aspect_ratio,
        "round_cost_usd": casting.round_cost_usd(),
        "candidates_per_round": int(
            config_loader.casting().get("candidates_per_round", 3)
        ),
        "sessions": [_session_payload(s, project_id) for s in gate.sessions],
        # 段落清單給「造型用於：第 N 段」下拉用。沒分段就是空的、UI 不顯示。
        "segments": [
            {"order": seg.order, "title": seg.title} for seg in (state.segments or [])
        ],
    }



# 角色名走 query parameter，不放 URL path：
#
#     角色名若含斜線（例如「Luna (月音/翎)」），前端 encodeURIComponent 成 `%2F`
#     後，雲端負載平衡層會把 `%2F` 還原成真正的斜線，路徑多出一段，
#     route pattern 對不上，回 405 Method Not Allowed。
#
#     根治不是去 escape 那個斜線 —— 角色名是劇本來的使用者資料，
#     可能含 `/`、`?`、`#`、空格任何字元，本來就不該當路徑段落。
#     query parameter 沒有這個問題。
@router.post("/{project_id}/casting/rounds")
def generate_round(
    project_id: str,
    character: str,
    direction: str = "initial",
    segment_order: int | None = None,
) -> dict:
    """生一輪候選圖。這一步會花錢（約 $0.065）。
    同步執行，三張圖之間有節流間隔，整輪約 20–30 秒。前端要顯示等待狀態。

    segment_order 有值 = 段落造型變體：角色必須已鎖定，
    拿身份參考（臉）當圖片輸入、只換造型（預設方向 change_wardrobe），
    出來的候選用 select?segment_order=N 設成該段造型，不動角色本體。
    """
    from ..agents import character_designer

    state = _load(project_id)
    if not state.screenplay:
        raise HTTPException(status_code=409, detail="這個專案還沒解析劇本")

    # 定調（第 4 關）必須在選角（第 5 關）之前：
    #
    #     角色候選圖是照風格生的：真人版與 2D 動畫是兩套完全不同的圖。
    #     沒定調就抽，等於用預設的寫實風生一輪，使用者選完之後改風格，
    #     那些圖全部作廢 —— 每輪 $0.06 就這樣丟掉。
    #
    #     Stage 的順序本來就是 LOOK_CHOSEN → CAST_LOCKED，
    #     但 `style_id` 有預設值 "photoreal"，
    #     所以「沒選過」跟「選了寫實風」在資料上長得一模一樣。
    #     用 stage 判斷才分得出來。
    if state.stage.order < Stage.LOOK_CHOSEN.order:
        raise HTTPException(
            status_code=409,
            detail=(
                "還沒定調。先選視覺風格與畫面比例，再開始選角 —— "
                "角色的長相是照風格生的，真人版與動畫版是兩套完全不同的圖，"
                "先定調才不會白花錢。"
            ),
        )

    gate = _ensure_gate(state)
    session = _session_or_404(gate, character)
    if segment_order is None and session.is_locked:
        raise HTTPException(
            status_code=409,
            detail=(
                f"角色「{character}」已經選定並鎖定。"
                f"要重抽必須先解除選定。"
            ),
        )
    if segment_order is not None:
        if not session.is_locked:
            raise HTTPException(
                status_code=409,
                detail=f"角色「{character}」還沒選定。段落造型是同一張臉換衣服，先鎖定角色本體。",
            )
        if not any(seg.order == segment_order for seg in state.segments):
            raise HTTPException(status_code=404, detail=f"沒有第 {segment_order} 段")

    # 身份參考（臉）：有就當圖片輸入。上傳的、或第一次鎖定時自動取的，都走這裡。
    identity_image = None
    identity_src = casting.identity_uri(session)
    if identity_src:
        identity_image = media_store.load(identity_src)

    try:
        candidates, usage = character_designer.design_round(
            project_id,
            session,
            style_id=state.style_id,
            direction_id=direction,
            lang=state.prompt_language or "zh",
            context_excerpt=_context_excerpt(state, character),
            # 參考圖比例跟著專案影片比例：影片模型會沿用參考圖的方向，比例不一致會污染輸出
            project_aspect=state.aspect_ratio or "16:9",
            identity_image=identity_image,
            look_segment=segment_order,
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層的 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"角色生成失敗：{exc}") from exc

    # 提交走交易式寫入（多個角色同時生成會撞出 lost update）：
    #    生成花了 20–30 秒，這段期間別的角色的輪次已經寫進狀態了。
    #    不能拿「生成前那份舊狀態」整包存回 —— 那會把別人的成果蓋掉。
    #    mutate 會在最新狀態上重找 session、掛上這一輪，
    #    Firestore transaction 保證併發提交誰都不蓋誰。
    def _commit(fresh):
        fresh_gate = _ensure_gate(fresh)
        fresh_session = _session_or_404(fresh_gate, character)
        if segment_order is None and fresh_session.is_locked:
            # 生成期間被鎖定了（例如另一個分頁選定了）——輪次不掛，
            # 圖已經生了留在媒體庫，錢記進帳本誠實呈現。
            raise HTTPException(
                status_code=409,
                detail=f"角色「{character}」在生成期間被選定鎖住，這一輪未加入。",
            )
        try:
            casting.add_round(
                fresh_session, candidates,
                direction_id=direction, segment_order=segment_order,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        fresh.casting = fresh_gate
        # 花費一定要記進帳本：硬預算下，「還剩多少可以用」
        #    不能靠事後翻 Google Cloud 帳單。
        fresh.spend.llm_usd += usage.get("cost_usd", 0.0)
        fresh.spend.images_usd += usage.get("image_cost_usd", 0.0)
        fresh.record_decision(
            choice=f"{character} 第 {fresh_session.rounds_used} 輪選角（{direction}）",
            rationale=f"{usage.get('images_generated', 0)} 張，${usage.get('total_cost_usd', 0)}",
        )
        return fresh

    new_state = project_store.get_store().mutate(project_id, _commit)
    new_gate = new_state.casting
    new_session = new_gate.session_for(character) if new_gate else None

    return {
        "session": _session_payload(new_session, project_id),
        "gate": casting.gate_status(new_gate),
        "usage": usage,
    }


@router.post("/{project_id}/casting/select")
def select_candidate(
    project_id: str, character: str, candidate_id: str, segment_order: int | None = None
) -> dict:
    """選定一張候選圖，鎖住這個角色。$0。

    可以選任何一輪的圖，不限最後一輪。

    segment_order 有值 = 只把這張設成第 N 段的造型，角色本體與鎖定狀態不動。
    """
    if segment_order is not None:
        def _commit_look(state):
            gate = _ensure_gate(state)
            session = _session_or_404(gate, character)
            try:
                casting.select_look(session, segment_order, candidate_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            state.casting = gate
            state.record_decision(
                choice=f"{character} 第 {segment_order} 段改用造型 {candidate_id}",
                rationale="段落造型覆寫，$0",
            )
            return state
        new_state = project_store.get_store().mutate(project_id, _commit_look)
        gate = new_state.casting
        return {
            "session": _session_payload(gate.session_for(character), project_id),
            "gate": casting.gate_status(gate),
            "stage": new_state.stage.value,
        }

    # 整段走交易式寫入（理由見 generate_round 的 _commit）：
    #    選定常跟別的角色的生成/選定並發，整包存回會互相蓋掉。
    def _commit(state):
        gate = _ensure_gate(state)
        session = _session_or_404(gate, character)
        try:
            chosen = casting.select(session, candidate_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        state.casting = gate
        # 換了角色就必須讓改寫過的英文描述失效：
        #    visual_prompt 是在當時選定的候選下改寫的，
        #    不清掉會留下兩套外貌（標籤說沒眼鏡、描述說有眼鏡）。
        invalidated = 0
        for segment in state.segments:
            if not segment.shot_list:
                continue
            for shot in segment.shot_list.shots:
                if character in (shot.characters_in_frame or []) and shot.visual_prompt:
                    shot.visual_prompt = None
                    invalidated += 1

        state.record_decision(
            choice=f"{character} 選定 {candidate_id}",
            rationale=chosen.interpretation
            + (
                f"（{invalidated} 個鏡頭的英文描述已失效，需重新改寫）"
                if invalidated
                else ""
            ),
        )

        # 所有角色都選定了才推進階段 —— 這是硬性閘門。
        if gate.can_proceed and state.stage.order < Stage.CAST_LOCKED.order:
            state.stage = Stage.CAST_LOCKED
        return state

    new_state = project_store.get_store().mutate(project_id, _commit)
    gate = new_state.casting
    session = gate.session_for(character) if gate else None

    return {
        "session": _session_payload(session, project_id),
        "gate": casting.gate_status(gate),
        "stage": new_state.stage.value,
    }


@router.delete("/{project_id}/casting/select")
def unlock_character(
    project_id: str, character: str, segment_order: int | None = None
) -> dict:
    """解除選定，讓這個角色可以重抽。$0。

    segment_order 有值 = 只取消第 N 段的造型覆寫（回到全片預設），不解鎖角色。

    不自動刪掉任何已生成的東西 —— 只解鎖。
       後續鏡頭是否失效由使用者自己判斷（跟 rewind_to 同一個原則：
       只回報後果，不替使用者清資料）。
    """
    # 交易式寫入，理由同 select（避免併發 lost update）
    def _commit(state):
        gate = _ensure_gate(state)
        session = _session_or_404(gate, character)
        if segment_order is not None:
            casting.clear_look(session, segment_order)
            state.casting = gate
            return state
        casting.unlock(session)
        state.casting = gate
        if state.stage is Stage.CAST_LOCKED:
            state.stage = Stage.LOOK_CHOSEN
        return state

    new_state = project_store.get_store().mutate(project_id, _commit)
    gate = new_state.casting
    session = gate.session_for(character) if gate else None
    return {
        "session": _session_payload(session, project_id),
        "gate": casting.gate_status(gate),
        "stage": new_state.stage.value,
    }


@router.post("/{project_id}/casting/identity")
async def upload_identity(
    project_id: str,
    character: str,
    consent: bool = False,
    file: UploadFile = File(...),
) -> dict:
    """上傳一張身份參考圖（臉）給某個角色。$0。

    這是零自由文字輸入原則下唯一允許的輸入形式（檔案上傳），也是「自己當主角」／
    「動畫作者自帶角色設定」的入口。上傳的圖不是角色卡：
    之後按「生成候選」時，設計師拿這張臉照本片風格生三個造型候選，選定的才是角色卡。

    免責：使用者拿別人的照片來生成，肖像責任歸使用者。
       `consent=true` 才收檔；勾了就把時間與聲明版本記進專案狀態。
       前端要顯示聲明原文（見 frontend copy `identityConsent`），這裡不信任 UI 記得。

    角色已鎖定也可以上傳（換掉身份參考不影響鎖定），但已生成的鏡頭不會自動失效 ——
    跟 unlock 同一個原則：只回報後果，不替使用者清資料。
    """
    if not consent:
        raise HTTPException(
            status_code=400,
            detail="請先確認：你有權使用這張圖片，肖像相關的責任由你承擔。",
        )
    mime = (file.content_type or "").lower()
    if not mime.startswith("image/"):
        raise HTTPException(status_code=415, detail="請上傳圖片檔（jpg / png / webp）。")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="檔案是空的。")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="圖片超過 12MB，請縮小後再上傳。")

    state = _load(project_id)
    gate = _ensure_gate(state)
    _session_or_404(gate, character)  # 只驗存在，真正寫入走 mutate

    # 存檔在交易外做（跟 generate_round 同理：I/O 不放進 transaction）。
    # demo 模式在 media_store.save 的守衛擋下（DemoBlocked → 402）。
    uri = media_store.save(project_id, identity_name(character), data, mime)

    from datetime import datetime, timezone
    consent_record = {
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "statement_version": "2026-08-18",
        "filename": file.filename or "",
        "bytes": len(data),
    }

    def _commit(fresh):
        fresh_gate = _ensure_gate(fresh)
        session = _session_or_404(fresh_gate, character)
        casting.set_identity(session, uri, source="uploaded", consent=consent_record)
        fresh.casting = fresh_gate
        fresh.record_decision(
            choice=f"{character} 上傳身份參考圖",
            rationale=f"{len(data)} bytes，免責已確認 {consent_record['accepted_at']}",
        )
        return fresh

    new_state = project_store.get_store().mutate(project_id, _commit)
    gate = new_state.casting
    return {
        "session": _session_payload(gate.session_for(character), project_id),
        "gate": casting.gate_status(gate),
    }


@router.post("/{project_id}/casting/identity/use")
def use_identity_as_is(project_id: str, character: str) -> dict:
    """把上傳的那張圖原樣當成這個角色（不經模型重畫）。$0。

    為什麼要有：上傳的圖原本只是「設計師的輸入」—— 照這張臉、依本片風格**重新設計**三個造型。
    自己已經做好人物卡的人（另一套工具生的、自己畫的）要的是原圖照用（參考圖不能被我們的模型重畫，
    要照使用者自己的）。這張變成一個候選、直接選定；之後的全身卡、臉卡也都用原圖（services/ref_jobs.py）。
    """
    from ..agents.character_designer import _slug
    from ..schemas.casting import CastingRound, CharacterCandidate

    def _commit(fresh):
        gate = _ensure_gate(fresh)
        session = _session_or_404(gate, character)
        if session.identity_source != "uploaded" or not session.identity_reference_uri:
            raise HTTPException(status_code=409, detail="先上傳這個角色的圖。")
        n = len(session.rounds) + 1
        cand = CharacterCandidate(candidate_id=f"{_slug(character)}-own{n}", character_name=character, round_number=n,
                                  image_uri=session.identity_reference_uri, interpretation="你上傳的圖，原樣使用",
                                  appearance_tags=session.source_description or "")
        session.rounds.append(CastingRound(round_number=n, direction_id="own_image", direction_label="自己的圖",
                                           candidates=[cand]))
        casting.select(session, cand.candidate_id)
        fresh.casting = gate
        if gate.can_proceed and fresh.stage.order < Stage.CAST_LOCKED.order:   # 跟一般選定同一道閘門
            fresh.stage = Stage.CAST_LOCKED
        fresh.record_decision(choice=f"{character} 用自己上傳的圖當角色（原圖、不重畫）",
                              rationale="使用者自備人物卡")
        return fresh

    new_state = project_store.get_store().mutate(project_id, _commit)
    gate = new_state.casting
    return {"session": _session_payload(gate.session_for(character), project_id), "gate": casting.gate_status(gate)}


@router.delete("/{project_id}/casting/identity")
def clear_identity(project_id: str, character: str) -> dict:
    """移除身份參考。$0。圖檔留在媒體庫（跟 unlock 同原則），只清掉指向。"""
    def _commit(state):
        gate = _ensure_gate(state)
        session = _session_or_404(gate, character)
        casting.clear_identity(session)
        state.casting = gate
        return state

    new_state = project_store.get_store().mutate(project_id, _commit)
    gate = new_state.casting
    return {
        "session": _session_payload(gate.session_for(character), project_id),
        "gate": casting.gate_status(gate),
    }


@router.get("/{project_id}/casting-image/{candidate_id}")
def get_candidate_image(project_id: str, candidate_id: str) -> Response:
    """回一張候選圖。雲端轉去 Cloud Storage 直供，本機自己傳。

    必須走 `media_store.find()`（本機與雲端統一的查找入口），
    不能只掃 `media_store.root()`：Cloud Run 上本機目錄永遠是空的，
    所有媒體都在 GCS，只掃本機會對存在的圖回 404，而本機開發測不到這個差異。
    """
    # 一定要帶 project_id：candidate_id 是從角色名算出來的 hash，
    #    兩個專案有同名角色就會撞號。跨專案搜尋會拿到另一部片的角色圖，
    #    而且看起來完全正常。
    uri = media_store.find(candidate_id, project_id)
    if uri is None:
        raise HTTPException(status_code=404, detail="找不到這張圖")

    # 雲端：讓瀏覽器直接跟 GCS 拿，不佔 Cloud Run 的記憶體與頻寬。
    url = media_store.signed_url(uri)
    if url:
        return RedirectResponse(url, status_code=302)

    loaded = media_store.load(uri)
    if loaded is None:
        raise HTTPException(status_code=404, detail="找不到這張圖")
    data, mime = loaded
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _context_excerpt(state, character_name: str, max_scenes: int = 4) -> str:
    """挑幾段這個角色出現的場次摘要當語境。

    不整份劇本丟給模型 —— 一份 40K token 的劇本每抽一輪就重讀一次，
    抽十輪就是 400K token 的浪費。選角需要的資訊只有幾百字。
    """
    screenplay = state.screenplay
    if not screenplay:
        return ""

    picked = [
        s for s in screenplay.scenes if character_name in (s.characters_present or [])
    ][:max_scenes]
    summary = "\n".join(f"・{s.action}" for s in picked if s.action)
    # 劇本原文裡提到這個角色的句子（最多 8 句、約 600 字）：場次摘要看不出「ぷく」是貓，
    #    原文寫著「ぷくが『にゃあ』と鳴いた」「璃安はそれを抱き上げた」（第三世試跑：只給摘要，畫出三輪獸人）
    text = getattr(state, "script_text", "") or ""
    said = [x.strip() for x in re.split(r"(?<=[。！？!?\n])", text) if character_name in x and x.strip()][:8]
    raw = "\n".join(f"・{x[:120]}" for x in said)
    return "\n".join(p for p in (summary, f"（原文）\n{raw}" if raw else "") if p)
