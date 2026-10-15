"""導演的評審：分鏡過了所有關卡之後，Nemotron Ultra 用製作人的眼光看一遍，給具體意見讓寫的人改。

為什麼關卡之外還要評審：關卡只量得出「會壞」和一部分「會平」（景別、重複、拆句）。
量不出來的是 —— 情緒有沒有一路推上去、對手的反應合不合理、劇本要求的鏡頭意圖有沒有做到。
實證：Nemotron 導第 61 場，全部關卡都過了，但 Lisa 被月音當面質問時，每一鏡都在「輕輕點頭」；
月音講最傷人的那句時「眼神柔和」；劇本說「鏡子可以用」，22 鏡一次都沒用。

評審只給意見、不動手改：寫分鏡的人（Super）照意見改，改完照樣要過關卡。
意見要能落到某幾鏡、講得出「哪裡不對、該往哪裡走」，講不出來就不要提 —— 跟驗收同一個標準。
"""

import json
import re
import os
import pathlib

from . import director

ROOT = pathlib.Path(__file__).parent

ANSWER_SCHEMA = """Return ONLY a JSON object:
{"verdict": "approve" | "revise",
 "overall": "<one or two sentences on how the scene plays as a whole>",
 "notes": [{"shots": ["<shot_id>", ...], "quote": "<exact words copied from those shots' visual_prompt or dialogue — one phrase per shot, separated by ' / '>",
            "problem": "<what is wrong, concretely>", "direction": "<what to do instead>"}]}
Every note must quote text that really is in the shots it names; notes whose quote cannot be found are discarded.
A note about something missing (e.g. the mirror is never used) may leave quote empty and name no shots.
At most 6 notes, most important first. Approve when the scene works even if you would have shot it differently."""


def system_prompt() -> str:
    from ..services import knowledge   # 使用者知識庫那一份（安裝檔外面、隨時可改）
    rules = knowledge.read_text("directing_rules.md")
    return ("You are the producer reviewing a director's shot list for an anime scene before anything is rendered. "
            "The list already passed mechanical checks; your job is what they cannot measure:\n"
            "1. Does the emotion build beat by beat the way the scene's emotion_arc says? Name the beats that go flat or wrong.\n"
            "2. Are the other characters' reactions believable at that moment (someone being confronted does not keep nodding)?\n"
            "3. Is the scene's camera_intent actually used (e.g. a mirror, one line per shot, the listener's reactions between lines)?\n"
            "4. Rhythm: is the shot count and length right for the weight of the scene, or is it padded?\n"
            "5. Is anything the script makes a point of missing (a gag, a key gesture, an exit)?\n"
            "Your directions must stay within the directing rules below (body actions only, no mouth words, etc.). "
            "In particular never suggest lips, biting, mouth, breathing, sighing, gasping or crying sounds — "
            "the render model voices them. Use hands, shoulders, head, gaze, distance and posture instead.\n\n"
            f"{rules}\n\n{ANSWER_SCHEMA}")


def compact(shots: list) -> list:
    """給評審看的分鏡：只留導戲需要的欄位，省 token。"""
    keep = ("shot_id", "duration_seconds", "shot_type", "camera_move", "characters_in_frame", "blocking",
            "speaker", "voice_role", "dialogue", "dialogue_lines", "visual_prompt")
    return [{k: s[k] for k in keep if s.get(k)} for s in shots]


