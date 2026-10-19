"""AI 生成的說明文字（製作人意見、選角說明、解析備註、段落摘要）跟著介面語言顯示。

這些字是模型生成時用某一種語言寫的（製作人審稿寫英文、劇本解析照劇本寫中文），介面切到別的語言時
就會夾雜。不改成「生成時寫三種」：那樣舊專案要全部重生。改成顯示時才翻：
  - 內容語言跟介面一樣 → 原文
  - 不一樣 → 用「翻譯字幕」那個角色（預設 Nemotron Nano）翻，結果存在資料夾的快取，同一句同一語言只翻一次
學語言的台詞、學習筆記不走這裡（那些本來就是多語內容）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading

from pydantic import BaseModel

from .env_boot import config_home

logger = logging.getLogger(__name__)

LANG_NAME = {"zh": "Traditional Chinese (Taiwan)", "en": "English", "ja": "Japanese"}
_lock = threading.Lock()


def detect(text: str) -> str:
    """粗分 zh／ja／en：英文字母遠多於漢字＋假名就是英文；其次有假名是日文；漢字比英文字母多是中文。

    英文先判：審稿意見常是英文句子裡引一句日文（「Typo/missing particle 'に' in '気にかけてる'…」），
    以前「有假名就算日文」→ 日文介面當它已經是日文、不翻，畫面上留一整句英文（實證）。"""
    kana = len(re.findall(r"[぀-ヿ]", text))
    han = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if latin > 2 * (kana + han):
        return "en"
    if kana:
        return "ja"
    return "zh" if han * 2 > latin else "en"


def _cache_path():
    d = config_home() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ui_translate.json"


def _load() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 沒有或壞了就當空的
        return {}


def _key(text: str, lang: str, names: str) -> str:
    return hashlib.sha1(f"{lang}\n{names}\n{text}".encode("utf-8")).hexdigest()


def name_hints(glossary_text: str, lang: str) -> str:
    """專案名字表（「月音=つきね/Tsukine」）→ 給翻譯的名字規則，連同中文名的拼音寫法
    （模型寫英文時常把月音拼成 Yueyin，翻的時候要一起換掉）。"""
    rows = []
    try:
        from pypinyin import lazy_pinyin
    except Exception:  # noqa: BLE001
        lazy_pinyin = None
    for ln in (glossary_text or "").splitlines():
        if "=" not in ln:
            continue
        name, rest = (x.strip() for x in ln.split("=", 1))
        reading, _, english = (x.strip() for x in rest.partition("/"))
        target = {"en": english or reading or name, "ja": name, "zh": name}[lang]
        # 🔴 這串字是翻譯快取的鍵的一部分（範例專案附了預先翻好的快取），改寫法＝快取全部失效
        aliases = [name] + ([english] if english else [])
        if lazy_pinyin and re.search(r"[一-鿿]", name):
            aliases.append("".join(lazy_pinyin(name)).capitalize())
        rows.append(f"{' / '.join(dict.fromkeys(aliases))} → {target}")
    return "\n".join(rows)


def fix_names(text: str, glossary_text: str, lang: str) -> str:
    """模型自己拼的名字（月音 → Yueyin）換成名字表的寫法：英文 Tsukine、中日文 月音。
    不靠翻譯：內容語言剛好跟介面一樣時根本不會經過翻譯，名字也要對。只換拉丁字母的拼法（整個字）。"""
    try:
        from pypinyin import lazy_pinyin
    except Exception:  # noqa: BLE001
        return text
    for ln in (glossary_text or "").splitlines():
        if "=" not in ln:
            continue
        name, rest = (x.strip() for x in ln.split("=", 1))
        reading, _, english = (x.strip() for x in rest.partition("/"))
        if not re.search(r"[一-鿿]", name):
            continue
        target = {"en": english or reading or name, "ja": name, "zh": name}[lang]
        pin = "".join(lazy_pinyin(name)).capitalize()
        if pin and pin != target:
            text = re.sub(rf"\b{re.escape(pin)}\b", target, text)
        # 留著假名讀音（つきね）＝沒換到名字表的寫法：中英文一定要換；日文介面也換回漢字名，
        #   跟畫面其他地方（標題、選角、台詞表）的「月音」一致（表演句日譯寫成つきね）
        if reading and reading != target and not re.search(r"[A-Za-z]", reading):
            text = text.replace(reading, target)
    return text


class _Out(BaseModel):
    items: list[str]


def translate(texts: list[str], lang: str, glossary_text: str = "") -> list[str]:
    """回傳與 texts 等長的譯文；已經是 lang 的、翻譯失敗的，照原文。"""
    if lang not in LANG_NAME:
        return texts
    names = name_hints(glossary_text, lang)
    with _lock:
        cache = _load()
    out = [fix_names(t, glossary_text, lang) for t in texts]   # 不用翻的也要把名字寫對
    todo = [(i, t) for i, t in enumerate(texts) if t and t.strip() and detect(t) != lang]
    need = []
    for i, t in todo:
        hit = cache.get(_key(t, lang, names))
        if hit is not None:
            out[i] = fix_names(hit, glossary_text, lang)
        else:
            need.append((i, t))
    if not need:
        return out
    done: list[tuple[int, str, str]] = []
    _translate_chunk(need, lang, names, done)
    if not done:
        return out
    with _lock:
        cache = _load()
        for i, t, g in done:
            g = fix_names((g or "").strip() or t, glossary_text, lang)
            out[i] = g
            cache[_key(t, lang, names)] = g
        _cache_path().write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return out


def _translate_chunk(items: list[tuple[int, str]], lang: str, names: str, done: list) -> None:
    """翻一批；模型回的句數對不上或格式壞了 → 拆兩半各自再試，拆到一句還不行就留原文（不寫快取，下次再翻）。
    （實測：一次送十幾句時 Nano 偶爾少回一句，整批退回原文；拆開就都翻得出來。）"""
    if not items:
        return
    try:
        got = _call([t for _, t in items], lang, names)
        if len(got) != len(items):
            raise ValueError(f"expected {len(items)} items, got {len(got)}")
        done.extend((i, t, g) for (i, t), g in zip(items, got))
    except Exception as exc:  # noqa: BLE001 - 翻不了就拆小再試，最後顯示原文，不擋畫面
        if len(items) == 1:
            logger.warning("ui translate failed for one item: %s", exc)
            return
        mid = len(items) // 2
        _translate_chunk(items[:mid], lang, names, done)
        _translate_chunk(items[mid:], lang, names, done)


def _call(texts: list[str], lang: str, names: str) -> list[str]:
    from google.genai import types

    from . import genai_client

    client = genai_client.client(genai_client.QUICK_MS, action="翻譯字幕")
    system = (f"Translate each item into {LANG_NAME[lang]}. These are short notes written by an AI film director "
              "about shots and characters. Keep the meaning and the tone; keep shot ids (s001_06), numbers and "
              "rule numbers exactly. Text quoted from the script or a prompt (inside 「」, \"\" or after a colon "
              "as an example) stays in its original language. Return exactly the same number of items, in order."
              + (f"\nCharacter names — always write them like this:\n{names}" if names else ""))
    cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                      response_schema=_Out, temperature=0.2)
    r = client.models.generate_content(model="", contents=json.dumps(texts, ensure_ascii=False), config=cfg)
    return (r.parsed or _Out.model_validate_json(r.text)).items
