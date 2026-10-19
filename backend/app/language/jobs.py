"""台詞導演的背景工作：一個段落的所有台詞 → 台詞導演 → 寫回分鏡表。一路寫紀錄給畫面看（跟導演分鏡同一套）。

為什麼寫回分鏡表：H3 唸的是 shot.dialogue／dialogue_lines。學語言模式下要唸的是導過的原聲台詞，
不是劇本原句。原句一定留在 segment.lines[*].source —— 重導台詞時從原句重來，
不能拿已經改過的日文再改一次（越改越偏）。

寫回之後清掉這一段的字幕快取與學習字幕成片：台詞變了，舊的全部對不上。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from ..services import project_store
from . import gates, line_director

logger = logging.getLogger(__name__)
_running: dict[tuple[str, int], threading.Thread] = {}
_lock = threading.Lock()
CONTEXT_CHARS = 2500     # 每場戲給模型看多少原文（判斷誰跟誰講話、熟不熟）


def _seg(state, order):
    return next(s for s in state.segments if s.order == order)


def _log(project_id: str, order: int, kind: str, **data: Any) -> None:
    entry = {"t": round(time.time(), 1), "kind": kind, **data}
    project_store.get_store().mutate(project_id, lambda st: _seg(st, order).lines_log.append(entry))


def is_running(project_id: str, order: int) -> bool:
    t = _running.get((project_id, order))
    return bool(t and t.is_alive())


def collect(state, seg) -> list[dict]:
    """分鏡表裡的每一句台詞 → 送給台詞導演的格式。原句優先拿上次存的 source（分鏡表裡的可能已經是導過的）。"""
    film = state.film_language or "ja"
    before = {(ln.get("shot_id"), ln.get("k", 0)): ln.get("source") for ln in seg.lines}
    out: list[dict] = []
    for shot in seg.shot_list.shots:
        entries = shot.dialogue_lines or ([{"speaker": shot.speaker, "text": shot.dialogue}] if shot.dialogue else [])
        sec = float(shot.total_seconds or 0)
        for k, d in enumerate(entries):
            text = (d.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "id": f"L{len(out) + 1}", "shot_id": shot.shot_id, "k": k,
                "speaker": d.get("speaker") or shot.speaker or "",
                # 畫面裡沒有別人＝對著鏡頭講（主持、旁白式講解）：寫明聽的人是觀眾，不然模型預設客氣的「〜ください」
                #    （幕後第 1 集：「留言告訴我們」被寫成「コメントで教えてください」）
                "listeners": [c for c in shot.characters_in_frame if c != (d.get("speaker") or shot.speaker)]
                             or ["the viewers (talking straight to the camera)"],
                "voice_role": shot.voice_role, "scene": shot.director_scene_id or "",
                "seconds": round(sec / max(1, len(entries)), 1),
                "max_units": gates.max_units(sec / max(1, len(entries)), film),
                "performance": (shot.performance or shot.description or "")[:200],
                "source": before.get((shot.shot_id, k)) or text,
            })
    return out


def people(state, seg) -> dict[str, str]:
    """{角色名: 是什麼人}：場面調度寫的（"human woman, office worker"）優先，沒有就用劇本解析的外貌。
    台詞導演要靠這個選人稱（女生不說俺）、關卡要靠這個擋名字被翻掉。"""
    out = {c.name: (c.visual_description or "") for c in state.screenplay.characters}
    for dscene in seg.director_scenes.values():
        for c in (dscene.get("characters") or {}).values():
            if c.get("name"):
                out[c["name"]] = c.get("kind") or out.get(c["name"], "")
    en = glossary_en(state)
    cast = set(out)
    for name, say in glossary(state).items():
        if name in cast or not say:
            note = (f"a name — say it as {say}" if say else "a name") + (f"; in English subtitles write {en[name]}" if name in en else "")
            out[name] = (out.get(name, "") + "; " + note).strip("; ")
        else:
            # 名字表裡不是角色的那幾行＝專有名詞／用語（提示詞=プロンプト/prompt）：照這個翻，不是當成人名
            #    （幕後第 1 集：「翻車」被照字面寫成「事故現場」、「提示詞」沒有固定譯法）
            out[name] = f"a term (not a person) — in the film language always say it as {say}"                 + (f"; in English subtitles write {en[name]}" if name in en else "")
    for name, persona in personas(state).items():
        out[name] = (out.get(name, "") + f"; personality: {persona}").strip("; ")
    return out


def _rows(state):
    """名字表一行「名字=原聲唸法/英文寫法/個性」（後兩段可省）。例：深息=シェンシー/Shenxi/溫柔的男生，自稱僕。"""
    for row in (state.name_glossary or "").splitlines():
        name, _, rest = row.partition("=")
        say, _, tail = rest.partition("/")
        en, _, _ = tail.partition("/")
        if name.strip():
            yield name.strip(), say.strip(), en.strip()


def personas(state) -> dict[str, str]:
    """{名字: 個性}：名字表第四段（幕後第 1 集：深息是溫柔的男生、自稱僕 —— 台詞導演不知道，重跑一次就變回「私」）。"""
    out = {}
    for row in (state.name_glossary or "").splitlines():
        name, _, rest = row.partition("=")
        parts = rest.split("/", 2)
        if name.strip() and len(parts) == 3 and parts[2].strip():
            out[name.strip()] = parts[2].strip()
    return out


def glossary(state) -> dict[str, str]:
    """{名字: 原聲唸法}。"""
    return {n: s for n, s, _ in _rows(state)}


def glossary_en(state) -> dict[str, str]:
    """{名字: 英文字幕的寫法}（深息在英文版是 Shenxi，模型自己拼成 ShinSoku）。"""
    return {n: e for n, _, e in _rows(state) if e}


def context(state, seg) -> str:
    """給台詞導演的故事背景：片名、角色（是什麼人）、這一段每場戲的原文（看得出誰跟誰、什麼關係）。"""
    sp = state.screenplay
    chars = "\n".join(f"- {n}: {k}" for n, k in people(state, seg).items())
    scenes = [s for s in sp.scenes if s.scene_number in set(seg.scene_numbers)]
    body = "\n\n".join(f"[Scene {s.scene_number}] {(s.raw_text or s.action)[:CONTEXT_CHARS]}" for s in scenes)
    return f"Title: {sp.title}\nCharacters:\n{chars}\n\nScene text (the script as written):\n{body}"


def start(project_id: str, order: int) -> bool:
    with _lock:
        if is_running(project_id, order):
            return False

        def reset(state):
            sg = _seg(state, order)
            sg.lines_log = []
            sg.lines_status = "running"

        project_store.get_store().mutate(project_id, reset)
        t = threading.Thread(target=_run, args=(project_id, order), daemon=True, name=f"lines-{project_id}-{order}")
        _running[(project_id, order)] = t
        t.start()
        return True


def _run(project_id: str, order: int) -> None:
    store = project_store.get_store()
    try:
        state = store.load(project_id)
        seg = _seg(state, order)
        film = state.film_language or "ja"
        learners = [x for x in (state.learner_languages or ["zh", "en"]) if x != film] or ["en"]
        lines = collect(state, seg)
        if not lines:
            raise RuntimeError("this part has no dialogue to direct")
        _log(project_id, order, "start", lines=len(lines), film=film, learners=learners)
        results, notes, usage = line_director.direct(
            lines, context(state, seg), film, learners,
            on_event=lambda e: _log(project_id, order, e.pop("kind"), **e), people=people(state, seg), en_names=glossary_en(state),
            name_say=glossary(state))
        apply(project_id, order, results, notes, usage)
        left = sum(1 for r in results for i in r["issues"] if i["level"] == "ERROR")
        # 讀音跟字典不一樣、模型確認後還是不改的：不擋（字典不懂上下文），但學習教材讀音錯就是教錯 → 數字講出來請人看
        doubts = sum(1 for r in results for i in r["issues"] if i["rule"] == "reading")
        _log(project_id, order, "done", lines=len(results), notes=len(notes), unresolved=left, reading_doubts=doubts,
             cost=round(usage["cost_usd"], 4), model=usage["model"])
    except Exception as exc:  # noqa: BLE001 - 背景工作：錯誤寫進紀錄給畫面看
        logger.exception("line direction failed")
        _log(project_id, order, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")

        def failed(st):
            _seg(st, order).lines_status = "failed"

        store.mutate(project_id, failed)


def say(line: dict, film: str, table: dict[str, str]) -> str:
    """送 H3 唸的字：名字（和名字表裡的詞）換成讀音。
    第三世 s3_gen 的 READ 表就是這招：H3 看到漢字名字會照中文唸（月音→げつおん），給假名才唸對。
    字幕仍用漢字（spoken），只有送去生成的那一份換。"""
    text = line["spoken"]
    if film == "ja":
        for base, rb in gates.tokens(line.get("ruby") or ""):
            if rb and base in table and not table[base]:
                table[base] = rb          # 名字表只寫了名字沒寫唸法 → 用台詞導演標的讀音
    for word, reading in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if reading:
            text = text.replace(word, reading)
    return text


def write_dialogue(sg, directed: bool) -> None:
    """把分鏡表裡送影片模型唸的台詞換成導過的版本（directed=True）或劇本原句（False）。
    關掉學語言模式／換影片語言時要換回原句，不然一般生成照樣唸舊的外語台詞。"""
    keep = {(ln["shot_id"], ln.get("k", 0)): ln for ln in sg.lines}
    if not keep or not sg.shot_list:
        return

    def pick(ln: dict) -> str:
        return (ln.get("say") or ln.get("spoken") or "") if directed else (ln.get("source") or "")

    for shot in sg.shot_list.shots:
        if shot.dialogue_lines:
            for k, d in enumerate(shot.dialogue_lines):
                ln = keep.get((shot.shot_id, k))
                if ln and pick(ln):
                    d["text"] = pick(ln)
            if shot.dialogue:
                shot.dialogue = "　".join(d.get("text", "") for d in shot.dialogue_lines)
        elif shot.dialogue and (shot.shot_id, 0) in keep and pick(keep[(shot.shot_id, 0)]):
            shot.dialogue = pick(keep[(shot.shot_id, 0)])


def remap_lines(sg, shot_ids: list[str]) -> None:
    """一鏡被修過（併了鄰鏡的短句、鏡號變了）之後，台詞導演的結果照「原句」重新對回去。

    台詞導演的結果用 (鏡號, 第幾句) 對分鏡表；修鏡會把句子搬到別鏡、句序也變，照鏡號對會配錯句。
    原句（source）不會變 → 用它找回是哪一句，送 H3 的字換回導過的版本（say）。"""
    norm = lambda t: re.sub(r"[\s「」『』。、！？!?,.]", "", t or "")  # noqa: E731
    by_src = {norm(ln.get(f)): ln for ln in sg.lines for f in ("say", "spoken", "source") if ln.get(f)}
    if not by_src:
        return
    for shot in sg.shot_list.shots:
        if shot.shot_id not in shot_ids:
            continue
        entries = shot.dialogue_lines or ([{"speaker": shot.speaker, "text": shot.dialogue}] if shot.dialogue else [])
        for k, d in enumerate(entries):
            ln = by_src.get(norm(d.get("text"))) or _merge_run(sg, norm(d.get("text")), norm)
            if ln:
                ln["shot_id"], ln["k"] = shot.shot_id, k
                d["text"] = ln.get("say") or ln.get("spoken") or d["text"]
        if shot.dialogue_lines:
            shot.dialogue = "　".join(d.get("text", "") for d in shot.dialogue_lines)
        elif entries:
            shot.dialogue = entries[0]["text"]
    sg.subtitles = {}


def _merge_run(sg, text: str, norm) -> dict | None:
    """兩鏡合成一鏡（修鏡把拆成兩半的一句接起來）：台詞導演的結果也是兩筆半句 → 找連續幾筆、原句接起來正好是這一句的，
    合成一筆（原句、口語、送 H3 的字、讀音、字幕各自接起來），其餘幾筆拿掉。找不到回 None。"""
    rows = sg.lines
    for i in range(len(rows)):
        acc = ""
        for j in range(i, min(i + 4, len(rows))):
            acc += norm(rows[j].get("source"))
            if acc == text and j > i:
                run = rows[i:j + 1]
                head = dict(run[0])
                for f in ("source", "spoken", "say", "ruby"):
                    head[f] = "".join(r.get(f) or "" for r in run)
                for f in [k for k in head if k.startswith("sub_")]:
                    head[f] = " ".join((r.get(f) or "").strip() for r in run).strip()
                sg.lines = rows[:i] + [head] + rows[j + 1:]
                return head
            if not text.startswith(acc):
                break
    return None


def apply(project_id: str, order: int, results: list[dict], notes: list[dict], usage: dict) -> None:
    """結果存進段落、導過的台詞寫回分鏡表（H3 唸這個）；舊字幕快取作廢。"""
    st0 = project_store.get_store().load(project_id)
    table = {name: "" for name in people(st0, _seg(st0, order)) if re.search(r"[一-鿿]", name)}
    table.update(glossary(st0))
    for r in results:
        # 讀音標記只該在 ruby 欄位；模型偶爾把「ぷく[ぷく]」寫進原字欄位 → 字幕出現方括號、H3 照著唸（第三世試跑 L8）
        #    strip_ruby 只認「漢字[よみ]」，「ぷく[ぷく]」這種假名後面的它不剝 → 原字欄位本來就不該有方括號，整段拿掉
        if "[" in (r.get("spoken") or ""):
            r["spoken"] = re.sub(r"\[[^\]]*\]", "", r["spoken"])
        if st0.film_language == "ja":   # 名字表寫了唸法 → 字幕上的讀音也照它（深息是シェンシー，模型標成しんそく）
            for name, reading in glossary(st0).items():
                if reading and re.search(r"[一-鿿]", name):
                    r["ruby"] = re.sub(re.escape(name) + r"(\[[^\]]*\])?", f"{name}[{reading}]", r.get("ruby") or "")
        r["say"] = say(r, st0.film_language, dict(table))
    keep = {(r["shot_id"], i): r for i, r in _with_k(results)}

    def fn(st):
        sg = _seg(st, order)
        sg.lines = [{**r, "k": k} for k, r in _with_k(results)]
        sg.study_notes = notes
        for shot in sg.shot_list.shots:
            if shot.dialogue_lines:
                for k, d in enumerate(shot.dialogue_lines):
                    if (shot.shot_id, k) in keep:
                        d["text"] = keep[(shot.shot_id, k)]["say"]   # 送 H3 的版本（名字換讀音）；字幕用 lines 的 spoken
                if shot.dialogue:
                    shot.dialogue = "　".join(d.get("text", "") for d in shot.dialogue_lines)
            elif shot.dialogue and (shot.shot_id, 0) in keep:
                shot.dialogue = keep[(shot.shot_id, 0)]["say"]
        sg.shot_list.film_language = st.film_language
        sg.subtitles = {}
        sg.study_videos = {}
        sg.lines_status = "done"
        sg.spent_usd += usage["cost_usd"]
        st.spend.llm_usd += usage["cost_usd"]
        st.record_decision(choice=f"第 {order} 段台詞導演：{len(results)} 句改成{st.film_language}口語",
                           rationale="Nemotron 照角色關係與情境改寫（讀音、口語、秒數、字幕關卡）")

    project_store.get_store().mutate(project_id, fn)


def _with_k(results: list[dict]):
    """(shot_id, 第幾句) —— 同一鏡兩個人一來一往時要分得出是哪一句。"""
    seen: dict[str, int] = {}
    for r in results:
        k = r.get("k")
        if k is None:
            k = seen.get(r["shot_id"], 0)
        seen[r["shot_id"]] = k + 1
        yield k, r