def critique(scene: dict, shots: list, call=None, model: str = None) -> dict:
    if not model:
        from ..services import settings_store
        model = settings_store.load()["roles"]["critic"]["model"]       # 設定頁的製作人（預設 Nemotron Ultra）
    call = call or (lambda msgs: director.call_token_factory(msgs, model))
    brief = {k: scene.get(k) for k in ("title", "logline_en", "blocking", "events", "dialogue", "emotion_arc", "camera_intent")}
    messages = [{"role": "system", "content": system_prompt()},
                {"role": "user", "content": "The scene:\n" + json.dumps(brief, ensure_ascii=False, indent=1)
                 + "\n\nThe director's shot list:\n" + json.dumps(compact(shots), ensure_ascii=False, indent=1)
                 # 角色要在最後再講一次：實測 Ultra 看完一份分鏡，直接「自己重導一遍」回了一份新分鏡
                 #（還編了劇本沒有的台詞），而不是給意見。評審只評、不寫。
                 + "\n\nYou are the reviewer, not the director: do NOT return a shot list and do not write new dialogue. "
                   'Reply with only your review: {"verdict": ..., "overall": ..., "notes": [...]}.'}]
    # 🔴 格式不對的評審「不能」當成通過。實測：第二次評審回了 1800 tokens 但欄位對不上，
    #    舊版把 verdict=None 當成「沒意見＝核准」—— 一個壞掉的檢查跟通過的檢查長得一模一樣。
    #    現在：格式不對就再問一次；再不對就標 "unreviewed"（不是 approve），原文留一段在紀錄裡查得到。
    usage, out, raw = {}, None, ""
    for _ in range(2):
        reply, usage = call(messages)
        raw = reply
        try:
            got = director.parse_json(reply)
            # 正常的寫法差異照收（"REVISE" 大寫、意見寫成一句話而不是物件）—— 內容是對的評審，不該因為大小寫被丟掉；
            # 但 verdict 不是 approve／revise 的，一律不算（那才是「沒評」）。
            v = str(got.get("verdict", "")).strip().lower()
            notes = got.get("notes", [])
            if v in ("approve", "revise") and isinstance(notes, list):
                got["verdict"] = v
                got["notes"] = [n if isinstance(n, dict) else {"shots": [], "problem": str(n), "direction": ""} for n in notes]
                out = got
                break
            problem = f"'verdict' must be \"approve\" or \"revise\" and 'notes' a list; got keys {sorted(got)}"
        except (ValueError, json.JSONDecodeError) as e:
            problem = f"could not parse your reply ({e})"
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": f"{problem}. Return only the JSON object in the exact shape asked for."}]
    if out is None:
        out = {"verdict": "unreviewed", "overall": "the producer's review came back malformed twice — not approved, needs a human look",
               "notes": [], "raw_excerpt": raw[:1500]}
    else:
        verify(out, shots)
    out["_meta"] = {"model": model, "usage": usage}
    return out


def verify(review: dict, shots: list):
    """評審的每條意見都要有證據：引用的文字真的在它點名的那幾鏡裡。找不到的意見丟掉、不交給導演。

    為什麼：實測 Ultra 評審說「生日那句在 12、17 鏡各講一次、退場那句在 19、20 鏡各講一次」，
    實際的分鏡每句都只講一次 —— 評審自己幻覺。把幻覺的意見交給導演，導演會去「修」根本不存在的問題，
    改壞本來是好的鏡。證據先於判決：講得出在哪一鏡、哪幾個字，才算數。
    沒點名鏡號、也沒引用的（「鏡子整場沒用到」這種講缺什麼的）照收 —— 缺的東西本來就引用不出來。
    丟掉的意見留在 dropped_notes 裡，網頁上看得到。
    """
    from . import gates

    by_id = {s.get("shot_id"): gates._norm((s.get("visual_prompt") or "") + (s.get("dialogue") or "")
                                           + "".join(l.get("text", "") for l in s.get("dialogue_lines") or []))
             for s in shots}
    keep, dropped = [], []
    for n in review.get("notes") or []:
        # 引用可以是一段，也可以是「每鏡各引一段、用 / 隔開」或清單 —— 雲端實測：評審照實逐鏡引用
        # 「最後Lisa點頭 / 最後Lisa輕輕點頭 / …」，舊版把整串當成一段去找，五條真的意見全被丟掉。
        raw_q = n.get("quote") or ""
        frags = [gates._norm(x) for x in (raw_q if isinstance(raw_q, list) else re.split(r"\s*[/／]\s*", str(raw_q)))]
        frags = [f for f in frags if f]
        q, ids = "".join(frags), n.get("shots") or []
        found = [i for i in ids if any(f in by_id.get(i, "") for f in frags)]
        if not ids and not q:
            keep.append(n)
        elif not found:
            dropped.append({**n, "why_dropped": "quoted text not found in the shots it names"})
        elif len(ids) >= 2 and len(found) < 2:
            # 意見說「這幾鏡都有這個問題」，卻只在一鏡找得到 —— 「重複」這件事本身是假的
            dropped.append({**n, "why_dropped": f"claims {len(ids)} shots but the quote is only in {found[0]}"})
        else:
            if len(found) < len(ids):
                n = {**n, "shots": found, "problem": n.get("problem", "") + f" [verified only in {', '.join(found)}]"}
            keep.append(n)
    review["notes"], review["dropped_notes"] = keep, dropped
    if review.get("verdict") == "revise" and not keep:
        review["verdict"] = "approve"
        review["overall"] = (review.get("overall") or "") + " [Every note failed verification — nothing actionable left.]"


def notes_text(c: dict) -> str:
    """把評審意見寫成交回給導演的一段話。"""
    lines = [f"- {', '.join(n.get('shots') or ['whole scene'])}: {n.get('problem')} → {n.get('direction')}" for n in c.get("notes") or []]
    return "The producer reviewed your shot list.\n" + (c.get("overall") or "") + "\n" + "\n".join(lines)
