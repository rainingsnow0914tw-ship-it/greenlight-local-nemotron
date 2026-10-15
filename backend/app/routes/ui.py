"""介面用的小工具：AI 生成的說明文字跟著介面語言（services/ui_translate.py）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import project_store, ui_translate

router = APIRouter()


@router.post("/translate")
def translate(payload: dict):
    """{texts: [...], lang: zh|en|ja, project_id?} → {texts: [...]}（同樣長度；專案的名字表拿來統一名字寫法）。"""
    texts = payload.get("texts")
    lang = payload.get("lang")
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts) or len(texts) > 60:
        raise HTTPException(400, "texts must be a list of up to 60 strings")
    if lang not in ui_translate.LANG_NAME:
        raise HTTPException(400, f"lang must be one of {list(ui_translate.LANG_NAME)}")
    glossary = ""
    if payload.get("project_id"):
        st = project_store.get_store().load(str(payload["project_id"]))
        glossary = (st.name_glossary or "") if st else ""
    return {"texts": ui_translate.translate([t[:2000] for t in texts], lang, glossary)}
