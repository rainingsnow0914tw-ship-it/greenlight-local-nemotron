"""台詞導演（Nemotron）：劇本台詞 → 這個角色在這一刻、對這個人，用原聲語言真的會怎麼講。

為什麼不是翻譯：機翻一句一句翻，不知道誰在跟誰講話。作者做《第三世》日文動畫時一再修的就是這個 ——
小說日文版照抄進台詞會太像書面語、譯錯原意（「道具にするときしか遊んでくれない」，
原意是「其他人類只會把你當工具，只有我會陪你玩」）。這些機翻都抓不到，導演才抓得到：
  - 兩個人熟不熟、誰地位高 → 口語還是敬語（越熟越口語；評議會這種身份角色保留正式說法）
  - 作者原意 > 字面（原文版和譯本不一樣時以原文版的意思為準）
  - 這一鏡幾秒、唸不唸得完
  - 字幕不是把台詞再翻一次，是讓看不懂的人一眼懂「這句話的語氣」

一句台詞回來的東西：ruby（原聲語言＋讀音標記）、register（語氣）、why（為什麼這樣說，給學習者看）、subs（各語字幕）。
每一版都先過 gates.check，擋下的講給它聽、叫它改，最多三版；挑問題最少的那一版。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from pydantic import AliasChoices, BaseModel, Field

from ..services import genai_client, rates
from . import gates

logger = logging.getLogger(__name__)
ACTION = "台詞導演"     # settings_store.ROLES → 設定頁「台詞導演」那一格
MAX_ROUNDS = 3
CHUNK = 40              # 一次最多送幾句（一場戲通常 10～30 句）



class _Line(BaseModel):
    id: str = Field(description="the line id exactly as given (L1, L2, …)")
    source: str = Field(description="the script line of THIS id, copied exactly as given (proves the lines did not shift)")
    # 用字串不用 Literal：模型偶爾回 "neutral" 之類，整份回覆就被當成格式壞掉丟掉（實測連兩次）。
    # 值合不合法交給 gates（擋下講給它聽），不要讓格式檢查吃掉一整版。
    speech_level: str = Field(description="one of: intimate, casual, polite, formal, honorific, narration")
    # 先寫純文字再加讀音（幕後第 1 集：模型一個字一個字標讀音時順手把字打錯 —— 選[え]らぶ、登録[と]録[ろく]、
    #    仕[し]上[あげ]る —— 字典也認不出來是錯字；有了純文字版，程式比一下就知道哪裡被改了）
    spoken: str = Field(default="", description="the rewritten line in the film language as plain text, no readings. Write this FIRST.")
    ruby: str = Field(description="the SAME line as `spoken`, word for word, with readings added: Japanese 漢字[よみ] (hiragana), "
                                  "Chinese 字[pīnyīn] one character at a time. English/Korean: plain text. "
                                  "Removing the brackets must give back `spoken` exactly.")
    why: str = Field(description="ONE short sentence for the learner, in the explanation language: why this register / "
                                 "this wording, as a teacher would say it (who is speaking to whom, how close they are)")
    sub_zh: str | None = Field(default=None, description="Traditional Chinese (Taiwan) subtitle, if requested")
    sub_en: str | None = Field(default=None, description="English subtitle, if requested")
    sub_ja: str | None = Field(default=None, description="Japanese subtitle, if requested")
    sub_ko: str | None = Field(default=None, description="Korean subtitle, if requested")


class _Note(BaseModel):
    # 第三世試跑：輸入的台詞欄位叫 id，模型的筆記也跟著寫 id → 三次都被擋、整批 133 句全丟。兩個名字都收
    line_id: str = Field(validation_alias=AliasChoices("line_id", "id"), description="which line this note is about")
    point: str = Field(description="the learning point as a short title, in the explanation language")
    explain: str = Field(description="2-3 sentences in the explanation language: why it is said this way here, "
                                     "and how it would change for a different listener (a stranger, a superior)")
    textbook: str = Field(description="how a textbook would write the same sentence, in the film language")
    examples: list[str] = Field(description="2 everyday example sentences in the film language using the same pattern, "
                                            "each followed by ' — ' and its meaning in the explanation language")


class _Reply(BaseModel):  # noqa: D101
    lines: list[_Line]
    notes: list[_Note] = Field(default_factory=list, description="only in the first chunk: the 3 most useful learning points")


_SYSTEM = """You are the dialogue director of a language-learning film. The film is SPOKEN in {film}.
Viewers are learners whose own language is {learners}; they will read your line with its reading on top
and a subtitle in their language below. You are NOT a translator. For every line, write what THIS character
would actually say, out loud, in {film}, at THIS moment, to THIS listener.

