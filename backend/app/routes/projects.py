"""專案相關的 endpoint。

專案生命週期：上傳 → 解析 → 分段 → 定調 → 試拍 → 生成 → 接片。
每一步花錢的動作都先報價、再由使用者按下去；唯讀 endpoint 全部 $0。
"""

from __future__ import annotations

import os
import re

import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from ..agents import segmenter
from ..schemas.plan import QualityTier
from ..schemas.project import STAGE_LABELS, ProjectState, Stage
from ..schemas.segment import SegmentStatus
from ..services import rates
from ..services import (
    config_loader,
    constraint_engine,
    cost_engine,
    demo_mode,
    ledger,
    project_store,
    risk_text,
    script_ingest,
)

router = APIRouter()

# 上傳大小上限。一份 300 頁的劇本純文字約 500KB，
# Word 檔含格式資訊會膨脹幾倍，PDF 更多。10MB 綽綽有餘，
# 而且擋掉「不小心拖了一支影片進來」這種情況。
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/upload")
async def upload_script(file: UploadFile = File(...)) -> dict:
    """接收劇本檔案，建立草稿專案。這一步不花錢。

    刻意不在上傳時就自動解析：
       解析是這條鏈路第一個花錢的動作（約 $0.15）。
       上傳完就自動扣款，違反「花錢前先講清楚」。

       所以這裡只做三件事：讀檔、轉成純文字、估算解析成本。
       使用者看到數字之後，再自己決定要不要按下一步。

    支援 .txt / .md / .fountain / .docx / .pdf。
    """
    data = await file.read()

    if not data:
        raise HTTPException(status_code=400, detail="檔案是空的")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"檔案太大（{len(data) / 1024 / 1024:.1f} MB），上限 10 MB",
        )

    try:
        result = script_ingest.ingest(data, file.filename or "untitled.txt")
    except script_ingest.UnsupportedFormat as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層的 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"讀取失敗：{exc}") from exc

    if result.char_count < 200:
        raise HTTPException(
            status_code=400,
            detail=f"內容太短（{result.char_count} 字元），這看起來不像一份劇本",
        )

    project_id = f"p-{uuid.uuid4().hex[:10]}"
    title = (file.filename or "未命名").rsplit(".", 1)[0]

    state = ProjectState(project_id=project_id, title=title)
    state.script_text = result.text
    state.stage = Stage.SCRIPT_UPLOADED
    project_store.get_store().save(state)

    estimate = script_ingest.estimate_parse_cost(result.text)
    marks = segmenter.detect_chapters(result.text)

    return {
        "project_id": project_id,
        "title": title,
        "file": {
            "filename": result.filename,
            "format": result.detected_format,
            "encoding": result.encoding,
            "page_count": result.page_count,
            "char_count": result.char_count,
            "line_count": result.line_count,
        },
        # 上傳階段就能免費看出章節結構 —— 純字串比對，不花錢。
        # 這讓使用者在付費解析之前，先確認系統看懂了他的檔案結構。
        "chapters_detected": len(marks),
        "chapter_titles": [m.title[:60] for m in marks[:12]],
        "continuity_problems": segmenter.check_continuity(marks),
        "parse_estimate": estimate,
        "warnings": result.warnings,
    }


@router.post("/{project_id}/parse")
def parse_script(project_id: str) -> dict:
    """解析劇本。這一步會花錢（約 $0.15，依長度）。

    同步執行，會跑 30–90 秒。前端要顯示等待狀態。
    """
    from ..agents import script_parser

    state = _load(project_id)
    if not state.script_text:
        raise HTTPException(status_code=409, detail="這個專案沒有劇本內容")
    if state.screenplay:
        raise HTTPException(
            status_code=409,
            detail="這個專案已經解析過了。重新解析會再花一次錢，請先確認。",
        )

    try:
        screenplay, usage = script_parser.parse(
            state.script_text, title_hint=state.title
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層的 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"解析失敗：{exc}") from exc

    state.screenplay = screenplay
    state.title = screenplay.title or state.title
    # 非人角色的判斷一定要存進狀態：
    #    只放在這支 API 的回應裡的話，重新載入專案就消失了，
    #    選角關卡因此拿不到，所有 AI 角色都會被當成人物、給錯方向按鈕。
    state.non_human_characters = usage.get("non_human_characters", [])
    state.stage = Stage.PARSED
    state.spend.llm_usd += usage["cost_usd"]
    state.record_decision(
        choice=f"解析完成：{len(screenplay.scenes)} 場、{len(screenplay.characters)} 個角色",
        rationale=f"{usage['model']}，${usage['cost_usd']}",
    )
    project_store.get_store().save(state)

    return {
        "project_id": project_id,
        "title": screenplay.title,
        "scene_count": len(screenplay.scenes),
        "character_count": len(screenplay.characters),
        "total_seconds": screenplay.total_estimated_seconds,
        "warnings": screenplay.parse_warnings,
        "usage": usage,
        "non_human_characters": usage.get("non_human_characters", []),
    }


