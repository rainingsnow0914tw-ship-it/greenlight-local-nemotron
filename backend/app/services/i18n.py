"""多語文字取值 —— 中文 / 英文 / 日本語。

設計理由：
    UI 介面要有中／英／日三語版本 —— 目標受眾跨這三個語言圈，
    hosted URL 點進去只有單一語言會直接吃虧。

    但多語不只是前端的事。系統產出的文字大部分來自後端：
    風格名稱、約束原理、風險提示、階段名稱、方向按鈕……
    這些散落在 config 與 code 裡，需要一套統一的取值方式。

═══════════════════════════════════════════════════════════════════════════
為什麼是 `label` + `label_en` 而不是 `label: {zh: ..., en: ...}`
═══════════════════════════════════════════════════════════════════════════

    config 檔是給人讀的 —— 維護者會直接打開 constraints.yaml 看規則對不對。
    巢狀成 {zh: ..., en: ...} 會讓每一條規則變成兩倍高，可讀性大幅下降。

    後綴法保持中文在前、一眼可讀，英文是補充。
    而且**漸進式**：還沒翻譯的欄位自動退回中文，不會因為漏翻就整個壞掉。

退回中文時要記 log。「英文介面上出現中文」是使用者看得到的瑕疵，
   不該靜默發生 —— 上線前應該把 log 掃一遍，補齊缺的翻譯。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ZH = "zh"
EN = "en"
JA = "ja"

# 所有「要對每個語言做一遍」的地方都讀這個 tuple，不要各自寫死。
#    若 pair() 寫死回 {zh, en}，加了日文之後前端永遠拿不到 ja，
#    日文介面下所有 config 文字都退回中文 —— 而 config 裡明明有 label_ja。
#    兩邊看起來都「有做」，所以特別難查。
SUPPORTED = (ZH, EN, JA)

DEFAULT_LANG = ZH

# 缺翻譯的欄位。啟動後累積，上線前用 missing_report() 掃一遍。
_missing: set[str] = set()


def pick(item: dict[str, Any], field: str, lang: str = DEFAULT_LANG) -> str:
    """從一個 config 項目取出指定語言的文字。

        pick(style, "label", "en")  → style["label_en"]，沒有就退回 style["label"]

    為什麼不直接寫 `item.get(f"{field}_en") or item[field]`：
        因為要記錄「哪些欄位缺翻譯」。散在各處寫 or 的話，
        缺翻譯這件事會永遠沉默 —— 直到使用者在英文介面上看到中文。
    """
    if lang == ZH:
        return str(item.get(field, ""))

    key = f"{field}_{lang}"
    value = item.get(key)
    if value:
        return str(value)

    # 退回中文，並記下來
    fallback = str(item.get(field, ""))
    if fallback:
        identifier = item.get("id") or item.get("label") or "?"
        marker = f"{identifier}.{key}"
        if marker not in _missing:
            _missing.add(marker)
            logger.info("缺 %s 翻譯，暫時顯示中文：%s", lang, marker)
    return fallback


def pair(item: dict[str, Any], field: str) -> dict[str, str]:
    """一次取出**所有支援語言**的版本，給 API 回應用。

    前端拿到 {"zh": "...", "en": "...", "ja": "..."} 之後自己決定顯示哪個，
    不需要為了切語言重打一次 API。這對「切換語言」要是即時的很重要 ——
    使用者按下日本語，介面應該立刻變，不是轉圈等後端。

    跟著 SUPPORTED 走，不寫死語言清單：
       寫死只回 {zh, en} 的話，**前端永遠拿不到日文**，
       日文介面下所有從 config 來的文字都退回中文。

       config 裡明明有 `label_ja`，畫面上卻是中文 —— 這種 bug 特別難查，
       因為兩邊看起來都「有做」。跟著 SUPPORTED 走，
       以後加語言不用回來改這裡。
    """
    return {lang: pick(item, field, lang) for lang in SUPPORTED}


class Localized:
    """程式碼裡的多語字串（給不在 config 裡的文字用）。

    用法：
        RISK = Localized("畫面中有手部特寫", "Hands visible in frame", "手のアップあり")
        RISK.get("ja")

    為什麼需要這個：
        success_predictor 的風險說明、project 的階段名稱這類文字
        寫在 code 裡而不是 config 裡（它們跟邏輯綁得很緊，
        抽到 config 反而會讓兩邊容易不同步）。
        這個小類別讓它們也能多語，而不用退回「到處寫 if lang == 'en'」。

    沒給的語言退回中文 —— 跟 pick() 同一套規則。
    """

    __slots__ = ("zh", "en", "ja")

    def __init__(self, zh: str, en: str = "", ja: str = ""):
        self.zh = zh
        self.en = en or zh
        self.ja = ja or zh

    def get(self, lang: str = DEFAULT_LANG) -> str:
        return {ZH: self.zh, EN: self.en, JA: self.ja}.get(lang, self.zh)

    def as_dict(self) -> dict[str, str]:
        return {ZH: self.zh, EN: self.en, JA: self.ja}

    def __str__(self) -> str:  # 讓既有的 f-string 用法不會壞掉
        return self.zh


# 舊名保留，避免既有 import 壞掉
Bilingual = Localized


def missing_report() -> list[str]:
    """列出目前為止偵測到的缺翻譯欄位。

    上線前必跑。英文介面上出現中文是使用者看得到的瑕疵。
    """
    return sorted(_missing)


def reset_missing() -> None:
    """清空缺翻譯紀錄。測試用。"""
    _missing.clear()
