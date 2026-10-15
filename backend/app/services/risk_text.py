"""風險說明的三語呈現層。

為什麼獨立一層：
    success_predictor 的職責是算出這一鏡有多容易失敗，
    不是決定用什麼語言講。若它回寫死的中文字串清單，
    日文介面下整塊風險說明仍然是中文，而那正是這個產品
    最有價值的資訊（「為什麼這一鏡會失敗」）。

    所以拆成兩件事：
        success_predictor  →  回 key（`night_scene`、`extension_drift|2|22`）
        risk_text（這裡）  →  key + 語言 → 人話

    資料層講 key、呈現層講語言。同一套做法也用在
    casting directions 與 prompt rooms 上。

參數格式：
    `key` 或 `key|參數0|參數1`，參數依序填進 label 裡的 {0} {1}。
    扁平字串而不是 dict，是為了不動 `risk_factors: list[str]` 這個型別：
    改型別會牽動 schema、route、前端三層，而這裡要傳的資訊少到不值得。
"""

from __future__ import annotations

from typing import Any

from . import config_loader, i18n

_SEP = "|"


def _labels() -> dict[str, Any]:
    return config_loader.priors().get("risk_labels", {})


def _resolve_param(value: str) -> dict[str, str]:
    """把參數本身也翻成三語（如果它是一個 config 裡的 id）。

    `style_unforgiving|photoreal` 的參數是風格 id，
    中文要顯示「實拍寫實」、英文要顯示 "Photoreal"。
    認不得的參數（數字、已經是人話的字串）原樣帶過。
    """
    try:
        # style_preset 查不到會拋 ConfigError（那是它該有的行為，
        # 風格 id 打錯不該靜默通過）。但這裡的參數本來就可能不是風格 id，
        # 例如「3」或「22」，所以查不到是正常情況。
        return i18n.pair(config_loader.style_preset(value), "label")
    except Exception:  # noqa: BLE001
        return {lang: value for lang in i18n.SUPPORTED}


def localize(raw: str) -> dict[str, str]:
    """一條風險說明 → {zh, en, ja}。

    認不得的 key 原樣回傳（三語都是同一個字串）：
    這樣新增風險項目時忘了補翻譯，畫面上會看到原始 key 而不是空白，
    比靜默消失容易發現得多。
    """
    key, _, rest = raw.partition(_SEP)
    entry = _labels().get(key)

    if not isinstance(entry, dict):
        return {lang: raw for lang in i18n.SUPPORTED}

    template = i18n.pair(entry, "label")
    if not rest:
        return template

    params = rest.split(_SEP)
    resolved = [_resolve_param(p) for p in params]

    out: dict[str, str] = {}
    for lang in i18n.SUPPORTED:
        args = [r.get(lang, r.get("zh", "")) for r in resolved]
        try:
            out[lang] = template.get(lang, template.get("zh", "")).format(*args)
        except (IndexError, KeyError):
            # 佔位符數量對不上就退回未格式化的模板：
            # 顯示一句帶 {0} 的怪句子，好過整個 API 500。
            out[lang] = template.get(lang, template.get("zh", ""))
    return out


def localize_all(raws: list[str]) -> list[dict[str, str]]:
    return [localize(r) for r in raws]