@router.post("/{project_id}/duplicate")
def duplicate_project(project_id: str, payload: dict | None = None) -> dict:
    """複製一份新專案：同一個劇本、進度全部歸零。$0。

    為什麼是「複製」不是「重置」：
        真正的需求是同一份劇本，想試真人版也想試動畫版。
        「重置」會毀掉舊的那一版 —— 那是把兩個選擇變成一個。
        複製之後兩份並存，在專案列表裡各走各的。

        而且省錢：劇本解析要 ~$0.15，複製時直接沿用已解析的結果，
        新專案從「讀本完成」開始，不必重新付一次。

    帶什麼、不帶什麼：
        帶  劇本原文、解析結果（場景/角色）、非人角色判定
        不帶 分段進度、選角、試拍、生成的片段、成片、字幕、花費、決策紀錄

    媒體檔不複製 —— 新專案有自己的目錄。舊專案的圖與影片留在原處，
       兩邊的媒體庫互不干擾。

    Args:
        project_id: 要複製的來源專案。
        payload: 選填 `{"title": "新名字"}`。沒給就在原標題後面加註記。

    Returns:
        {project_id, title, stage} —— 新專案的識別資料。
    """
    # 展示版擋住：不花錢，但會讓專案列表長出訪客建的東西，
    #    展示的敘事就散掉了。demo 站要一直維持「一個真的跑完的專案」。
    demo_mode.guard("開新版本")

    source = _load(project_id)

    new_id = f"p-{uuid.uuid4().hex[:10]}"
    suffix = (payload or {}).get("title") if payload else None

    # 新版本再開新版本：不要疊成「（新版本）（新版本）」，改成編號（新版本 2、3…）
    base = re.sub(r"（新版本(?: \d+)?）$", "", source.title)
    store = project_store.get_store()
    taken = {s.title for s in (store.load(i) for i in store.list_ids()) if s}
    n = 1
    while (f"{base}（新版本）" if n == 1 else f"{base}（新版本 {n}）") in taken:
        n += 1
    fresh = ProjectState(project_id=new_id, title=suffix or (f"{base}（新版本）" if n == 1 else f"{base}（新版本 {n}）"))
    # 只帶「已經花錢買到、而且跟後續選擇無關」的東西。
    fresh.script_text = source.script_text
    fresh.screenplay = source.screenplay
    fresh.non_human_characters = list(source.non_human_characters)
    fresh.stage = Stage.PARSED if source.screenplay else Stage.SCRIPT_UPLOADED
    # 解析的花費不繼承 —— 那筆錢記在原專案身上，複製沒有再花一次。
    #    把它算進新專案會讓「這部片花了多少」變成假的。

    project_store.get_store().save(fresh)

    return {
        "project_id": new_id,
        "title": fresh.title,
        "stage": fresh.stage.value,
        "copied_from": project_id,
        "note": "劇本與解析結果已沿用，進度歸零。原專案不受影響。",
    }


@router.get("/{project_id}/delete/preview")
def delete_preview(project_id: str) -> dict:
    """刪除之前先看會清掉什麼。$0，不刪任何東西。

    刪除不可逆，跟 rewind 一樣走兩段式：先看代價，再決定。

    誠實原則：連「不會刪什麼」也要講。使用者以為全刪了、
       結果 ClickHouse 還留著紀錄，那是另一種形式的欺騙。
    """
    from ..services import media_store

    state = _load(project_id)
    file_count, total_bytes = media_store.project_usage(project_id)

    return {
        "project_id": project_id,
        "title": state.title,
        "will_delete": {
            "project_state": True,
            "media_files": file_count,
            "media_bytes": total_bytes,
            "spent_usd": state.spend.total_usd,
        },
        "will_keep": {
            # ledger 不刪，理由跟 rewind 一樣：學習迴路要跨得過重來。
            #    而且它根本不佔空間 —— 一筆幾百 bytes，佔空間的是影片。
            "learning_records": (
                "ClickHouse 的生成紀錄會保留 —— 那是系統學到的東西，"
                "跨專案累積，而且一筆只有幾百位元組。刪掉只會讓成功率預估"
                "退回猜測，省不到空間。"
            ),
        },
    }


@router.delete("/{project_id}")
def delete_project(project_id: str) -> dict:
    """刪掉專案與它的所有媒體檔。不可逆。

    狀態與媒體必須一起刪，並且回報刪了什麼、留了什麼：
        只刪專案狀態的話，GCS 上的圖與影片會變成沒有任何入口找得到、
        卻一直計費的孤兒。
    先刪媒體再刪狀態。順序反過來的話，狀態刪掉之後如果媒體刪失敗，
        就再也沒有入口能找到那些檔案了。

    刻意不刪 ClickHouse 的生成紀錄：那是跨專案累積的學習資料，
       而且一筆幾百位元組 —— 刪它省不到空間，只會讓系統變笨。
       這跟 rewind 不動 ledger 是同一條原則。
    """
    # 展示版擋住：這是不可逆的，而 demo 站只有一個專案 ——
    #    訪客按一下，整個展示就沒了，而且救不回來（媒體檔跟著刪）。
    #    前端也會隱藏這顆按鈕，但守衛放在後端才擋得住直接打 API 的人。
    demo_mode.guard("刪除專案")

    from ..services import media_store

    state = project_store.get_store().load(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"找不到專案「{project_id}」")

    title = state.title
    _count, total_bytes = media_store.project_usage(project_id)

    # 先媒體、後狀態。反過來的話狀態沒了、媒體刪失敗 = 永久孤兒。
    deleted_files = media_store.delete_project(project_id)

    ok = project_store.get_store().delete(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"找不到專案「{project_id}」")

    return {
        "deleted": project_id,
        "title": title,
        "deleted_files": deleted_files,
        "freed_bytes": total_bytes,
        "kept": "ClickHouse 的生成紀錄（系統學到的東西，跨專案累積）",
    }


def _load(project_id: str):
    state = project_store.get_store().load(project_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"找不到專案「{project_id}」")
    return state


@router.get("")
def list_projects() -> dict:
    """專案清單。使用者隔幾天回來，第一個看到的畫面。"""
    store = project_store.get_store()
    items = []
    for pid in store.list_ids():
        try:
            state = store.load(pid)
        except Exception:  # noqa: BLE001 - 壞掉的存檔不該讓整個清單掛掉
            items.append({"project_id": pid, "error": "存檔損壞"})
            continue
        if state:
            items.append(state.progress_summary())
    return {"projects": items}


@router.get("/{project_id}")
def get_project(project_id: str) -> dict:
    """專案總覽：進度、劇本摘要、角色、花費。"""
    state = _load(project_id)
    sp = state.screenplay

    return {
        "progress": state.progress_summary(),
        "screenplay": None
        if not sp
        else {
            "title": sp.title,
            "language": sp.language,
            "scene_count": len(sp.scenes),
            "total_seconds": sp.total_estimated_seconds,
            "warnings": sp.parse_warnings,
            "characters": [
                {
                    "name": c.name,
                    "visual_description": c.visual_description,
                    "is_principal": c.is_principal,
                    "line_count": c.line_count,
                    "first_appearance_scene": c.first_appearance_scene,
                }
                for c in sp.characters
            ],
        },
        "spend": {
            "llm_usd": state.spend.llm_usd,
            "images_usd": state.spend.images_usd,
            "video_usd": state.spend.video_usd,
            "total_usd": state.spend.total_usd,
            "ceiling_usd": config_loader.guardrails().get("total_budget_usd"),
        },
        "decisions": [
            {
                "at": d.at.isoformat(),
                "stage": d.stage.value,
                "choice": d.choice,
                "rationale": d.rationale,
                "followed_recommendation": d.followed_recommendation,
            }
            for d in state.decisions
        ],
    }


