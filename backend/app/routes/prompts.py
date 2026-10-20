"""提示詞導出的 endpoint（第 6 關）。

選好角色之後、生成影片之前，先把全部提示詞導出來 ——
使用者可以拿去別的工具試，確認沒問題再進下一步。

這一關的三個價值：
    1. 免費檢查點 —— 發現「主角描述根本不對」在這裡花 $0，
       在生成之後花 $18。差 900 倍。
    2. 不鎖定使用者 —— 敢讓人把成果帶走的工具，比綁著他的工具值得信任。
    3. 透明度 —— 提示詞是這個系統的核心產出，藏起來等於要人盲信。

全部 $0。這一關不呼叫任何模型 —— 提示詞是 code 組裝的，
   組裝規則在 services/prompt_export.py。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from ..services import config_loader, project_store, prompt_export

from ..services import demo_mode  # noqa: E402

router = APIRouter()

_FORMATS = {
    "text": ("export_plain_text", "text/plain; charset=utf-8", "txt"),
    "markdown": ("export_markdown", "text/markdown; charset=utf-8", "md"),
    "json": ("export_json", "application/json; charset=utf-8", "json"),
}


def _segment(project_id: str, order: int):
    state = project_store.get_store().load(project_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"找不到專案「{project_id}」")

    segment = next((s for s in state.segments if s.order == order), None)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"找不到第 {order} 段")
    if segment.shot_list is None:
        raise HTTPException(
            status_code=409,
            detail=f"第 {order} 段還沒分鏡，沒有提示詞可以導出。",
        )
    return state, segment


def _fix_prompt_names(state, segment) -> None:
    """改寫成英文／日文的畫面描述，名字照名字表（月音 → Tsukine，不是模型自己拼的 Yueyin）。

    改寫的模型只看得到中文名，會自己拼拼音（範例 2 英文提示詞整段 Yueyin）；
    名字表的寫法有唯一的家（language/glossary.py），換名字用介面翻譯同一支 fix_names。"""
    from ..services.ui_translate import fix_names

    lang = segment.shot_list.prompt_language
    if not lang or lang == "zh" or not state.name_glossary:
        return
    for shot in segment.shot_list.shots:
        if getattr(shot, "visual_prompt", None):
            shot.visual_prompt = fix_names(shot.visual_prompt, state.name_glossary, lang)


@router.get("/{project_id}/segments/{order}/prompts")
def get_prompts(project_id: str, order: int) -> dict:
    """整段的提示詞，分段呈現。$0。

    回傳結構化資料而不是一整坨字串 —— 使用者要看得出
    哪一段是角色標籤、哪一段是物理約束，才知道要改什麼。
    """
    state, segment = _segment(project_id, order)
    casting = state.casting

    # 導出的形態要跟實際生成用的一致：
    #    Omni 走敘事式、Veo 走標籤式。使用者複製走的那段，
    #    就是送給模型的那段 —— 不然這一關的透明度是假的。
    tier = (state.chosen_tier.value if state.chosen_tier else None) or config_loader.default_veo_tier()
    _fix_prompt_names(state, segment)   # 舊資料（範例）裡已經寫成 Yueyin 的，讀的時候也照名字表
    prompts = prompt_export.build_all(segment.shot_list, casting, tier)

    # 誠實提醒要精確：
    #    已選定的角色有標籤、沒選定的才沒有。籠統的警告
    #    （「提示詞裡沒有角色外貌標籤」）會讓人以為整份提示詞都不能用，
    #    於是白白重跑一次。
    #    所以只點名真正還缺的那幾個角色，而且只算這一段實際出現的。
    casting_ready = bool(casting and casting.can_proceed)

    in_segment = {
        name
        for shot in segment.shot_list.shots
        for name in (shot.characters_in_frame or [])
    }
    missing = sorted(
        name
        for name in in_segment
        if not (casting and (s := casting.session_for(name)) and s.is_locked)
    )

    return {
        "project_id": project_id,
        "order": order,
        "title": segment.title,
        "style_id": segment.shot_list.style_id,
        "aspect_ratio": segment.shot_list.aspect_ratio,
        "prompt_language": segment.shot_list.prompt_language,
        "casting_ready": casting_ready,
        # 這一段用不到的角色沒選定，不影響這一段的提示詞 ——
        # 所以警告看的是 missing 而不是整片的 can_proceed。
        "casting_warning": (
            ""
            if not missing
            else f"這一段有 {len(missing)} 個角色還沒選定："
            f"{'、'.join(missing)}。"
            f"他們出現的鏡頭沒有外貌標籤，同一個角色在不同鏡頭會長得不一樣。"
        ),
        "characters_missing": missing,
        "shot_count": len(prompts),
        "prompts": prompts,
    }


@router.post("/{project_id}/segments/{order}/prompts/rewrite")
def rewrite_prompts(project_id: str, order: int, lang: str = "en") -> dict:
    """把畫面描述改寫成指定語言，填進 `visual_prompt`。約 $0.002。

    這是導出前的可選步驟。不做的話導出會是中英混雜
    （英文標籤與約束 + 中文描述），拿去別的工具測試時
    測到的是「工具讀不懂」而不是「鏡頭設計好不好」。

    只改寫畫面描述，台詞不動 —— 台詞屬於影片語言，
       翻譯它等於改變角色說的話。
    """
    from ..agents import prompt_writer

    state, segment = _segment(project_id, order)

    try:
        filled, usage = prompt_writer.rewrite_shot_list(
            segment.shot_list,
            lang,
            # 沒有這兩個，AI 角色會被翻成人：
            #    「X 獨自坐在」→ "A young man sits"，但 X 是沒有身體的 AI。
            #    翻譯會把原文的模糊變成譯文的斷言。
            casting=state.casting,
            non_human=set(state.non_human_characters or []),
        )
    except demo_mode.DemoBlocked:
        # Demo 模式的擋下不是錯誤，要讓它穿透到最外層 handler
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"改寫失敗：{exc}") from exc

    segment.shot_list.prompt_language = lang
    _fix_prompt_names(state, segment)
    state.spend.llm_usd += usage["cost_usd"]
    state.record_decision(
        choice=f"第 {order} 段提示詞改寫成 {lang}",
        rationale=f"{filled} 個鏡頭，${usage['cost_usd']}",
    )
    project_store.get_store().save(state)

    return {
        "rewritten": filled,
        "total": len(segment.shot_list.shots),
        "language": lang,
        "usage": usage,
    }


@router.get("/{project_id}/segments/{order}/prompts/download")
def download_prompts(
    project_id: str,
    order: int,
    fmt: str = Query("text", alias="format"),
) -> Response:
    """下載提示詞檔案。$0。

    三種格式各有用途：
        text     貼到任何地方都能用，最通用
        markdown 附上每條約束的原理，適合自己讀或分享
        json     給要接自己流程的人
    """
    if fmt not in _FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的格式「{fmt}」。可用：{', '.join(_FORMATS)}",
        )

    state, segment = _segment(project_id, order)
    func_name, media_type, ext = _FORMATS[fmt]
    # 下載的那份也要跟生成用的形態一致（見 get_prompts 的說明）
    tier = (state.chosen_tier.value if state.chosen_tier else None) or config_loader.default_veo_tier()
    body = getattr(prompt_export, func_name)(segment.shot_list, state.casting, tier)

    filename = f"{project_id}-seg{order}-prompts.{ext}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
