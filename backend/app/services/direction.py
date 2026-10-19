"""導演工作：一個段落 → 每一場先場面調度、再跑導演迴圈 → 換成 Greenlight 的分鏡表。在背景跑，一路寫紀錄。

為什麼在背景跑：一場戲導演迴圈要 7～15 分鐘（寫稿、關卡擋、改、製作人審），原版「分鏡」是一次呼叫等結果，
這樣畫面會卡住、使用者不知道程式在幹嘛。改成背景工作，每一步寫進 segment.direction_log，
畫面輪詢這份紀錄顯示進度 —— 使用者要看得出來 UI 在跑什麼、做到哪一步。

紀錄怎麼寫：給人看的一句話（三語言由前端翻）＋結構化的數字。每一筆都馬上存檔 ——
程式中途被關掉，重開還看得到做到哪裡。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config_loader, project_store

logger = logging.getLogger(__name__)
_running: dict[tuple[str, int], threading.Thread] = {}
_lock = threading.Lock()


def _style_zh(style_id: str | None) -> str:
    for p in config_loader.styles().get("presets", []):
        if p.get("id") == style_id:
            return p.get("prompt_prefix_zh") or p.get("prompt_prefix", "")
    return ""


def _log(project_id: str, order: int, kind: str, **data: Any) -> None:
    """寫一筆進度紀錄，馬上存檔。"""
    entry = {"t": round(time.time(), 1), "kind": kind, **data}

    def fn(state):
        seg = next(s for s in state.segments if s.order == order)
        seg.direction_log.append(entry)

    project_store.get_store().mutate(project_id, fn)


def is_running(project_id: str, order: int) -> bool:
    t = _running.get((project_id, order))
    return bool(t and t.is_alive())


def start(project_id: str, order: int) -> bool:
    """開始導這一段；已經在跑就不重開（回 False）。"""
    with _lock:
        if is_running(project_id, order):
            return False

        def reset(state):
            seg = next(s for s in state.segments if s.order == order)
            seg.direction_log = []
            seg.direction_status = "running"

        project_store.get_store().mutate(project_id, reset)
        t = threading.Thread(target=_run, args=(project_id, order), daemon=True, name=f"direct-{project_id}-{order}")
        _running[(project_id, order)] = t
        t.start()
        return True


def _run(project_id: str, order: int) -> None:
    from ..agents import stager
    from ..director import director
    from ..director.bridge import to_greenlight_shots
    from ..schemas.shot import ShotList

    store = project_store.get_store()
    try:
        state = store.load(project_id)
        seg = next(s for s in state.segments if s.order == order)
        scenes = [s for s in state.screenplay.scenes if s.scene_number in set(seg.scene_numbers)]
        _log(project_id, order, "start", scenes=len(scenes))
        style = _style_zh(state.style_id)
        all_shots = []
        for n, scene in enumerate(scenes, 1):
            sid = f"s{scene.scene_number:03d}"
            dscene = seg.director_scenes.get(sid)
            if not dscene:
                _log(project_id, order, "staging", scene=scene.scene_number, index=n, of=len(scenes))
                dscene, usage = stager.stage(scene, state.screenplay, style, series=state.screenplay.title)

                def keep(st, dscene=dscene, sid=sid, usage=usage):
                    sg = next(s for s in st.segments if s.order == order)
                    sg.director_scenes[sid] = dscene
                    sg.spent_usd += usage["cost_usd"]
                    st.spend.llm_usd += usage["cost_usd"]

                store.mutate(project_id, keep)
                _log(project_id, order, "staged", scene=scene.scene_number,
                     spots=list(dscene["render_zh"]["spots"]), characters=[c["name"] for c in dscene["characters"].values()],
                     lines=len(dscene["dialogue"]), unverified=sum(1 for d in dscene["dialogue"] if d.get("unverified")),
                     cost=usage["cost_usd"])
            # 長場次切段導（第三世第 2 話試跑：一場 30～40 句台詞，一次要寫 50+ 鏡 →
            #    思考撞頂回空、或 57 鏡剩 116 錯原樣交回；第 2 場 36 句只寫出 1 鏡）。
            #    同一個場面調度（站位、空景、人物不變），只把台詞切成每段 ≤ CHUNK_LINES 句，一段一段導再接起來。
            parts = split_scene(dscene)
            final = {"shots": [], "status": "passed", "note": ""}

            def direct_part(k, part, scene=scene, parts=parts):
                done = None
                for ev in director.direct_iter(part):
                    if ev["type"] == "round":
                        _log(project_id, order, "round", scene=scene.scene_number, part=k, parts=len(parts),
                             round=ev["round"], shots=ev["shots"],
                             errors=ev["errors"], warnings=ev.get("warnings", 0), rejected=bool(ev.get("rejected")),
                             seconds=ev.get("seconds"), tokens=(ev.get("usage") or {}).get("total_tokens"),
                             top=_top_rules(ev.get("issues") or []))
                    elif ev["type"] == "critique":
                        _log(project_id, order, "critique", scene=scene.scene_number, part=k, verdict=ev.get("verdict"),
                             notes=[{"shots": x.get("shots"), "quote": x.get("quote"), "problem": x.get("problem"),
                                     "direction": x.get("direction")} for x in ev.get("notes") or []][:8],
                             overall=ev.get("overall"),
                             dropped=len(ev.get("dropped_notes") or []), model=ev.get("model"))
                    elif ev["type"] == "done":
                        done = ev
                return done

            # 同一場的幾段同時導（每段各自獨立、共用同一份場面調度）：4 段排隊要 45 分鐘，同時跑約 1/4
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(len(parts), PARALLEL_PARTS)) as pool:
                results = list(pool.map(lambda kp: direct_part(*kp), enumerate(parts, 1)))
            for k, done in enumerate(results, 1):
                if not done or not done.get("shots"):
                    raise RuntimeError(f"scene {scene.scene_number} part {k}/{len(parts)}: the director produced no shot list")
                final["shots"] += done["shots"]
                if done["status"] != "passed":
                    final["status"], final["note"] = done["status"], done.get("note", "")
            for i, sh in enumerate(final["shots"], 1):
                sh["shot_id"] = f"s{scene.scene_number:03d}_{i:02d}"
            # 規則 26：正反打合成同框（格式固定的事交給程式，每合一組重跑關卡，變壞就不合）
            from ..director import two_shots

            final["shots"], pairs = two_shots.merge(dscene, final["shots"])
            final["shots"], listeners = two_shots.keep_both_in_frame(dscene, final["shots"])
            if pairs or listeners:
                _log(project_id, order, "two_shots", scene=scene.scene_number, pairs=pairs + listeners,
                     shots=len(final["shots"]))
            _log(project_id, order, "scene_done", scene=scene.scene_number, status=final["status"],
                 shots=len(final["shots"]), note=final.get("note", ""))
            all_shots += to_greenlight_shots(dscene, final["shots"], scene.scene_number)

        def finish(st):
            from ..schemas.project import Stage

            sg = next(s for s in st.segments if s.order == order)
            if sg.shot_list is not None:
                # 重新導：舊鏡頭、試拍、成片、參考卡都是照舊分鏡做的，新分鏡的鏡號會重複
                # （s001_01 會接到舊的那支影片）。跟 plan-shots force 用同一個共用重置。
                from ..routes.video import reset_segment_progress

                reset_segment_progress(st, sg)
                sg.refs_status, sg.refs_log = None, []
            sg.clear_line_direction()   # 新分鏡：舊的台詞導演結果作廢（鏡號會重複）
            sg.shot_list = ShotList(screenplay_title=st.screenplay.title, shots=all_shots,
                                    aspect_ratio=st.aspect_ratio or "16:9",
                                    prompt_language=st.prompt_language or "zh", film_language=st.film_language or "zh",
                                    style_id=st.style_id or "anime",
                                    planning_notes=["Directed by Nemotron with pre-render gates and a producer review"])
            sg.direction_status = "done"
            if st.stage.order < Stage.SHOTS_PLANNED.order:
                st.stage = Stage.SHOTS_PLANNED
            st.record_decision(choice=f"第 {order} 段導演分鏡：{len(all_shots)} 鏡",
                               rationale="Nemotron 導演迴圈（關卡＋製作人）")

        store.mutate(project_id, finish)
        _log(project_id, order, "done", shots=len(all_shots), seconds=sum(s.total_seconds for s in all_shots))
    except Exception as exc:  # noqa: BLE001 - 背景工作：錯誤寫進紀錄給畫面看，不能默默消失
        logger.exception("direction failed")
        _log(project_id, order, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")

        def failed(st):
            sg = next(s for s in st.segments if s.order == order)
            sg.direction_status = "failed"

        store.mutate(project_id, failed)


CHUNK_LINES = 10   # 一段最多幾句台詞（第 61 場 21 句一次導得過、第三世 30～40 句導不過）
PARALLEL_PARTS = 5  # 同時導幾段（Nebius API 呼叫，不吃本機顯示卡）


def split_scene(dscene: dict) -> list[dict]:
    """台詞太多的場次切成幾段（同一份場面調度，只換台詞），每段附上前後文讓導演接得上。"""
    import copy
    import math

    lines = dscene.get("dialogue") or []
    if len(lines) <= CHUNK_LINES:
        return [dscene]
    n = math.ceil(len(lines) / CHUNK_LINES)
    size = math.ceil(len(lines) / n)
    say = lambda d: f"{d.get('speaker', '')}: {d.get('line') or d.get('text') or ''}"  # noqa: E731
    parts = []
    for k in range(n):
        a, b = k * size, min((k + 1) * size, len(lines))
        part = copy.deepcopy(dscene)
        part["dialogue"] = lines[a:b]
        part["part_note"] = (
            f"This scene is long, so it is directed in {n} parts. You are directing part {k + 1} of {n}: write shots "
            "for ONLY the dialogue lines listed here, in order. Same set, same blocking, same characters as the rest "
            "of the scene. "
            + (f"The previous part ended on: «{say(lines[a - 1])}» — your first shot picks up right after it. " if a else
               "This is the opening of the scene. ")
            + (f"The next part starts with: «{say(lines[b])}» — end on a moment that leads into it." if b < len(lines) else
               "This is the end of the scene."))
        parts.append(part)
    return parts


def _top_rules(issues: list) -> list:
    """這一回合最常被哪幾條擋（給畫面一句話：「3 鏡秒數不夠、2 鏡寫了會出聲的字」）。"""
    from collections import Counter

    c = Counter(i["rule"] for i in issues if i.get("level") == "ERROR")
    return [{"rule": r, "count": n} for r, n in c.most_common(3)]