@router.get("/{project_id}/scenes")
def get_scenes(project_id: str) -> dict:
    """場次表。含原文行號，前端可以做「對照原文」。"""
    state = _load(project_id)
    if not state.screenplay:
        raise HTTPException(status_code=409, detail="這個專案還沒解析劇本")

    return {
        "scenes": [
            {
                "scene_number": s.scene_number,
                "setting": s.setting.value,
                "location": s.location,
                "time_of_day": s.time_of_day.value,
                "action": s.action,
                "characters_present": s.characters_present,
                "estimated_seconds": s.estimated_screen_seconds,
                "source_line_start": s.source_line_start,
                "source_line_end": s.source_line_end,
            }
            for s in state.screenplay.scenes
        ]
    }


@router.get("/{project_id}/scenes/{scene_number}/source")
def get_scene_source(project_id: str, scene_number: int) -> dict:
    """某一場的原文。

    這是誠實原則的實作：系統說「第 3 場是這樣」，
       使用者要能點開看原文核對。行號是模型給的，可能數錯 ——
       不給對照的機會等於要人盲信。
    """
    state = _load(project_id)
    if not state.screenplay:
        raise HTTPException(status_code=409, detail="這個專案還沒解析劇本")

    scene = next(
        (s for s in state.screenplay.scenes if s.scene_number == scene_number), None
    )
    if not scene:
        raise HTTPException(status_code=404, detail=f"沒有第 {scene_number} 場")

    return {
        "scene_number": scene.scene_number,
        "source_line_start": scene.source_line_start,
        "source_line_end": scene.source_line_end,
        "text": scene.raw_text,
    }


@router.get("/{project_id}/segments")
def get_segments(
    project_id: str,
    tier: str | None = Query(None, description="用哪個畫質檔位估價，不給就用 config 預設"),
) -> dict:
    """分段方案。

    只回章節方案（純字串比對，零成本）—— LLM 建議的分段要另外觸發，
    因為那會花錢，不該在使用者只是「看一下」的時候就扣款。
    """
    tier = tier or config_loader.default_veo_tier()
    state = _load(project_id)
    if not state.screenplay:
        raise HTTPException(status_code=409, detail="這個專案還沒解析劇本")
    if not state.script_text:
        raise HTTPException(status_code=409, detail="這個專案沒有存劇本原文")

    marks = segmenter.detect_chapters(state.script_text)
    plan = segmenter._plan_from_chapters(state.screenplay, marks)  # noqa: SLF001

    try:
        per_second = float(config_loader.veo_tier(tier)["per_second_usd"])
    except Exception:
        per_second = 0.05

    # 已經有進度就用 state.segments，不要用剛重算出來的 plan.segments：
    #    plan.segments 是每次請求都從劇本原文重新算一份的全新物件，
    #    身上沒有任何進度。回它的話專案頁上每一章永遠顯示「尚未開始」，
    #    而且不會報錯 —— 章節標題、場次數、估價全部對得上，只有進度是錯的。
    #
    #    重算的那份仍然有用：專案還沒初始化分段時（state.segments 是空的）
    #    要靠它把方案與估價顯示出來，讓使用者在花錢之前先看。
    display_segments = state.segments if state.segments else (plan.segments if plan else [])

    # 短劇本沒有章節標記：_ensure_segments 會自動整份當一段，但這裡以前只看章節方案 → 回 plan=None，
    #    專案頁一個段落都不顯示，導演、台詞導演、生成全部點不進去（發現：第 61 場測試專案就卡在這）。
    if not plan and (state.segments or 0 < len(state.screenplay.scenes) <= _SHORT_SCRIPT_SCENES):
        from types import SimpleNamespace

        display_segments = _ensure_segments(state)
        total = sum(s.estimated_seconds for s in display_segments)
        plan = SimpleNamespace(plan_id="single", label=state.screenplay.title[:80] or "Film",
                               source=SimpleNamespace(value="single"), rationale="short script — one part",
                               total_seconds=total, average_seconds=total / max(1, len(display_segments)))

    if not plan:
        return {
            "has_chapters": False,
            "chapter_marks": len(marks),
            "continuity_problems": segmenter.check_continuity(marks),
            "plan": None,
        }

    return {
        "has_chapters": True,
        "chapter_marks": len(marks),
        "continuity_problems": segmenter.check_continuity(marks),
        "plan": {
            "plan_id": plan.plan_id,
            "label": plan.label,
            "source": plan.source.value,
            "rationale": plan.rationale,
            "total_seconds": plan.total_seconds,
            "average_seconds": plan.average_seconds,
            "estimated_total_usd": round(plan.total_seconds * per_second, 2),
            "tier": tier,
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "order": s.order,
                    "title": s.title,
                    "scene_numbers": s.scene_numbers,
                    "estimated_seconds": s.estimated_seconds,
                    "estimated_usd": round(s.estimated_seconds * per_second, 2),
                    "synopsis": s.synopsis,
                    "is_self_contained": s.is_self_contained,
                    "status": s.status.value,
                    "status_label": s.status_label,
                    # 花了多少 —— 有進度的段落才有值。
                    # 估價與實際花費放在一起，使用者才看得出估得準不準。
                    "spent_usd": round(s.spent_usd, 2),
                    "has_output": bool(s.output_uri),
                }
                for s in display_segments
            ],
        },
        # 全片成品狀態：
        #    沒有這一塊的話，FilmPanel 的「已合成」只活在前端 state ——
        #    F5 之後全片下載鍵消失，使用者要再按一次合成才拿得回來。
        #    stale = 合成之後有段落重接過（ISO 字串直接比大小）。
        "film": (
            {
                "seconds": state.film_output_seconds,
                "built_at": state.film_built_at,
                "stale": bool(state.film_built_at)
                and any(
                    s.output_built_at and s.output_built_at > state.film_built_at
                    for s in state.segments
                ),
                "burned": {
                    lang: f"/api/projects/{project_id}/media/{fname}/raw"
                    for lang, fname in state.film_burned.items()
                },
            }
            if state.film_output_uri
            else None
        ),
    }


