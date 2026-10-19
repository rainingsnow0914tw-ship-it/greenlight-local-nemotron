"""規則 26 的結構解：把「你一鏡、我一鏡」的正反打合併成兩人同框。

為什麼用程式合、不叫模型寫：兩次實測，規則寫進說明 → 25 鏡 0 鏡同框；再加關卡 → Nemotron 每次試著寫同框，
就同時踩到規則 21（同框沒點名誰做什麼，一回合 32 處）、規則 6（dialogue 跟 dialogue_lines 對不上）、
規則 9（順手改了台詞），十回合在 7→49→63 處錯誤之間來回，最後放棄。
同框的格式是固定的（兩個人、面對面、台詞照順序一人一行、每個動作點名），格式固定的事交給程式；
模型負責它做得好的部分：每一鏡的表演和秒數。

安全網：每合一組就重跑全部關卡，ERROR 變多就退回不合。合不了的照舊是單人鏡，不會更糟。
"""

import math
import re

from . import gates

_SHE = re.compile(r"她")


def _named(prompt: str, name: str) -> str:
    """表演句裡的「她」換成名字（規則 21：同框時模型分不出是哪個她）；句尾句號拿掉，讓兩句接得起來。"""
    p = (prompt or "").strip().rstrip("。.")
    p = _SHE.sub(name, p)
    return p if p.startswith(name) else f"{name}{p}"


def _speaker(s: dict) -> str | None:
    return s.get("speaker") if s.get("dialogue") and (s.get("voice_role") or "character") == "character" else None


def _mergeable(a: dict, b: dict, chars: dict) -> bool:
    sa, sb = _speaker(a), _speaker(b)
    if not sa or not sb or sa == sb:
        return False
    if (a.get("characters_in_frame") or []) != [sa] or (b.get("characters_in_frame") or []) != [sb]:
        return False
    if a.get("dialogue_lines") or b.get("dialogue_lines"):
        return False
    if a.get("spot") != b.get("spot") or chars.get(sa, {}).get("location") != chars.get(sb, {}).get("location"):
        return False
    return gates.speech_seconds(a["dialogue"] + b["dialogue"]) + gates.ACTION_TAIL <= gates.MAX_SECONDS


def _merge(a: dict, b: dict, chars: dict) -> dict:
    sa, sb = _speaker(a), _speaker(b)
    na, nb = chars[sa].get("name_zh") or sa, chars[sb].get("name_zh") or sb
    need = gates.speech_seconds(a["dialogue"] + b["dialogue"]) + gates.ACTION_TAIL
    seconds = min(gates.MAX_SECONDS, max(math.ceil(need), a.get("duration_seconds") or 0))
    return {
        **a,
        "duration_seconds": seconds,
        "shot_type": "MEDIUM",
        "characters_in_frame": [sa, sb],
        "blocking": "face_to_face",
        "speaker": sa,
        "dialogue_lines": [{"speaker": sa, "text": a["dialogue"]}, {"speaker": sb, "text": b["dialogue"]}],
        "dialogue": a["dialogue"] + b["dialogue"],
        "visual_prompt": f"{_named(a.get('visual_prompt'), na)}，之後{_named(b.get('visual_prompt'), nb)}。",
        "visual_prompt_en": f"{a.get('visual_prompt_en') or ''} Then {b.get('visual_prompt_en') or ''}".strip(),
        "reasoning_en": f"two-shot: {na} and {nb} trade lines face to face (merged {a.get('shot_id')}+{b.get('shot_id')}, rule 26)",
    }


def _errors(scene: dict, shots: list) -> int:
    return sum(1 for i in gates.check(scene, shots) if i["level"] == "ERROR")


# 聽的那一方的身體動作，輪流用（同一句抄三鏡會被規則 23 擋，也真的會很呆）。只寫身體、不寫嘴（規則 3）。
_LISTEN = ["站在對面看著{n}", "雙手抱胸看著{n}", "手扶著洗手盆邊緣看著{n}", "往後退半步看著{n}",
           "低頭一下又抬眼看著{n}", "別開臉又轉回來看著{n}", "雙手垂在身側看著{n}", "肩膀微微縮起看著{n}"]


def _add_listener(s: dict, listener: str, chars: dict, k: int = 0) -> dict:
    """單人鏡補上在場的另一個人（聽的那一方）。不寫嘴、不寫聲音（規則 3），兩個人都點名（規則 21）。"""
    me = (s.get("characters_in_frame") or [None])[0]
    nm, nl = chars[me].get("name_zh") or me, chars[listener].get("name_zh") or listener
    return {
        **s,
        "characters_in_frame": [me, listener],
        "blocking": s.get("blocking") or "face_to_face",
        "visual_prompt": f"{_named(s.get('visual_prompt'), nm)}，同時{nl}{_LISTEN[k % len(_LISTEN)].format(n=nm)}。",
        "reasoning_en": (s.get("reasoning_en") or "") + f" (two-shot: {nl} stays in frame, rule 26)",
    }


def keep_both_in_frame(scene: dict, shots: list) -> tuple[list, list[str]]:
    """一鏡到底：兩個人都在同一個地點、其中一個已經出現過之後，剩下的單人鏡都補上另一個人。
    還沒登場的不補（例：月音還在隔間裡的第一鏡）。一樣每改一鏡重跑關卡，變壞就不改。"""
    chars = scene.get("characters") or {}
    out, changed, seen = list(shots), [], set()
    base = _errors(scene, out)
    for i, s in enumerate(out):
        frame = s.get("characters_in_frame") or []
        if len(frame) == 1:
            me = frame[0]
            others = [c for c in seen if c != me and chars.get(c, {}).get("location") == chars.get(me, {}).get("location")]
            if others:
                trial = out[:i] + [_add_listener(s, others[0], chars, len(changed))] + out[i + 1:]
                errs = _errors(scene, trial)
                if errs <= base:
                    out, base = trial, errs
                    changed.append(s.get("shot_id"))
        seen.update(out[i].get("characters_in_frame") or [])
        # 這一鏡有人離開（走出去、離開畫面）→ 之後不再把她補回來當聽的人（月音在 s001_17 走掉，s001_18 又被補回來）
        prompt = out[i].get("visual_prompt") or ""
        for c in list(seen):
            nm = chars.get(c, {}).get("name_zh") or c
            if re.search(rf"{re.escape(nm)}[^，。]*?(離開|走出去|走了出去|離去|出了門)", prompt):
                seen.discard(c)
    return out, changed


def merge(scene: dict, shots: list) -> tuple[list, list[str]]:
    """回傳（合併後的鏡頭清單, 合了哪幾組 ["s001_03+s001_04", ...]）。原清單不動。"""
    chars = scene.get("characters") or {}
    out = list(shots)
    merged = []
    base = _errors(scene, out)
    i = 0
    while i < len(out) - 1:
        a, b = out[i], out[i + 1]
        if _mergeable(a, b, chars):
            trial = out[:i] + [_merge(a, b, chars)] + out[i + 2:]
            errs = _errors(scene, trial)
            if errs <= base:
                out, base = trial, errs
                merged.append(f"{a.get('shot_id')}+{b.get('shot_id')}")
        i += 1
    return out, merged
