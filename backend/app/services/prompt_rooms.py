"""提示詞房間：三語言各自獨立的術語慣例與 UI 文案，規則只有一份。

設計理由：
    三語言版本要在後端分好房間，不互相干擾；但提示詞用中文或日文寫
    未必比英文差，所以每種語言都要能當提示詞語言。

邊界畫在哪裡：

    system instruction 不切成三份。三份製造的是「不同步」：
    改一條連戲規則要改三個檔，漏一個就三種語言行為不一致。
    那正是「互相干擾」的反面版本，一樣糟。

    分工：

      base.yaml            分鏡與解析的規則。只有一份，改一次三語都生效。
      {zh,en,ja}.yaml      該語言的術語慣例與 UI 文案。純語言差異。
      code（services/）    完全共用，一份。

    組裝：base 的規則 ＋ 該語言的術語慣例 ＋「請用 X 語言輸出」

    為什麼術語慣例不能塞進 base：
      日文的「引き／なめ／パンフォーカス」跟中文的「過肩／泛焦」
      不是翻譯關係，是各自語言的現場詞彙。這部分才真的需要分房間。
      但「相鄰鏡頭預設要接」跟語言無關，只該有一份。

語言是三個獨立的維度，不是一個：

      1. 介面語言    使用者看的文字          → 前端狀態
      2. 提示詞語言  餵給模型的畫面描述       → ShotList.prompt_language
      3. 影片語言    角色說什麼話、字幕語言    → ShotList.film_language

    三個都可以不同。實際會發生的組合：

      中文介面 ＋ 日文提示詞（動畫風更精準）＋ 日文配音（受眾在日本）
      中文介面 ＋ 日文提示詞 ＋ 中文配音（角色講中文，字幕上日文）

    為什麼提示詞語言與影片語言可以不同：
      提示詞裡「畫面描述」跟「台詞」是分開組裝的兩段
      （見 services/prompt_export.py）。
      畫面描述用日文寫、台詞用中文寫，是合法而且合理的。

「哪種語言效果最好」目前是假設不是結論。
   「Veo 對英文服從度較高」是沿用業界說法、未經三語對照實驗驗證。
   在做完實驗之前，UI 不要宣稱哪個比較好，只提供選擇。
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

from .env_boot import project_root as _project_root   # 唯一的家（打包後位置不同，那邊處理）

_PROMPT_DIR = _project_root() / "config" / "prompts"

# 支援的語言。加新語言 = 丟一個 yaml 進去 + 在這裡加代碼，不用動其他 code。
SUPPORTED = ("zh", "en", "ja")
DEFAULT = "zh"


class PromptRoomError(RuntimeError):
    """房間檔缺失或格式錯誤。"""


@functools.lru_cache(maxsize=1)
def base() -> dict[str, Any]:
    """載入共用規則檔。

    這一份是所有語言共用的。改這裡，三種語言同時生效，
    這正是不把 system instruction 切三份的理由。
    """
    path = _PROMPT_DIR / "base.yaml"
    if not path.exists():
        raise PromptRoomError(f"找不到共用規則檔：{path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise PromptRoomError(f"base.yaml 格式錯誤（最外層應為 mapping）")
    return data


@functools.lru_cache(maxsize=len(SUPPORTED))
def room(lang: str) -> dict[str, Any]:
    """載入一個語言房間。

    查不到就拋 —— 不要靜默 fallback 到中文。
    靜默 fallback 會讓「日文房間的檔名打錯」這種錯誤變成
    「日文模式下模型收到中文指示」，而且沒有任何跡象。
    """
    if lang not in SUPPORTED:
        raise PromptRoomError(
            f"不支援的語言 '{lang}'。目前支援：{', '.join(SUPPORTED)}"
        )

    path = _PROMPT_DIR / f"{lang}.yaml"
    if not path.exists():
        raise PromptRoomError(f"找不到語言房間檔：{path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise PromptRoomError(f"房間檔格式錯誤（最外層應為 mapping）：{path}")
    return data


def reload() -> None:
    """清快取。改完 yaml 想立刻生效時用。"""
    room.cache_clear()
    base.cache_clear()


def system_instruction(agent: str, lang: str = DEFAULT) -> str:
    """組出某個 agent 在某語言下的完整 system instruction。

    組裝方式：
        base.yaml 的規則（共用一份）
        ＋ 語言指示（「請用日文書寫畫面描述」）
        ＋ 該語言的術語慣例（「引き／なめ／パンフォーカス」）

    agent 只需要說「我是 shot_planner，現在要用日文」，
    規則從哪來、語言怎麼切，都是這裡的事。
    """
    rules = base().get("agents", {}).get(agent, {}).get("system_instruction", "")
    if not rules:
        raise PromptRoomError(f"base.yaml 缺少 agents.{agent}.system_instruction")

    meta = room(lang).get("meta", {})
    directive_template = base().get("language_directive", "")
    directive = directive_template.format(
        name=meta.get("name", lang),
        conventions=meta.get("writing_conventions", "").strip(),
    )

    return rules.strip() + "\n" + directive.rstrip()


def style_guidance(lang: str = DEFAULT) -> str:
    """提示詞寫作的語言特性說明。附在 user prompt 裡。"""
    return (
        room(lang)
        .get("agents", {})
        .get("prompt_writer", {})
        .get("style_guidance", "")
        .strip()
    )


def negative_suffix(lang: str = DEFAULT) -> str:
    """全域負面約束，用該語言表達。

    為什麼負面約束也要跟著語言走：
    在一段日文提示詞尾端接一句英文「No watermarks」，
    會讓模型在語言之間切換，服從度可能下降。
    整段提示詞保持同一種語言比較安全。
    """
    return (
        room(lang)
        .get("agents", {})
        .get("prompt_writer", {})
        .get("negative_suffix", "")
        .strip()
    )


def writing_conventions(lang: str = DEFAULT) -> str:
    """該語言的影視術語慣例。"""
    return room(lang).get("meta", {}).get("writing_conventions", "").strip()


def ui_text(lang: str = DEFAULT) -> dict[str, Any]:
    """介面文案。前端一次拿走整包。"""
    return room(lang).get("ui", {})


def languages() -> list[dict[str, str]]:
    """所有可用語言，給 UI 渲染切換按鈕。"""
    result = []
    for code in SUPPORTED:
        meta = room(code).get("meta", {})
        result.append(
            {
                "code": code,
                "name": meta.get("name", code),
                "name_en": meta.get("name_en", code),
            }
        )
    return result


# ---------------------------------------------------------------------------
# 提示詞語言的預設建議
# ---------------------------------------------------------------------------

def suggest_prompt_language(style_id: str, script_language: str) -> tuple[str, str]:
    """建議這個風格＋劇本語言的組合該用哪種語言寫提示詞。

    回傳 (語言代碼, 理由)。

    這是建議不是強制，使用者可以覆寫。
    而且理由要講出來，讓他知道為什麼這樣建議。

    目前的規則是基於推論不是實測：
      anime 風格 → 日文（「間」「余白」「作画」這些概念日文精確得多）
      其他       → 跟劇本原文同語言（少一層翻譯就少一層失真）

    做完三語對照實驗後要回頭修正這些規則。
    """
    normalized = (script_language or "").lower()
    if normalized.startswith("zh"):
        script_lang = "zh"
    elif normalized.startswith("ja"):
        script_lang = "ja"
    else:
        script_lang = "en"

    if style_id == "anime":
        return (
            "ja",
            "日式動畫的「間」「余白」「作画」這些概念，日文比英文精確得多。",
        )

    return (
        script_lang,
        "跟劇本原文同語言，少一層翻譯就少一層失真。",
    )


def suggest_film_language(script_language: str, target_audience: str = "") -> tuple[str, str]:
    """建議成品要用哪種語言（角色台詞、字幕）。

    回傳 (語言代碼, 理由)。

    這跟提示詞語言是完全不同的決定。
    提示詞語言影響「畫面生得準不準」，影片語言影響「誰聽得懂」。

    預設跟劇本原文同語言：那是作者寫台詞時的語言，
    語氣、節奏、停頓都是照那個語言設計的，換語言會失去很多。

    但如果受眾在別的地方，那是完全正當的理由換語言。
    這是發行決定，不是技術決定，所以系統只提供資訊、不替使用者決定。
    """
    normalized = (script_language or "").lower()
    if normalized.startswith("zh"):
        default_lang = "zh"
    elif normalized.startswith("ja"):
        default_lang = "ja"
    else:
        default_lang = "en"

    if target_audience and target_audience != default_lang:
        return (
            target_audience,
            f"受眾主要在{_LANG_NAMES.get(target_audience, target_audience)}地區。"
            f"⚠️ 台詞換語言會失去原文的語氣與節奏 —— "
            f"另一個選擇是保留原文配音、只上目標語言字幕。",
        )

    return (
        default_lang,
        "跟劇本原文同語言。台詞的語氣、節奏、停頓都是照那個語言寫的。",
    )


_LANG_NAMES = {"zh": "華語", "en": "英語", "ja": "日語"}