_SHORT_SCRIPT_SCENES = 8   # 沒有章節標記、場次不超過這個數 → 整份當一段（不花錢）


def _ensure_segments(state) -> list:
    """確保專案有分段。空的話用章節方案初始化。

    為什麼自動初始化而不是逼使用者先選方案：
       章節偵測是零成本的（純字串比對），而且作者自己劃的章節
       通常就是最好的分段。多一個「請先選分段方案」的步驟只是門檻。

       使用者想改分段方式的話，之後可以在段落頁換 —— 但不該擋在這裡。
    """
    if state.segments:
        return state.segments
    if not state.script_text or not state.screenplay:
        raise HTTPException(status_code=409, detail="這個專案還沒解析劇本")

    marks = segmenter.detect_chapters(state.script_text)
    plan = segmenter._plan_from_chapters(state.screenplay, marks)  # noqa: SLF001
    scenes = state.screenplay.scenes
    if not plan and 0 < len(scenes) <= _SHORT_SCRIPT_SCENES:
        # Greenlight Local：短劇本（一場戲、一支短片）沒有章節標記很正常 ——
        #    整份當一段就好，不必為了「建議怎麼分段」再花一次 AI 呼叫。
        from ..schemas.segment import Segment

        state.segments = [Segment(
            segment_id="all", order=1, title=state.screenplay.title[:80] or "Film",
            scene_numbers=[s.scene_number for s in scenes],
            estimated_seconds=sum(s.estimated_screen_seconds for s in scenes),
            synopsis=scenes[0].action[:120], is_self_contained=True,
        )]
        project_store.get_store().save(state)
        return state.segments
    if not plan:
        raise HTTPException(
            status_code=409,
            detail="這份劇本沒有章節標記，需要先請系統建議分段（會花約 $0.03）",
        )

    state.segments = plan.segments
    project_store.get_store().save(state)
    return state.segments


@router.post("/{project_id}/look")
def set_look(project_id: str, payload: dict) -> dict:
    """定調：風格 + 比例 + 語言。這一步不花錢。

    整片一致，不逐段設定 —— 混風格會讓觀眾出戲。
    """
    state = _load(project_id)

    style_id = payload.get("style_id", state.style_id)
    aspect_ratio = payload.get("aspect_ratio", state.aspect_ratio)
    # 敘事形式：跟風格同一關選 —— 兩個都是「整片一致」的決定，
    # 而且都要在分鏡之前定（分鏡策略吃這兩個值）。
    format_id = payload.get("format_id", state.format_id)

    # 驗證：查不到就拋，不要默默存一個壞值進去
    try:
        style = config_loader.style_preset(style_id)
        config_loader.aspect_ratio(aspect_ratio)
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層的 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state.style_id = style_id
    state.aspect_ratio = aspect_ratio
    state.format_id = format_id
    state.prompt_language = payload.get("prompt_language", state.prompt_language)
    state.film_language = payload.get("film_language", state.film_language)

    if state.stage.order < Stage.LOOK_CHOSEN.order:
        state.stage = Stage.LOOK_CHOSEN
    fmt = config_loader.format_preset(format_id)
    state.record_decision(
        choice=f"{style.get('label')} · {fmt.get('label')} · {aspect_ratio}",
        rationale=str(style.get("one_liner", "")).strip(),
    )
    project_store.get_store().save(state)

    return {
        "style_id": state.style_id,
        "format_id": state.format_id,
        "aspect_ratio": state.aspect_ratio,
        "prompt_language": state.prompt_language,
        "film_language": state.film_language,
        "stage": state.stage.value,
    }


@router.post("/{project_id}/segments/{order}/format")
def set_segment_format(project_id: str, order: int, payload: dict) -> dict:
    """設定這一段的敘事形式。$0。

    為什麼是段落層：
        一份劇本裡可能混合多種類型 —— 第一章是敘事、第二章是訪談 ——
        所以敘事形式允許逐段覆寫。

    這跟風格的分層剛好相反：風格必須全片一致（角色長相是照風格
       生的），敘事形式可以逐段不同（它只影響分鏡策略）。
    改了不會動到已經分好的鏡頭表 —— 要對那一段重新分鏡才生效。
       這裡回傳 `needs_replan` 讓 UI 講清楚，不要讓使用者以為改完就好了。
    """
    state = _load(project_id)
    segment = next((s for s in state.segments if s.order == order), None)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"沒有第 {order} 段")

    format_id = payload.get("format_id")
    # null 代表「跟著專案走」，是合法的值 —— 不要當成沒填
    if format_id is not None:
        fmt = config_loader.format_preset(str(format_id))
        if not fmt:
            raise HTTPException(status_code=400, detail=f"沒有這種敘事形式：{format_id}")
        format_id = str(format_id)

    segment.format_id = format_id
    effective = segment.format_id or state.format_id or "drama"
    state.record_decision(
        choice=f"第 {order} 段敘事形式：{config_loader.format_preset(effective).get('label')}",
        rationale="段落層覆寫" if segment.format_id else "跟著專案設定",
    )
    project_store.get_store().save(state)

    return {
        "order": order,
        "format_id": segment.format_id,
        "effective_format_id": effective,
        "project_format_id": state.format_id,
        # 已經分過鏡的話，新策略要重新分鏡才吃得到
        "needs_replan": segment.shot_list is not None,
    }