How to decide each line:
1. Who is speaking to whom, how close they are, who has the higher status, what they feel right now.
   The closer they are, the more casual. Private talk between people who know each other uses natural spoken
   contractions{contractions}. Formal or honorific speech only where the speaker truly talks that way here:
   rank, ceremony, a stranger, or someone performing formality on purpose.
   A line spoken TO THE VIEWERS (to the camera: greeting the audience, asking them to like, subscribe or comment,
   "everyone") is a host talking to their own audience: friendly and casual like a YouTuber (〜てね, 〜よ, 〜しよう),
   not です／ください customer-service politeness.
   Technical words and slang get the word people really use in the film language, never a literal rendering of the
   characters (Chinese 翻車 "a fail" → 失敗, not 事故; 提示詞 → プロンプト). Use the glossary when it gives one.
2. Keep the author's meaning, not the surface words. If an "original" text is given next to the line, its meaning
   wins over the script line (the script may be a mistranslation).
3. It must be speakable within the shot: stay at or under the given "max_units" (characters for Japanese, Chinese
   and Korean; letters for English). Shorten, but never lose the meaning.
4. Narration (voice_role narrator) is narration: register "narration", clear and calm.
5. Subtitles (only the languages asked for): natural, short enough to read at a glance, and carrying the TONE
   of the spoken line (a casual line gets a casual subtitle). Never a word-for-word gloss.
6. "why": one sentence for the learner, WRITTEN IN {explain}, like a teacher pointing at the screen: who is
   talking to whom and why that makes this wording right. Never mention earlier versions, the producer or the checks.
7. Each id rewrites ITS OWN line only — never merge two lines, split one, or move words between ids; copy the
   script line into "source". Match each speaker's gender, age and personality (see the cast list): a woman
   does not say 俺／お前 unless the cast list says she talks like that.
