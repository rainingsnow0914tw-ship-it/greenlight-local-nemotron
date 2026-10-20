"""查證診斷：一鏡沒過 → 對照症狀庫與成功過的同類鏡 → 真因＋修法。agent 只能引用紀錄，不能自己編原因。

為什麼這樣設計（作者影片管線一再踩過的坑）：
  - 「查不出原因就不要自己編一個」：模型每次都給得出一個像樣的理由，驗證一次卻要 8 分鐘 GPU。
    → 候選只能是症狀庫裡、而且跟這一鏡查證結果對得上的那幾條；回覆沒引用查證項目或症狀編號就丟掉。
  - 「壞了先找對照組」：同一段已經收下的鏡（同一個角色、同一種台詞行）就是最便宜的證據，一起給它看。
  - 「第一直覺通常是錯的」：症狀庫每條都寫了 wrong_guess（例如「換種子就好」），讓它不要再猜一次。
兩層：
  1. 程式對號入座（candidates）：查證沒過的項目、人點的觀察 → 症狀庫哪幾條。沒有 AI 也會有結果。
  2. Nemotron 判斷（設定頁「查證診斷」那一格）：從候選裡挑真因、寫出這一鏡具體怎麼改。
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any

from pydantic import BaseModel, Field

from ..services import config_loader, genai_client, rates

logger = logging.getLogger(__name__)
ACTION = "查證診斷"


def candidates(verify: dict, marks: list[str]) -> list[dict]:
    """查證結果＋人的觀察 → 對得上的症狀（附對上的證據）。"""
    failed = {c["id"]: c for c in (verify.get("checks") or []) if c["level"] in ("warn", "fail")}
    out = []
    for s in config_loader.symptoms():
        d = s.get("detect") or {}
        # ev 給模型看、拿來比對引用；ev_en／ev_ja 是同一條證據的英文版、日文版（畫面照介面語言顯示）
        ev, ev_en, ev_ja = [], [], []
        c = failed.get(d.get("check"))
        if c and c["level"] in (d.get("level") or []):
            ev.append(f"check:{c['id']}={c['value']}（{c['zh']}）")
            ev_en.append(f"check:{c['id']}={c['value']} ({c.get('en') or c['zh']})")
            ev_ja.append(f"check:{c['id']}={c['value']}（{c.get('ja') or c.get('en') or c['zh']}）")
        if d.get("human") and d["human"] in marks:
            ev.append(f"human:{d['human']}")
            ev_en.append(f"human:{HUMAN_EN.get(d['human'], d['human'])}")
            ev_ja.append(f"human:{HUMAN_JA.get(d['human'], d['human'])}")
        if ev:
            out.append({**s, "evidence": ev, "evidence_en": ev_en, "evidence_ja": ev_ja})
    return out


HUMAN_EN = {"嘴沒動": "mouth still", "嘴有動": "mouth moves", "動作重複": "action repeats", "語言不對": "wrong language"}
HUMAN_JA = {"嘴沒動": "口が動かない", "嘴有動": "口が動く", "動作重複": "動作の繰り返し", "語言不對": "言語が違う"}


def _tr(zh: str, en: str, ja: str = "") -> dict:
    """畫面上要顯示的字一律帶三種語言（英文、中文、日文介面各自看得懂）；缺日文才退回英文。
    日文混進中英文都沒有的外語字（「demasiados アクション」）就當沒有日文，退回英文、介面再翻。"""
    from ..services.ui_translate import _stray_latin
    if ja and _stray_latin(f"{zh} {en}", ja, "ja", ""):
        ja = ""
    return {"zh": zh, "en": en or zh, "ja": ja or en or zh}


class _Pick(BaseModel):
    symptom_id: str = Field(description="one of the candidate symptom ids")
    cause_index: int = Field(description="which cause of that symptom (0-based)")
    evidence: list[str] = Field(description="the exact evidence strings you rely on, copied from the candidates "
                                            "or from the control shots (e.g. 'check:head=1.2' or 'shot:s001_03')")
    reasoning: str = Field(description="2-3 sentences in Traditional Chinese: why this cause and not the wrong guess")
    reasoning_en: str = Field(default="", description="the same reasoning in English")
    reasoning_ja: str = Field(default="", description="the same reasoning in natural Japanese")
    fix_detail: str = Field(description="the concrete change for THIS shot in Traditional Chinese "
                                        "(e.g. the rewritten line or performance sentence); empty for trim_head / reroll")
    fix_detail_en: str = Field(default="", description="the same fix_detail in English; empty when fix_detail is empty")
    fix_detail_ja: str = Field(default="", description="the same fix_detail in Japanese; empty when fix_detail is empty")
    new_text: str = Field(default="", description="REQUIRED for edit_line / edit_performance: the full replacement "
                                                  "sentence, in the same language as the original; empty for other actions")


class _Diagnosis(BaseModel):
    picks: list[_Pick]


_SYSTEM = """You diagnose why one generated video shot failed its checks. You may ONLY choose among the candidate
symptoms given (they come from a library of causes proven in hundreds of real shots), and you must cite the exact
evidence strings you rely on. Each symptom lists the wrong first guess people usually make — do not make it.
Compare with the control shots (same scene, already approved): what does the failed shot have that they don't?
Return one pick for EVERY candidate that is a real problem in this shot — often two symptoms point at the same moment
and their fixes compete (example: gibberish before a late first word → "trim the head" is free but also cuts whatever
action happens before the line; "rewrite the performance so she speaks sooner, then regenerate" keeps the action but
costs a new render); the program shows the person what each one costs. For edit_performance / edit_line you MUST
write the full replacement sentence in new_text (same language as the original). Never invent a cause outside the library."""


def diagnose(shot_info: dict, prompt: str, verify: dict, marks: list[str], controls: list[dict]) -> dict:
    """回 {picks:[...], candidates:[ids], model, cost_usd, unreviewed?}。picks 每一筆都過了引用檢查。"""
    cands = candidates(verify, marks)
    facts = {"trim": verify.get("trim_head"), "seconds": verify.get("seconds")}   # 代價用量到的數字寫
    if not cands:
        return {"picks": [], "candidates": [], "note": "no symptom matches these checks"}
    brief = [{"id": s["id"], "title": s["title"]["en"], "evidence": s["evidence"], "wrong_guess": s.get("wrong_guess"),
              "causes": [{"cause": c["cause"], "fix": c["fix"], "example": c.get("example")} for c in s["causes"]]}
             for s in cands]
    user = (f"Shot: {json.dumps(shot_info, ensure_ascii=False)}\n\nPrompt sent to the video model:\n{prompt}\n\n"
            f"Check results: {json.dumps(verify.get('checks'), ensure_ascii=False)}\n"
            f"Human observations: {marks}\n\nCandidate symptoms:\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n\n"
            f"Control shots (approved in this scene):\n{json.dumps(controls, ensure_ascii=False, indent=1)}")
    valid_ev = {e for s in cands for e in s["evidence"]} | {f"shot:{c.get('shot_id')}" for c in controls}
    # 引用比對用「鍵」：check:head、human:嘴沒動 的「嘴沒動」、對照鏡號。模型抄的格式每次不一樣
    #    （實測同一題一次寫 check:head=0.66、一次只寫「嘴沒動」），鍵對到就算有引用，沒提到任何鍵才丟
    keys = {e.split("=")[0].split("（")[0] for e in valid_ev} | {e.split(":", 1)[1] for e in valid_ev if e.startswith("human:")}
    keys |= {str(c.get("shot_id")) for c in controls}
    try:
        from google.genai import types

        client = genai_client.client(genai_client.LONG_MS, action=ACTION)
        cfg = types.GenerateContentConfig(system_instruction=_SYSTEM, response_mime_type="application/json",
                                          response_schema=_Diagnosis, temperature=0.2)
        r = client.models.generate_content(model="", contents=user, config=cfg)
        meta = r.usage_metadata
        model = rates.model_used(meta, "")
        cost = rates.cost(model, meta.prompt_token_count, meta.candidates_token_count)
        dx = r.parsed or _Diagnosis.model_validate_json(r.text)
        # 改表演句／改台詞的選項沒附新句子 → 再問一次（s001_03：說了 MUST 它照樣空著）
        # 表演句只寫看得到的動作：台詞放在台詞那一行，寫進表演句會唸兩次（作者 h3_prompt 六行寫法；
        #    s001_03 實測模型寫「…撲水到臉上時，說出台詞「リサ、…」」）
        bad_perf = lambda p: _action_of(cands, p) == "edit_performance" and bool(re.search(r"[「」『』]|台詞|台词|說出|說：", p.new_text))  # noqa: E731
        need = [p for p in dx.picks if p.symptom_id in {s["id"] for s in cands}
                and _action_of(cands, p) in ("edit_performance", "edit_line") and (not p.new_text.strip() or bad_perf(p))]
        if need:
            follow = (user + "\n\nYour previous answer:\n" + dx.model_dump_json() + "\n\nFix new_text for: "
                      + ", ".join(p.symptom_id for p in need) + ". It must be the full replacement sentence. "
                      "A performance sentence describes only visible actions in order — never put the dialogue, quotes "
                      "or the word 'says' in it (the line is a separate prompt line; written twice it gets spoken twice). "
                      "To make her speak sooner, put fewer actions before the moment she speaks. Return the whole JSON again.")
            r2 = client.models.generate_content(model="", contents=follow, config=cfg)
            m2 = r2.usage_metadata
            cost += rates.cost(model, m2.prompt_token_count, m2.candidates_token_count)
            dx2 = r2.parsed or _Diagnosis.model_validate_json(r2.text)
            if dx2.picks:
                dx = dx2
    except Exception as exc:  # noqa: BLE001 - 診斷失敗就退回程式對號入座的結果，誠實標示沒經 AI 判斷
        logger.warning("doctor failed: %s", exc)
        return _fallback(cands, f"{type(exc).__name__}: {str(exc)[:200]}", facts)

    by_id = {s["id"]: s for s in cands}
    picks, dropped = [], 0
    for p in dx.picks:
        s = by_id.get(p.symptom_id)
        cited = [e for e in p.evidence if any(k and k in e for k in keys)]
        if not s or not (0 <= p.cause_index < len(s["causes"])) or not cited:
            dropped += 1          # 選了候選以外的、或沒引用任何證據 → 不收（跟製作人審稿同一招）
            logger.info("doctor dropped pick %s cause %s evidence %s", p.symptom_id, p.cause_index, p.evidence)
            continue
        if any(x["symptom_id"] == s["id"] for x in picks):
            continue
        picks.append(_option(s, p.cause_index, cited, facts, by="agent",
                             reasoning=_tr(p.reasoning, p.reasoning_en, p.reasoning_ja),
                             fix_detail=_tr(p.fix_detail, p.fix_detail_en, p.fix_detail_ja) if p.fix_detail else None,
                             new_text=p.new_text))
    if not picks:
        out = _fallback(cands, "the model's picks had no valid citation", facts)
        out.update(model=model, cost_usd=cost, dropped=dropped)
        return out
    # 程式對到、模型卻沒挑的症狀也列出來當另一個選項（s001_03：開頭亂語＋開口太晚同時成立，
    #    模型只給「剪頭」，可是剪頭會把走出隔間的動作一起剪掉 —— 另一條「改表演句重生」要讓人看得到、自己選）
    picks += [_option(s, 0, s["evidence"], facts, by="rule") for s in cands
              if not any(x["symptom_id"] == s["id"] for x in picks)]
    return {"picks": picks, "candidates": list(by_id), "model": model, "cost_usd": cost, "dropped": dropped}


NEEDS_REGEN = {"reroll", "edit_performance", "edit_line", "edit_voice_role", "lengthen_shot"}
RENDER = "本機約 8 分鐘"   # H3 四步一鏡 8 秒：實測 429～459 秒
RENDER_EN = "about 8 minutes locally"
RENDER_JA = "ローカルで約 8 分"


def _tradeoff(action: str, f: dict) -> dict:
    """每種修法的代價，用量到的數字寫，不讓模型寫（實測：模型寫「剪頭只移除約 0.1 秒」，實際 4.2 秒；
    寫「改表演句不用重新渲染」，實際要重生 —— 數字要量不要算）。中英日三份，數字同一份。"""
    trim, secs = float(f.get("trim") or 0), float(f.get("seconds") or 0)
    left = max(secs - trim, 0)
    if action == "trim_head":
        return _tr(f"不用重生；剪掉開頭 {trim:.1f} 秒（這一鏡 {secs:.1f} 秒 → 剩 {left:.1f} 秒），開口前的動作會一起剪掉",
                   f"No re-render; cuts the first {trim:.1f} s (this shot {secs:.1f} s → {left:.1f} s), "
                   "and any action before the line goes with it",
                   f"再生成なし。最初の {trim:.1f} 秒を切る（このカット {secs:.1f} 秒 → {left:.1f} 秒）。セリフ前の動作も一緒に切れる")
    if action == "edit_performance":
        return _tr(f"要重生一次（{RENDER}）；照新的表演句重演，動作會換掉",
                   f"One re-render ({RENDER_EN}); the shot is acted again from the new performance",
                   f"再生成 1 回（{RENDER_JA}）。新しい演技指示で演じ直すので、動作は変わる")
    if action == "lengthen_shot":
        return _tr(f"要重生一次（{RENDER}）；這一鏡加 1 秒", f"One re-render ({RENDER_EN}); the shot gets 1 s longer",
                   f"再生成 1 回（{RENDER_JA}）。このカットを 1 秒長くする")
    zh = {"reroll": f"提示詞不改，重生一次（{RENDER}），結果是隨機的",
          "edit_line": f"要重生一次（{RENDER}）；台詞換了，字幕跟著換",
          "edit_voice_role": f"要重生一次（{RENDER}）；只改台詞那一行的寫法",
          "human": "不用重生；請人看一眼再決定"}.get(action, "")
    en = {"reroll": f"Same prompt, one re-render ({RENDER_EN}); the result is random",
          "edit_line": f"One re-render ({RENDER_EN}); the line changes and so does the subtitle",
          "edit_voice_role": f"One re-render ({RENDER_EN}); only the dialogue line's wording changes",
          "human": "No re-render; a person takes a look and decides"}.get(action, "")
    ja = {"reroll": f"プロンプトはそのまま、再生成 1 回（{RENDER_JA}）。結果はランダム",
          "edit_line": f"再生成 1 回（{RENDER_JA}）。セリフが変わり、字幕も変わる",
          "edit_voice_role": f"再生成 1 回（{RENDER_JA}）。セリフ行の書き方だけを変える",
          "human": "再生成なし。人が確認してから決める"}.get(action, "")
    return _tr(zh, en, ja)


def _ev_text(s: dict, cited: list[str]) -> dict:
    """引用的證據照候選的標準寫法顯示（模型抄的格式每次不同），中英日各一份；對照鏡號三種語言一樣。"""
    out: dict[str, list[str]] = {"zh": [], "en": [], "ja": []}
    for e in cited:
        i = next((i for i, x in enumerate(s["evidence"]) if (x.split("=")[0].split("（")[0] in e
                                                             or (x.startswith("human:") and x.split(":", 1)[1] in e))), None)
        for lang, key in (("zh", "evidence"), ("en", "evidence_en"), ("ja", "evidence_ja")):
            out[lang].append(s.get(key, s["evidence"])[i] if i is not None else e)
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


def _option(s: dict, ci: int, evidence: list[str], facts: dict, *, by: str, reasoning: dict | None = None,
            fix_detail: dict | None = None, new_text: str = "") -> dict:
    c = s["causes"][ci]
    action = c["fix"]["action"]
    if action in ("trim_head", "reroll", "human", "lengthen_shot"):
        # 這幾種修法程式自己知道要做什麼（剪幾秒是量的、加長固定 +1 秒）；模型的補充說明會自己編數字
        #    （實測：「剪掉開頭約 0.1 秒」，實際 4.2 秒；「加長 0.3 秒」，apply_fix 實際加 1 秒）→ 不收
        fix_detail = None
    return {"symptom_id": s["id"], "title": s["title"],
            "cause": _tr(c["cause"], c.get("cause_en", ""), c.get("cause_ja", "")),
            "action": action, "fix": _tr(c["fix"]["zh"], c["fix"].get("en", ""), c["fix"].get("ja", "")),
            "fix_detail": fix_detail or {}, "new_text": new_text, "evidence": _ev_text(s, evidence),
            "reasoning": reasoning or {}, "example": c.get("example"), "source": c.get("source"),
            "source_en": c.get("source_en"), "source_ja": c.get("source_ja"), "by": by,
            "tradeoff": _tradeoff(action, facts), "regenerate": action in NEEDS_REGEN}


def _action_of(cands: list[dict], p) -> str:
    s = next((x for x in cands if x["id"] == p.symptom_id), None)
    return s["causes"][p.cause_index]["fix"]["action"] if s and 0 <= p.cause_index < len(s["causes"]) else ""


def _fallback(cands: list[dict], why: str, facts: dict) -> dict:
    """AI 沒判成：程式對號入座的每一個候選都列出來（各取第一個真因），標明「未經 AI 判斷」。"""
    return {"picks": [_option(s, 0, s["evidence"], facts, by="rule") for s in cands],
            "candidates": [x["id"] for x in cands], "unreviewed": why}


def controls_from(segment, shot_id: str) -> list[dict[str, Any]]:
    """對照組：同一段已經收下的鏡（同角色優先），給它們的台詞行與查證結果。"""
    from ..schemas.segment import ClipStatus

    shots = {s.shot_id: s for s in segment.shot_list.shots}
    me = shots.get(shot_id)
    out = []
    for c in segment.clips:
        if c.shot_id == shot_id or c.status != ClipStatus.APPROVED:
            continue
        s = shots.get(c.shot_id)
        if not s:
            continue
        out.append({"shot_id": c.shot_id, "speaker": s.speaker, "voice_role": s.voice_role,
                    "same_speaker": bool(me and s.speaker == me.speaker),
                    "checks": [{k: x[k] for k in ("id", "level", "value")} for x in (c.verify or {}).get("checks", [])]})
    out.sort(key=lambda x: not x["same_speaker"])
    return out[:4]