@router.post("/{project_id}/tier")
def set_tier(project_id: str, payload: dict) -> dict:
    """選定畫質檔位。這一步不花錢。

    這是 `chosen_tier` 唯一的寫入路徑：方案表把檔位列出來、標了「建議」，
       沒有這條 route 就點不下去，所有生成永遠 fallback 到預設檔位。
       schema、閘門（TIER_CHOSEN）、成本引擎都以它為前提。

    整片一致，不逐鏡設定 —— 檔位混用會讓成片畫質忽高忽低，
    而且統計（成功率按檔位分桶）也會失真。
    """
    state = _load(project_id)

    raw = str(payload.get("tier", "")).strip()
    try:
        tier = QualityTier(raw)
        tier_cfg = config_loader.veo_tier(tier.value)
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層的 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"不認識的畫質檔位「{raw}」"
        ) from exc

    # 已下架的檔位擋在 API 層 —— UI 不顯示只是第一道，直接打 API 也要擋
    # （跟選角閘門「兩層都不能省」同一個道理）。
    if not tier_cfg.get("enabled", True):
        raise HTTPException(
            status_code=400,
            detail=f"檔位「{tier_cfg.get('label', raw)}」已下架，不能選用。",
        )

    state.chosen_tier = tier
    if state.stage.order < Stage.TIER_CHOSEN.order:
        state.stage = Stage.TIER_CHOSEN
    state.record_decision(
        choice=f"畫質檔位：{tier_cfg.get('label', tier.value)}",
        rationale=f"${tier_cfg.get('per_second_usd', 0)}/秒",
    )
    project_store.get_store().save(state)

    return {"chosen_tier": state.chosen_tier.value, "stage": state.stage.value}


@router.get("/{project_id}/learning/records")
def get_learning_records(project_id: str, limit: int = 40) -> dict:
    """ClickHouse 裡的原始生成紀錄。$0，唯讀。

    為什麼要有這個 route：
       學習迴路面板只給統計（幾筆、一次過關率、提煉筆記）的話，
       那是「系統說它有」。使用者要看的是資料本身：
       統計可以編，一筆一筆帶時間戳、鏡號、模型 id、實際花費與延遲的
       原始列編不出來。這條路徑是可信度的來源。

    唯讀、不分頁。目的是「看得到真的有東西」，不是做資料瀏覽器。
    """
    from ..services import ledger

    _load(project_id)  # 專案不存在就 404，不要讓人用它撈別人的資料
    data = ledger.recent_records(limit=limit, project_id=project_id)
    return {
        "project_id": project_id,
        **data,
        # 連線目標也攤開 —— 「它真的連著一個外部資料庫」本身就是證據
        "endpoint": ledger.endpoint_label(),
    }


@router.get("/{project_id}/learning")
def get_learning(project_id: str) -> dict:
    """學習迴路的現況 —— ClickHouse 學到了什麼。$0，唯讀。

    為什麼要有這個 route：
       學習迴路一直在跑（每次裁決寫入、每段完成提煉、規劃時查回來），
       但全部發生在後台。使用者看不到的迴路等於不存在 ——
       核心零件必須看得見在轉。
    """
    from ..services import refinery

    state = _load(project_id)
    style_id = state.style_id or ""

    available = ledger.is_available()
    stats = ledger.overall_stats(style_id) if available else {"available": False}

    notes = None
    try:
        loaded = refinery.load_notes(style_id)
        if loaded and loaded.notes:
            notes = {
                "text": loaded.notes,
                "sample_size": loaded.sample_size,
                "refined_at": loaded.refined_at,
            }
    except Exception:  # noqa: BLE001
        pass  # 筆記讀不到不影響統計顯示

    return {
        "project_id": project_id,
        "style_id": style_id,
        # 明講資料庫是誰 —— 學習迴路要看得出是哪個外部資料庫在累積
        "database": ledger.database_label(),
        "connected": available,
        "records": stats.get("total", 0) if stats.get("available") else 0,
        "first_pass_rate": stats.get("first_pass_rate"),
        "first_tries": stats.get("first_tries", 0),
        "rejected": stats.get("rejected", 0),
        "distinct_shots": stats.get("distinct_shots", 0),
        "spent_usd": stats.get("spent_usd", 0.0),
        "refined_notes": notes,
        # 提煉門檻：不足時 UI 要說「還在收集」而不是空白。
        # 讀提煉層自己的常數，不要在這裡再寫一次數字 ——
        # 兩邊分開寫的話，調門檻只會改到一邊，UI 從此說謊。
        "refine_threshold": refinery.MIN_SAMPLES_TO_REFINE,
    }


@router.get("/{project_id}/segments/{order}/plan-estimate")
def estimate_plan_shots(project_id: str, order: int) -> dict:
    """分鏡要花多少錢。這一步不花錢，只是估算。

    花錢之前先報價 —— 跟上傳頁的解析費用同一個原則。
    """
    state = _load(project_id)
    segments = _ensure_segments(state)
    segment = next((s for s in segments if s.order == order), None)
    if not segment:
        raise HTTPException(status_code=404, detail=f"沒有第 {order} 段")

    scenes = [
        s
        for s in state.screenplay.scenes
        if s.scene_number in segment.scene_numbers
    ]
    source = "\n".join(s.raw_text for s in scenes)

    est = script_ingest.estimate_parse_cost(source)
    # 分鏡的輸出比解析多 —— 每個鏡頭要寫中英雙語描述、提示詞、風險標記。
    # 實測一章：2,570 in / 3,406 out，輸出是輸入的 1.33 倍。
    out_tokens = int(est["estimated_input_tokens"] * 1.33)
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("shot_planning", "pro")
    cfg = rates.estimate_cfg("分鏡", pricing.get("tiers", {}).get(tier_key, {}))   # 依設定頁：Nemotron 就用 Nemotron 的價
    cost = (est["estimated_input_tokens"] / 1_000_000) * float(
        cfg.get("input_per_1m_usd", 2.0)
    ) + (out_tokens / 1_000_000) * float(cfg.get("output_per_1m_usd", 12.0))

    return {
        "segment": {
            "order": segment.order,
            "title": segment.title,
            "scene_count": len(segment.scene_numbers),
            "estimated_seconds": segment.estimated_seconds,
            "status": segment.status.value,
            "has_shots": segment.shot_list is not None,
            # 段落敘事形式覆寫（None = 跟專案）；前端段落頁一進來就要知道
            "format_id": segment.format_id,
            "effective_format_id": segment.format_id or state.format_id or "drama",
        },
        "estimated_input_tokens": est["estimated_input_tokens"],
        "estimated_output_tokens": out_tokens,
        "estimated_cost_usd": round(cost, 3),
        "estimated_cost_low_usd": round(cost * 0.75, 3),
        "estimated_cost_high_usd": round(cost * 1.4, 3),
        "model_label": cfg.get("label"),
    }