8. Names are names: keep every character name as written (Japanese: 漢字 names keep their kanji with a reading;
   Latin names become katakana). Never translate what a name means. In English subtitles, write a name the way
   it sounds in the spoken film (月音 said つきね → Tsukine), not its Chinese pinyin.
{ruby_rule}
Return every line id exactly once. In the first chunk also return "notes": the 3 lines that teach the most
about how people really talk — register switches, contractions, sentence endings, word choices that textbooks
teach differently — with the explanation in {explain}. Notes are about the LANGUAGE, never about the reading
markup or how to write brackets."""

_RUBY_JA = """9. Readings: write EVERY kanji run as 漢字[よみ] in hiragana, e.g. 覚[おぼ]えてる, 一緒[いっしょ]に.
   The bracket follows the kanji run directly. Katakana names may stay bare. Do not put readings on kana."""

_RUBY_ZH = """9. Readings: write EVERY Chinese character with its own pinyin with tone marks, one character at a time:
   銀[yín]行[háng]。 Choose the reading this word has HERE (多音字 by context). Punctuation stays bare."""

_CONTRACTIONS = {
    "ja": " (〜てる, 〜じゃない, 〜ちゃう, 〜なきゃ, 〜って, 〜んだ, けど, じゃあ, なんで)",
    "en": " (I'm, don't, gonna, wanna — as people really speak)",
    "zh": " (Taiwanese everyday Mandarin: 啦, 喔, 欸, 好啦, 幹嘛)",
    "ko": " (반말 between close friends: 해, 했어, 뭐 해?)",
}


def _system(film: str, learners: list[str]) -> str:
    name = lambda c: gates.LANG_NAME.get(c, c)  # noqa: E731
    return _SYSTEM.format(film=name(film), learners=", ".join(name(x) for x in learners),
                          explain=name(learners[0]), contractions=_CONTRACTIONS.get(film, ""),
                          ruby_rule={"ja": _RUBY_JA, "zh": _RUBY_ZH}.get(film, ""))


class _Flag(BaseModel):
    id: str = Field(description="the line id")
    quote: str = Field(description="the exact words in the spoken line or its subtitle that are wrong (copied verbatim)")
    problem: str = Field(description="what is wrong: meaning changed from the script line, unnatural, wrong person/gender, "
                                     "a name translated, subtitle says something else than the spoken line")
    fix: str = Field(description="how to fix it, in one sentence")


class _Review(BaseModel):
    flags: list[_Flag] = Field(default_factory=list, description="only real problems; an empty list if the lines are fine")


_REVIEW_SYSTEM = """You are the producer reviewing a dialogue director's work for a language-learning film spoken in {film}.
Learners will memorise these lines, so a wrong line teaches them something wrong. For each line compare the
script line (source) with the rewritten spoken line and its subtitles, and flag ONLY real problems:
- the meaning changed (something added, lost, reversed; who does what to whom is different)
- a native speaker would not say it this way in this situation, or the wrong person/gender voice
- a character name was translated or changed
- a subtitle says something the spoken line does not
Do not flag style preferences, register choices that fit the relationship, or shortened lines that keep the meaning.
Quote the wrong words exactly. You review; you do not rewrite the lines."""


def review(chunk: list[dict], got: list[dict], context: str, film: str) -> tuple[list[dict], dict]:
    """製作人審意思：規則擋不住的錯（實測：「我知道妳在裡面」寫成「あんたの中にいる」＝在妳身體裡）。
    引用的字不在那一句裡的意見丟掉（跟分鏡製作人同一招：沒根據的意見不收）。"""
    by_id = {r.get("id"): r for r in got}
    rows = [{"id": ln["id"], "speaker": ln.get("speaker"), "listeners": ln.get("listeners"), "source": ln.get("source"),
             "spoken": gates.strip_ruby(by_id.get(ln["id"], {}).get("ruby", "")),
             "subtitles": by_id.get(ln["id"], {}).get("subs", {})} for ln in chunk]
    prompt = f"Story context:\n{context}\n\nLines to review (JSON):\n{json.dumps(rows, ensure_ascii=False, indent=1)}"
    rev, usage = _call(prompt, _REVIEW_SYSTEM.format(film=gates.LANG_NAME.get(film, film)), schema=_Review,
                       action="台詞審稿", temperature=0.2)
    kept = []
    for f in rev.flags:
        row = next((r for r in rows if r["id"] == f.id), None)
        hay = (row["spoken"] + " " + " ".join(row["subtitles"].values())) if row else ""
        if row and f.quote and f.quote.strip() in hay:
            kept.append(f.model_dump())
    usage["dropped"] = len(rev.flags) - len(kept)
    return kept, usage


def _call(prompt: str, system: str, schema=None, action: str = ACTION, temperature: float = 0.4) -> tuple[Any, dict]:
    from google.genai import types

    schema = schema or _Reply
    client = genai_client.client(genai_client.LONG_MS, action=action)
    config = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                         response_schema=schema, temperature=temperature)
    usage_in = usage_out = 0
    model = ""
    reply = None
    last_err = ""
    for _ in range(3):   # 回覆格式壞了就講給它聽再問一次（跟場面調度同一招）
        response = client.models.generate_content(model="", contents=prompt, config=config)
        meta = response.usage_metadata
        model = rates.model_used(meta, model)
        usage_in += meta.prompt_token_count
        usage_out += meta.candidates_token_count
        try:
            reply = response.parsed or schema.model_validate_json(response.text)
            break
        except ValueError as exc:
            # 學習筆記是附加的：只有筆記壞了就不要筆記，台詞照收（不為了三條筆記丟掉整批台詞）
            if schema is _Reply and "notes." in str(exc) and "lines." not in str(exc):
                try:
                    data = json.loads(response.text)
                    data["notes"] = []
                    reply = schema.model_validate(data)
                    logger.warning("line director: notes malformed, kept lines without notes")
                    break
                except (ValueError, TypeError):
                    pass
            last_err = " / ".join(str(exc).splitlines()[:4])[:300]
            logger.warning("line director bad reply: %s | tail: %s", last_err, (response.text or "")[-300:])
            # Ultra 實測：第一次回一篇散文分析、第二次把輸入的台詞 JSON 原樣抄回來 → 要把「該交什麼欄位」講白
            prompt += (f"\n\nYour previous reply was not valid ({last_err}). Do not write prose and do not copy the input back. "
                       "Return ONLY this JSON object, every field filled:\n"
                       + json.dumps(_skeleton(schema), ensure_ascii=False))
    if reply is None:
        raise ValueError(f"reply was not valid 3 times: {last_err}")
    return reply, {"model": model, "input_tokens": usage_in, "output_tokens": usage_out,
                   "cost_usd": rates.cost(model, usage_in, usage_out)}


def _skeleton(schema) -> dict:
    """回覆格式的樣板（給格式壞掉時看：欄位名＋說明）。"""
    if schema is _Review:
        return {"flags": [{k: f.description for k, f in _Flag.model_fields.items()}]}
    return {"lines": [{k: f.description for k, f in _Line.model_fields.items()}],
            "notes": [{k: f.description for k, f in _Note.model_fields.items()}]}


def _rekey(chunk: list[dict], got: list[dict]) -> list[dict]:
    """模型把句子錯位了（id 跟抄回來的原句對不上）→ 照原句把 id 改回它真正改寫的那一句。
    ：審稿後重寫那一版 23 句全部錯位，導致審稿抓到的 5 個錯字一個都換不上去。
    只在原句唯一、而且那個 id 沒被別句正確佔用時才改；對不上的留著，給 align 關卡擋。"""
    by_src: dict[str, list[str]] = {}
    for ln in chunk:
        by_src.setdefault((ln.get("source") or "").strip(), []).append(ln["id"])
    own = {ln["id"]: (ln.get("source") or "").strip() for ln in chunk}
    right = {r.get("id") for r in got if own.get(r.get("id")) == (r.get("source") or "").strip()}
    for r in got:
        src = (r.get("source") or "").strip()
        if own.get(r.get("id")) == src:
            continue
        ids = by_src.get(src, [])
        if len(ids) == 1 and ids[0] not in right:
            r["id"] = ids[0]
            right.add(ids[0])
    return got


def _as_dict(r: _Line) -> dict:
    d = r.model_dump()
    d["register"] = d.pop("speech_level")   # pydantic 不准叫 register（跟 BaseModel 撞名），出來再改回來
    d["subs"] = {k[4:]: v for k, v in d.items() if k.startswith("sub_") and v}
    for k in [k for k in d if k.startswith("sub_")]:
        d.pop(k)
    return d


def direct(lines: list[dict], context: str, film: str, learners: list[str],
           on_event: Callable[[dict], None] | None = None, people: dict[str, str] | None = None,
           en_names: dict[str, str] | None = None,
           name_say: dict[str, str] | None = None) -> tuple[list[dict], list[dict], dict]:
    """導一批台詞。lines＝[{id, shot_id, speaker, listeners, voice_role, seconds, max_units, source, original?, scene}]。

    Returns: (每句結果, 學習筆記, 用量)。結果每句加上 spoken（要唸的台詞）、source（原句）、issues（沒改掉的問題）。
    """
    emit = on_event or (lambda e: None)
    system = _system(film, learners)
    total = {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model": ""}
    results: dict[str, dict] = {}
    notes: list[dict] = []
    chunks = [lines[i:i + CHUNK] for i in range(0, len(lines), CHUNK)]
    for ci, chunk in enumerate(chunks):
        payload = [{k: v for k, v in ln.items() if k not in ("shot_id", "k")} for ln in chunk]
        base = (f"Subtitle languages wanted: {', '.join(learners)}. Explanation language: {learners[0]}.\n\n"
                f"Story context:\n{context}\n\nLines (JSON):\n{json.dumps(payload, ensure_ascii=False, indent=1)}")
        if ci > 0:
            base += "\n\nThis is a later chunk: return an empty notes list."
        prompt = base
        best: tuple[int, list[dict], list[dict], list[dict]] | None = None
        good_notes: list[dict] = []   # 任一版交出的合格筆記就留著（最後選中的那版沒附筆記 → 整段 0 條）

        def keep_notes(reply):
            nonlocal good_notes
            cand = [n.model_dump() for n in reply.notes]
            cand = [n for n in cand if not _about_markup(n) and gates.in_language(n["point"] + n["explain"], learners[0])]
            if cand and not good_notes:
                good_notes = cand

        for rnd in range(1, MAX_ROUNDS + 1):
            emit({"kind": "round_start", "chunk": ci + 1, "of": len(chunks), "round": rnd})
            reply, usage = _call(prompt, system)
            for k in ("cost_usd", "input_tokens", "output_tokens"):
                total[k] += usage[k]
            total["model"] = usage["model"] or total["model"]
            got = _rekey(chunk, [_as_dict(r) for r in reply.lines])
            if film == "ja":   # 漏標的讀音字典補（模型三版都補不齊的實測），補了哪些記下來給人看
                for r in got:
                    r["ruby"], r["filled"] = gates.fill_ruby(gates.ja_punct(r.get("ruby", "")))
            issues = gates.check(chunk, got, film, learners, people, en_names, name_say)
            if ci == 0:
                keep_notes(reply)
                if not good_notes:   # 還沒有一份合格的筆記 → 筆記的問題也講給它聽
                    issues += gates.check_notes([n.model_dump() for n in reply.notes], learners[0]) or [
                        {"id": "notes", "level": "ERROR", "rule": "explain", "zh": "沒有學習筆記",
                         "msg": f"return 3 study notes in {gates.LANG_NAME.get(learners[0], learners[0])}"}]
            errors = [i for i in issues if i["level"] == "ERROR"]
            emit({"kind": "round", "chunk": ci + 1, "of": len(chunks), "round": rnd, "lines": len(got),
                  "errors": len(errors), "warnings": len(issues) - len(errors), "cost": usage["cost_usd"],
                  "top": _top(errors)})
            if best is None or len(errors) < best[0]:
                best = (len(errors), got, [n.model_dump() for n in reply.notes], issues)
            if not errors:
                break
            prompt = (base + "\n\nYour previous version (JSON):\n"
                      + json.dumps({"lines": got}, ensure_ascii=False)
                      + "\n\nThe checks rejected these — fix exactly these, keep everything else:\n"
                      + "\n".join(f"- {i['id']}: {i['msg']}" for i in errors))
        assert best is not None
        # 製作人審意思（Ultra）：有問題就照意見再改一版；改完關卡反而變多就不收、留原版
        try:
            flags, rusage = review(chunk, best[1], context, film)
            total["cost_usd"] += rusage["cost_usd"]
            emit({"kind": "review", "chunk": ci + 1, "flags": [{k: f[k] for k in ("id", "quote", "problem")} for f in flags][:12],
                  "dropped": rusage.get("dropped", 0), "model": rusage["model"], "cost": rusage["cost_usd"]})
        except Exception as exc:  # noqa: BLE001 - 審稿失敗不擋交件，但要寫進紀錄（不是當成通過）
            logger.warning("line review failed: %s", exc)
            flags = []
            emit({"kind": "review", "chunk": ci + 1, "flags": [], "failed": f"{type(exc).__name__}: {str(exc)[:200]}"})
        # 字典跟模型讀音不一樣的地方也一起請它再確認一次（字典不懂上下文，所以是「確認」不是「照改」）
        doubts = [i for i in best[3] if i["rule"] == "reading"]
        if flags or doubts:
            emit({"kind": "round_start", "chunk": ci + 1, "of": len(chunks), "round": MAX_ROUNDS + 1})
            prompt = (base + "\n\nYour previous version (JSON):\n" + json.dumps({"lines": best[1]}, ensure_ascii=False)
                      + "\n\nFix exactly these, keep every other line as it is:\n"
                      + "\n".join(f"- {f['id']}: the producer says \"{f['quote']}\" — {f['problem']} → {f['fix']}" for f in flags)
                      + "".join(f"\n- {d['id']}: a dictionary reads it differently — {d['msg']}. Keep yours only if it is "
                                f"right in this sentence." for d in doubts))
            reply, usage = _call(prompt, system)
            total["cost_usd"] += usage["cost_usd"]
            got = _rekey(chunk, [_as_dict(r) for r in reply.lines])
            if film == "ja":
                for r in got:
                    r["ruby"], r["filled"] = gates.fill_ruby(gates.ja_punct(r.get("ruby", "")))
            issues = gates.check(chunk, got, film, learners, people, en_names, name_say)
            if ci == 0:
                keep_notes(reply)
            errors = [i for i in issues if i["level"] == "ERROR"]
            if len(errors) <= best[0]:
                best = (len(errors), got, best[2] or [n.model_dump() for n in reply.notes], issues)
                merged = None
            else:
                # 整版變差也不要整版丟：被點名的那幾句，新版本身沒錯就換上去（L9 意思錯被抓到、改對了，
                #    卻因為別句多了錯整版被退，L9 就留著錯的）
                bad_ids = {i["id"] for i in errors}
                new_by_id = {r.get("id"): r for r in got}
                merged = [new_by_id[r["id"]] if r.get("id") in {f["id"] for f in flags} | {d["id"] for d in doubts}
                          and r.get("id") in new_by_id and r.get("id") not in bad_ids else r for r in best[1]]
                m_issues = gates.check(chunk, merged, film, learners, people, en_names, name_say)
                m_errors = [i for i in m_issues if i["level"] == "ERROR"]
                if len(m_errors) <= best[0]:
                    best = (len(m_errors), merged, best[2], m_issues)
                else:
                    merged = None
            emit({"kind": "round", "chunk": ci + 1, "of": len(chunks), "round": MAX_ROUNDS + 1, "lines": len(got),
                  "errors": len(errors), "warnings": len(issues) - len(errors), "cost": usage["cost_usd"],
                  "top": _top(errors), "after_review": True,
                  "rejected": len(errors) > best[0] and merged is None, "merged": merged is not None})
        _, got, _, issues = best
        notes += good_notes
        by_id = {ln["id"]: ln for ln in chunk}
        for r in got:
            src = by_id.get(r.get("id"))
            if not src:
                continue
            ruby = r.get("ruby", "")
            plain = gates.ja_punct((r.get("spoken") or "").strip()) if film == "ja" else ""
            # 讀音的錯到最後還在（標在假名上、兩個詞併一起標、吃掉送假名）也走同一條：範例 3 L23「登録忘[わす]れずに」
            #   關卡抓到了、模型改了三輪沒改好，就這樣交出去
            bad_reading = film == "ja" and any(i["id"] == r["id"] and i["level"] == "ERROR"
                                               and i["rule"] in ("ruby", "okurigana") for i in issues)
            if film == "ja" and bad_reading and not plain:
                plain = gates.ja_punct(gates.strip_ruby(ruby).strip())
            if plain and (gates.strip_ruby(ruby).strip() != plain or bad_reading):
                # 改了幾輪讀音版還是把字改掉 → 信純文字版，讀音整句用字典重標（字典讀音錯的少、錯字錯得多）
                ruby, refilled = gates.fill_ruby(plain)
                # 這一句的問題照換過的版本重查一次（舊的「把字改掉了」「讀音可能錯」講的是丟掉的那版，留著會讓人以為沒修好）
                fixed = {**r, "ruby": ruby, "filled": refilled}
                #    只重查跟讀音有關的（秒數那類是整鏡加總算的，單句重查會算錯）
                about = {"ruby_copy", "ruby", "reading", "okurigana"}
                issues = [i for i in issues if not (i["id"] == r["id"] and i["rule"] in about)] + [
                    i for i in gates.check([src], [fixed], film, learners, people, en_names, name_say)
                    if i["id"] == r["id"] and i["rule"] in about]
                r = fixed
            spoken = gates.strip_ruby(ruby)
            results[r["id"]] = {
                **{k: src.get(k) for k in ("id", "shot_id", "k", "speaker", "voice_role", "seconds", "source", "original")},
                "ruby": ruby, "spoken": spoken, "register": r.get("register"), "why": r.get("why", ""),
                "subs": r.get("subs") or {}, "hints": gates.hints(spoken, film), "filled": r.get("filled") or [],
                "issues": [{"level": i["level"], "rule": i["rule"], "zh": i["zh"]} for i in issues if i["id"] == r["id"]],
            }
    ordered = [results[ln["id"]] for ln in lines if ln["id"] in results]
    return ordered, notes, total


def _about_markup(note: dict) -> bool:
    """學習筆記要講語言，不是講讀音標記（實測兩次都有一條「漢字要標讀音」）。"""
    return bool(re.search(r"讀音|假名標|標註|ふりがな|振り仮名|ruby|reading|bracket|標記",
                          (note.get("point") or "") + (note.get("explain") or "")))


def _top(errors: list[dict]) -> list[dict[str, Any]]:
    from collections import Counter

    return [{"rule": r, "count": n} for r, n in Counter(e["rule"] for e in errors).most_common(3)]