@router.post("/{project_id}/segments/{order}/plan-shots")
def plan_shots(project_id: str, order: int, force: bool = False) -> dict:
    """替某一段分鏡。這一步會花錢（約 $0.05，依段落長度）。

    同步執行，跑 30–120 秒。前端要顯示等待狀態。

    force=true 允許重新分鏡：
    會先把這一段的生成進度全部作廢（批次、試拍、成片、音色鎖 ——
    跟批次重置同一個共用函式），因為舊鏡頭是照舊分鏡表生的，
    新表的鏡號跟內容都對不上。影片檔不刪，沉到媒體庫由使用者處置。
    """
    from ..agents import shot_planner

    state = _load(project_id)
    segments = _ensure_segments(state)
    segment = next((s for s in segments if s.order == order), None)
    if not segment:
        raise HTTPException(status_code=404, detail=f"沒有第 {order} 段")

    if segment.shot_list is not None and not force:
        raise HTTPException(
            status_code=409,
            detail=f"第 {order} 段已經分鏡過了（{segment.shot_list.clip_count} 鏡）。"
            f"重新分鏡會再花一次錢，並作廢這一段的試拍與批次進度 —— "
            f"確定要重來就帶 force=true。",
        )

    if segment.shot_list is not None and force:
        # 延遲 import 避免 routes 互相引用的循環問題（同 shot_planner 的做法）
        from .video import reset_segment_progress

        summary = reset_segment_progress(state, segment)
        state.record_decision(
            choice=f"第 {order} 段重新分鏡",
            rationale=f"作廢 {summary['cleared_clips']} 個鏡頭進度"
            + (
                f"，解除音色鎖定：{'、'.join(summary['unlocked_voices'])}"
                if summary["unlocked_voices"]
                else ""
            ),
        )
        segment.shot_list = None
        segment.clear_line_direction()   # 舊分鏡的台詞導演結果作廢

    try:
        shot_list, usage = shot_planner.plan_segment(
            state.screenplay,
            segment,
            style_id=state.style_id,
            aspect_ratio=state.aspect_ratio,
            # 分鏡時就用目標語言寫畫面描述 —— 比事後再改寫一次省一趟。
            lang=state.prompt_language or "zh",
            film_lang=state.film_language or "zh",
            # 段落可以覆寫敘事形式（一個劇本可能混合幾種類型）
            format_id=segment.format_id or state.format_id or "drama",
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層的 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"分鏡失敗：{exc}") from exc

    # 設好 shot_list，段落狀態自己就是 SHOTS_PLANNED ——
    # status 是推導的 property，不用（也不能）在這裡指定。
    segment.shot_list = shot_list
    segment.clear_line_direction()       # 新分鏡：舊的台詞導演結果作廢（鏡號會重複）
    segment.spent_usd += usage["cost_usd"]
    state.spend.llm_usd += usage["cost_usd"]
    if state.stage.order < Stage.SHOTS_PLANNED.order:
        state.stage = Stage.SHOTS_PLANNED
    state.record_decision(
        choice=f"第 {order} 段分鏡：{shot_list.clip_count} 鏡 / {shot_list.total_seconds} 秒",
        rationale=f"{usage['model']}，${usage['cost_usd']}",
    )
    project_store.get_store().save(state)

    return {
        "order": order,
        "title": segment.title,
        "clip_count": shot_list.clip_count,
        "total_seconds": shot_list.total_seconds,
        "api_call_count": shot_list.api_call_count,
        "planning_notes": shot_list.planning_notes,
        "usage": usage,
    }


def _constraint_payload(shot, style_id: str) -> dict:
    """一個鏡頭的約束方案，攤成前端看得懂的形狀。

    `skipped` 是這裡最重要的一段。

       約束按預期提升排序後只取前 `max_rules_per_shot` 條（目前是 6）。
       所以 content_flags 越多的鏡頭匹配到越多規則、被丟掉的也越多 ——
       上限會在最難的鏡頭上砍掉最多約束。

       使用者看得到這個，才有機會判斷「這鏡要不要拆成兩鏡」。
       看不到的話，他只會覺得這鏡莫名其妙一直失敗。

    這一步是 $0 純計算（讀 config + 比對欄位），不呼叫任何 API，
    所以放在列表 payload 裡不會讓開頁面變慢或變貴。
    """
    plan = constraint_engine.plan_for_shot(shot, style_id)
    return {
        "applied": [
            {
                "rule_id": c.rule_id,
                "category_label": c.category_label,
                "principle": c.principle,
                "effective_lift": c.effective_lift,
            }
            for c in plan.applied
        ],
        "skipped": [
            {
                "rule_id": s.rule_id,
                "category_label": s.category_label,
                "principle": s.principle,
                "lost_lift": s.lost_lift,
            }
            for s in plan.skipped
        ],
        "matched_count": plan.matched_count,
        "total_lift": plan.total_lift,
        # 不是所有問題都能靠提示詞解決。這幾條要改工作流或補素材。
        "advisories": plan.advisories,
    }


@router.get("/{project_id}/segments/{order}/shots")
def get_segment_shots(project_id: str, order: int) -> dict:
    """某一段的鏡頭表與成本估算。

    鏡頭表要先跑過分鏡才有 —— 沒有的話回 409 並說明該做什麼，
    不要回空陣列假裝一切正常。
    """
    state = _load(project_id)
    segment = next((s for s in state.segments if s.order == order), None)

    if not segment or not segment.shot_list:
        raise HTTPException(
            status_code=409,
            detail=f"第 {order} 段還沒分鏡。請先執行分鏡（scripts/plan_segment.py）。",
        )

    shot_list = segment.shot_list
    plan = cost_engine.build_plan(
        plan_id=f"{project_id}-seg{order}",
        shot_list=shot_list,
        # 接上學習迴路。這個參數是 None 的話，
        #    整條估價鏈路一路 fallback 到 _no_history、全程用先驗值 ——
        #    ClickHouse 寫了、查詢函式寫了、就是沒有人把兩端接起來。
        history_lookup=ledger.make_history_lookup(state.style_id or ""),
        # 檔位清單走 config：
        #    寫死在這裡的話，下架一個檔位要改 code —— 配置驅動就是要防這個。
        tiers=[QualityTier(t) for t in config_loader.selectable_veo_tiers()],
    )

    return {
        # format_id：這一段自己的敘事形式覆寫（None = 跟專案）；effective 是實際生效的。
        # 要在這裡回，否則前端段落頁沒地方讀到已存的覆寫，重新載入就顯示成「跟專案設定」。
        "segment": {
            "order": segment.order,
            "title": segment.title,
            "format_id": segment.format_id,
            "effective_format_id": segment.format_id or state.format_id or "drama",
        },
        "shot_list": {
            "style_id": shot_list.style_id,
            "aspect_ratio": shot_list.aspect_ratio,
            "clip_count": shot_list.clip_count,
            "total_seconds": shot_list.total_seconds,
            "api_call_count": shot_list.api_call_count,
            "planning_notes": shot_list.planning_notes,
            "shots": [
                {
                    "shot_id": s.shot_id,
                    "scene_number": s.scene_number,
                    "base_duration_seconds": s.base_duration_seconds,
                    "extensions": s.extensions,
                    "total_seconds": s.total_seconds,
                    "shot_type": s.shot_type.value,
                    "camera_move": s.camera_move.value,
                    "description": s.description,
                    "visual_prompt": s.visual_prompt,
                    "characters_in_frame": s.characters_in_frame,
                    "dialogue": s.dialogue,
                    # speaker 要 serialize：schema 有的欄位 route 要跟上，
                    #    否則從 API 看永遠是 None，查「音色有沒有接上」時會誤判成 agent 沒填。
                    "speaker": s.speaker,
                    "content_flags": [f.value for f in s.content_flags],
                    "continues_from": s.first_frame_from,
                    # 約束方案攤出來。ConstraintPlan.skipped 要顯示出來、不能靜默丟棄 ——
                    #    這一鏡匹配到幾條、套用得下幾條，是使用者判斷
                    #    「為什麼這鏡成功率特別低」的關鍵資訊。
                    "constraints": _constraint_payload(s, state.style_id or ""),
                }
                for s in shot_list.shots
            ],
        },
        "variants": [
            {
                "tier": v.tier.value,
                "label": v.tier_label,
                "video_base_usd": v.cost.video_base_usd,
                "retry_expected_usd": v.cost.retry_expected_usd,
                "total_usd": v.cost.total_usd,
                "success_rate": v.overall_success_rate,
                "confidence": v.confidence.value,
                "budget_verdict": v.budget_verdict.value,
                "riskiest": [
                    {
                        "shot_id": e.shot_id,
                        "success_rate": e.success_rate,
                        "expected_attempts": e.expected_attempts,
                        # 風險說明在這裡才變成人話 —— success_predictor 只回 key。
                        # 回三語讓前端切語言不用重打 API。
                        "risk_factors": risk_text.localize_all(e.risk_factors),
                    }
                    for e in v.riskiest_shots
                ],
            }
            for v in plan.variants
        ],
        "recommended_tier": plan.recommended_tier.value if plan.recommended_tier else None,
        # 使用者實際選定的檔位（沒選過就 None）。跟 recommended_tier 分開回 ——
        # 「系統建議」與「使用者決定」是兩件事，UI 要能同時呈現。
        "chosen_tier": state.chosen_tier.value if state.chosen_tier else None,
        "recommendation_reason": risk_text.localize(plan.recommendation_reason),
        "test_shot_required": plan.test_shot_required,
        "test_shot_candidate": plan.test_shot_candidate,
    }


# ---------------------------------------------------------------------------
# Rewind —— 退回到前面的階段
#
# 設計理由：
#     使用者選完角色才發現「這個故事應該用動畫風」是很正常的事。
#     schemas.project.rewind_to() 只回失效清單、不自動清資料，
#     實際要不要清由這裡在使用者確認後執行。
#
# 兩段式：preview 先讓使用者看代價，POST 才真動。
# 檔案一律留給媒體庫 —— rewind 不刪任何生成的圖或影片，只清 project state。
# ---------------------------------------------------------------------------


def _parse_target(target: str) -> Stage:
    """字串 → Stage enum。認不得就回 400 並把有效值全列出來。

    為什麼要有這個函式？
        target 從 query string / request body 進來，是自由字串。
        直接 `Stage(target)` 會拋 ValueError → FastAPI 回 500，
        而那看起來像產品壞掉，實際只是輸入打錯。

    verify_wiring 有對應測項（「無效階段回 400 不是 500」）。

    錯誤訊息一定要列出所有有效值。只說「無效的階段」等於要人去翻原始碼。

    Args:
        target: 階段字串，例如 "look_chosen"。

    Returns:
        對應的 Stage enum。

    Raises:
        HTTPException(400): 不是有效的階段值。
    """
    try:
        return Stage(target)
    except ValueError as exc:
        valid = ", ".join(s.value for s in Stage)
        raise HTTPException(
            status_code=400,
            detail=f"「{target}」不是有效的階段。可用：{valid}",
        ) from exc


@router.get("/{project_id}/rewind/preview")
def rewind_preview(project_id: str, target: str = Query(...)) -> dict:
    """看看退回到 target 階段會讓什麼失效。$0，不改任何狀態。

    為什麼要有這個 endpoint？
        rewind 沒有 undo。退回去就是把已經花錢生出來的選角、提示詞、
        試拍結果標記成失效 —— 使用者必須先看到代價才能決定值不值得。
        `ProjectState.rewind_to()` 分成兩段（只回報後果、不自動清資料），
        這支是「只回報」的那一段。

    這支絕對不能有副作用。使用者按下拉選單挑階段就會觸發它，
       一次挑錯就清掉十輪選角是不可接受的。verify_wiring 有兩條
       測項專門守這件事（preview 沒改 stage、preview 沒清 clips）。

    Args:
        project_id: 專案 ID。
        target: 想退回的階段字串。

    Returns:
        {current, current_label, target, target_label, can_rewind, invalidated[]}。
        invalidated 是人話清單，直接顯示給使用者看。
    """
    state = _load(project_id)
    target_stage = _parse_target(target)
    invalidated = state.rewind_to(target_stage)
    current_label = STAGE_LABELS.get(state.stage, (state.stage.value, ""))[0]
    target_label = STAGE_LABELS.get(target_stage, (target_stage.value, ""))[0]
    return {
        "project_id": project_id,
        "current": state.stage.value,
        "current_label": current_label,
        "target": target_stage.value,
        "target_label": target_label,
        "can_rewind": target_stage.order < state.stage.order,
        "invalidated": invalidated,
    }


@router.post("/{project_id}/rewind")
def rewind_commit(project_id: str, payload: dict) -> dict:
    """真的退回到 target 階段。清掉會失效的 project state。$0，不可復原。

    為什麼要有這個 endpoint？
        使用者選完角色才發現「這個故事應該用動畫風」是很正常的事。
        沒有這支，他唯一的選擇是整個專案重來 —— 之前抽的角色圖、
        寫的提示詞、試拍的鏡頭全部變成沉沒成本。

    不刪媒體檔 —— 生成的圖與影片留給媒體庫。使用者事後回去
       挑「好看的留、醜的刪」。這是刻意的分工：
       rewind 管 project state，檔案生命週期歸媒體庫管。
       verify_wiring 有測項守著（rewind 後 fixture 圖還在）。

    選角關卡：只 unlock、不刪 candidates。使用者退回後再進選角頁，
       看得到之前抽過的候選圖 —— 也許有一張本來就可以用。
       刪掉等於強迫他重花一輪 $0.065。

    不動 ClickHouse ledger。學習迴路的價值就在於它跨得過重來：
       使用者重跑一次，系統應該比上一次更準，而不是回到什麼都不知道。

    清除範圍依 target 的 order 逐段判斷，順序不能調換 ——
       每個 if 對應 Stage 順序上的一個門檻，混在一起會漏清或誤清。

    退到跟當前同一階段（或更後面）是 no-op，回 note 說明而不是報錯。
       前端下拉選單只列更早的階段，但 API 不能假設呼叫端一定守規矩。

    Args:
        project_id: 專案 ID。
        payload: {"target": "<stage 字串>"}。

    Returns:
        {target, target_label, invalidated[], cleared{}, new_stage, note}。
        cleared 是各類別的清除計數，給前端顯示「已清掉 X 個」。
    """
    state = _load(project_id)
    target = _parse_target(payload.get("target", ""))
    invalidated = state.rewind_to(target)
    if not invalidated and target.order >= state.stage.order:
        return {
            "project_id": project_id,
            "target": target.value,
            "invalidated": [],
            "cleared": {},
            "new_stage": state.stage.value,
            "note": "沒有需要退回的內容",
        }

    cleared = {
        "casting_sessions_unlocked": 0,
        "prompts_exported": False,
        "test_shots": 0,
        "segment_clips": 0,
        "segment_outputs": 0,
        "segment_subtitles": 0,
        "chosen_tier": False,
    }

    if target.order < Stage.CAST_LOCKED.order and state.casting:
        for session in state.casting.sessions:
            if session.is_locked:
                cleared["casting_sessions_unlocked"] += 1
            chosen = session.selected
            if chosen:
                chosen.voice_tags = ""
            session.selected_candidate_id = None
            session.voice_locked_from = None

    if target.order < Stage.PROMPTS_READY.order:
        if state.prompts_exported_at:
            cleared["prompts_exported"] = True
        state.prompts_exported_at = None
        for seg in state.segments:
            seg.prompts_exported_at = None

    if target.order < Stage.TEST_SHOT_PASSED.order:
        if state.test_shot_id or state.test_shot_approved:
            cleared["test_shots"] += 1
        state.test_shot_id = None
        state.test_shot_approved = False
        for seg in state.segments:
            if seg.test_shot or seg.test_shot_approved:
                cleared["test_shots"] += 1
            seg.test_shot = None
            seg.test_shot_id = None
            seg.test_shot_approved = False

    if target.order < Stage.TIER_CHOSEN.order:
        if state.chosen_tier is not None:
            cleared["chosen_tier"] = True
        state.chosen_tier = None

    if target.order < Stage.GENERATING.order:
        for seg in state.segments:
            cleared["segment_clips"] += len(seg.clips)
            seg.clips = []
            seg.spent_usd = 0.0

    if target.order < Stage.ASSEMBLED.order:
        for seg in state.segments:
            if seg.output_uri:
                cleared["segment_outputs"] += 1
            if seg.subtitles:
                cleared["segment_subtitles"] += len(seg.subtitles)
            seg.output_uri = None
            seg.output_seconds = 0.0
            seg.output_built_at = ""
            seg.subtitles = {}
            seg.burned = {}
        # 段落成片都作廢了，全片跟它的燒錄自然也是
        state.film_output_uri = None
        state.film_output_seconds = 0.0
        state.film_built_at = ""
        state.film_subtitles = {}
        state.film_burned = {}

    target_label = STAGE_LABELS.get(target, (target.value, ""))[0]
    state.record_decision(
        choice=f"退回到「{target_label}」",
        rationale=(
            "清掉：" + "、".join(invalidated) if invalidated else "退回但無失效項目"
        ),
        followed_recommendation=False,
    )
    state.stage = target
    state.touch()
    project_store.get_store().save(state)

    return {
        "project_id": project_id,
        "target": target.value,
        "target_label": target_label,
        "invalidated": invalidated,
        "cleared": cleared,
        "new_stage": state.stage.value,
        "note": "檔案已保留在媒體庫，未刪除",
    }
