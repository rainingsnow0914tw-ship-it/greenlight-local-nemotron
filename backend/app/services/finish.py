"""成片流程（背景工作）：收下的鏡 → 剪頭 → 放大 → 轉場 → 接片 → 燒字幕。每一步寫紀錄給畫面看。

每一步都照作者本機影片管線實測過的做法（只讀參考，不 import 那邊的程式）：
  剪頭   查證量到開頭亂語的鏡（clip.trim_head），從第一個字前 0.1 秒剪       （headnoise.py／_剪頭.json）
  放大   動畫風＋ComfyUI 有 RealESRGAN_x4plus_anime_6B：×4 → lanczos 到 1080p，跟純 lanczos 放大混 0.35，
         48 格一段（整批 4 倍會吃爆記憶體），聲音接回原片                       （作者放大腳本實測過的柔和度）
         其他畫風或沒有模型：純 lanczos（真人用動畫模型會把皮膚抹成塑膠 —— 寫實版的放大模型要另外下載）
  轉場   同一場硬切；換場淡出淡入；片頭淡入、片尾淡出。淡只放在講話前後的安靜段（「不吃口白」）
                                                                                   （開工.sh 檢轉場三條線、s3_assemble.py）
  接片   重新編碼成同一規格再接（各鏡尺寸、聲音格式不一定一樣）
  字幕   學語言模式：每一句的時間用查證聽寫量到的「真的開口／講完」（第三世用 whisper 對時間），不是照鏡長平均分；
         一般模式：交給原本的燒字幕（routes/video.py）
"""

from __future__ import annotations

import logging
import re
import subprocess
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import media_store, project_store, settings_store

logger = logging.getLogger(__name__)
_running: dict[tuple[str, int], threading.Thread] = {}
_lock = threading.Lock()
UPSCALE_MODEL = "RealESRGAN_x4plus_anime_6B.pth"   # 動畫風（作者 s3_upscale 用的）
UPSCALE_MODEL_REAL = "RealESRGAN_x4plus.pth"      # 其他畫風（官方 v0.1.0、64MB、BSD-3）
BLEND, CHUNK = 0.35, 48
FADE_SCENE, FADE_FILM_IN, FADE_FILM_OUT = 0.4, 0.8, 1.0
FLASH_OUT, FLASH_IN = 0.2, 0.25   # 閃白：正式管線 s3_assemble 的長度
ANIME_STYLES = {"anime"}


def _seg(state, order):
    return next(s for s in state.segments if s.order == order)


def _log(project_id: str, order: int, kind: str, **data: Any) -> None:
    entry = {"t": round(time.time(), 1), "kind": kind, **data}
    project_store.get_store().mutate(project_id, lambda st: _seg(st, order).finish_log.append(entry))


def is_running(project_id: str, order: int) -> bool:
    t = _running.get((project_id, order))
    return bool(t and t.is_alive())


def _ff() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def ff(*args) -> None:
    r = subprocess.run([_ff(), "-v", "error", "-y", *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr[-400:]}")


def probe(path: Path) -> tuple[int, int, float, float]:
    """寬、高、秒數、fps。"""
    err = subprocess.run([_ff(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    t = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    f = re.search(r"([\d.]+) fps", err)
    if not m or not t:   # ffmpeg 讀不到這個檔：講清楚是哪個檔、ffmpeg 說什麼（不要只丟 NoneType）
        raise RuntimeError(f"ffmpeg cannot read {path.name}: {err.strip()[-300:]}")
    return (int(m.group(1)), int(m.group(2)),
            int(t.group(1)) * 3600 + int(t.group(2)) * 60 + float(t.group(3)), float(f.group(1)) if f else 24.0)


def start(project_id: str, order: int, subs_only: bool = False) -> bool:
    """subs_only＝只重燒字幕：拿上次成片的母片（已剪頭、放大、接好、沒字幕）跟同一份字幕時間重燒，
    不重新放大（字幕是放大之後才燒的 —— 以前改字幕版型／讀音要重跑一個多小時）。"""
    with _lock:
        if is_running(project_id, order):
            return False

        def reset(state):
            sg = _seg(state, order)
            sg.finish_log, sg.finish_status = [], "running"

        project_store.get_store().mutate(project_id, reset)
        t = threading.Thread(target=_run_subs if subs_only else _run, args=(project_id, order), daemon=True,
                             name=f"finish-{project_id}-{order}")
        _running[(project_id, order)] = t
        t.start()
        return True


def _plan(state, seg) -> list[dict]:
    """要接的鏡（照分鏡表順序；收下的優先，沒有收下的就用有檔案的）＋每鏡的講話時段與場次。"""
    from ..schemas.segment import ClipStatus

    order = {s.shot_id: i for i, s in enumerate(seg.shot_list.shots)}
    shots = {s.shot_id: s for s in seg.shot_list.shots}
    clips = [c for c in seg.clips if c.uri]
    approved = [c for c in clips if c.status == ClipStatus.APPROVED]
    use = sorted(approved or clips, key=lambda c: order.get(c.shot_id, 999))
    out = []
    for c in use:
        words = (c.verify or {}).get("words") or []
        out.append({"clip": c, "shot": shots.get(c.shot_id), "scene": (shots.get(c.shot_id).director_scene_id
                    if shots.get(c.shot_id) else ""), "trim": float(c.trim_head or 0.0),
                    # 台詞講完後模型又多講的（checks.tail_extra）→ 剪在這一秒；講話時段也只算到台詞結束
                    "end": float((c.verify or {}).get("trim_tail") or 0.0),
                    "speech": (words[0][1], min(words[-1][2], float((c.verify or {}).get("trim_tail") or 1e9)))
                    if words else None})
    return out


def _upscale_comfy(src: Path, dst: Path, tw: int, th: int, project_id: str, say, model: str = UPSCALE_MODEL) -> None:
    """作者 s3_upscale 的圖（RealESRGAN 動畫 ×4 → lanczos → 跟純 lanczos 混 0.35），48 格一段，聲音接回原片。"""
    from ..engines import comfy

    url = settings_store.comfy_url()
    _, _, dur, fps = probe(src)
    n = int(round(dur * fps))
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i in range(0, n, CHUNK):
            part = Path(tmp) / f"up_{src.stem}_{i:05d}.mp4"
            # -r 一定要寫：不寫的話片段標成 24.51／25.5 fps，ComfyUI 照那個 fps 組回去，整支變短、聲音被 -shortest 剪掉
            #    （實測 8.71 秒放大後剩 7.83 秒，講到最後的「ニュース見た？」會被切）
            ff("-i", src, "-vf", f"select='between(n\\,{i}\\,{min(i + CHUNK, n) - 1})',setpts=N/{fps}/TB", "-an",
               "-r", f"{fps}", "-c:v", "libx264", "-crf", "12", part)
            name = comfy.upload_refs(url, [str(part)])[0]
            g = {
                "1": {"class_type": "LoadVideo", "inputs": {"file": name}},
                "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
                "3": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
                "4": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["3", 0], "image": ["2", 0]}},
                "5": {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "upscale_method": "lanczos",
                                                             "width": tw, "height": th, "crop": "disabled"}},
                "5b": {"class_type": "ImageScale", "inputs": {"image": ["2", 0], "upscale_method": "lanczos",
                                                              "width": tw, "height": th, "crop": "disabled"}},
                "5c": {"class_type": "ImageBlend", "inputs": {"image1": ["5b", 0], "image2": ["5", 0],
                                                              "blend_factor": BLEND, "blend_mode": "normal"}},
                "6": {"class_type": "CreateVideo", "inputs": {"images": ["5c", 0], "fps": ["2", 2]}},
                "7": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0], "filename_prefix": f"greenlight/{project_id}/up",
                                                            "format": "auto"}},
            }
            video, _ = comfy.render(url, g)
            out = Path(tmp) / f"done_{i:05d}.mp4"
            out.write_bytes(video)
            parts.append(out)
            say(i + CHUNK, n)
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        ff("-f", "concat", "-safe", "0", "-i", lst, "-i", src, "-map", "0:v", "-map", "1:a?", "-vf", f"setpts=N/{fps}/TB",
           "-r", f"{fps}", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", dst)
    # 關卡：放大前後長度要一樣（差超過 0.1 秒＝格數或 fps 跑掉，聲音會被剪）—— 停下來講，不要默默交一支短掉的片
    got = probe(dst)[2]
    if abs(got - dur) > 0.1:
        raise RuntimeError(f"upscaled {src.name} is {got:.2f}s but the source is {dur:.2f}s — frames or fps got lost")


def _run(project_id: str, order: int) -> None:
    store = project_store.get_store()
    work = None
    try:
        state = store.load(project_id)
        seg = _seg(state, order)
        plan = _plan(state, seg)
        if not plan:
            raise RuntimeError("no generated shots to assemble yet")
        portrait = (state.aspect_ratio or "16:9") == "9:16"
        tw, th = (1080, 1920) if portrait else (1920, 1080)
        anime = (state.style_id or "") in ANIME_STYLES
        url = settings_store.comfy_url()
        # 動畫風用動畫版模型；寫實與其他畫風用 lanczos —— 兩種畫質都可以接受，選速度快的。
        #    實測 s061_10（寫實）：動畫模型 433 秒／鏡、lanczos 1.4 秒；動畫模型睫毛唇線較利、皮膚沒變塑膠、
        #    相鄰格差異 0.97～0.98 倍（沒殘影）—— 畫質能接受，輸在速度。（「真人用動畫模型會變塑膠」量過不成立。）
        #    寫實模型 settings.json 的 params.upscale_real_model=true 才用（沒做畫面開關：少一個要維護的東西）
        use_real = bool(settings_store.load().get("params", {}).get("upscale_real_model"))
        model = UPSCALE_MODEL if anime else (UPSCALE_MODEL_REAL if use_real else "")
        has_model = bool(model) and _has_upscale_model(url, model)
        _log(project_id, order, "start", shots=len(plan), size=f"{tw}x{th}",
             upscale="model" if has_model else "lanczos", model=model if has_model else "",
             why_lanczos="" if has_model else ("fast (photoreal default)" if not anime else "upscale model not installed"))
        work = Path(tempfile.mkdtemp(prefix="gl_finish_"))
        ready: list[tuple[Path, float, dict]] = []
        for k, p in enumerate(plan):
            c = p["clip"]
            src = media_store.path_for(c.uri)
            if not src or not src.exists():
                _log(project_id, order, "skip", shot=c.shot_id, why="file missing")
                continue
            # ⓪ 放好的現成品：同一個片檔＋同樣的剪頭剪尾＋同樣的放大法和尺寸 → 直接用，不重剪不重放大
            #    （重生一兩鏡就只該重新放大那一兩鏡 —— 以前改一鏡要 23 鏡全部重放大 80 分鐘）
            cache = src.parent / "_finish_cache" / (
                f"{src.stem}_{p['trim']:.2f}_{p['end']:.2f}_{(model if has_model else 'lanczos').split('.')[0]}_{tw}x{th}.mp4")
            if cache.exists() and cache.stat().st_size > 0:
                up = cache
                _log(project_id, order, "reuse", shot=c.shot_id, index=k + 1, of=len(plan))
            else:
                up = None
            # ① 剪頭
            cur = src
            if up is None and (p["trim"] > 0 or p["end"] > p["trim"]):
                cut = work / f"{k:03d}_trim.mp4"
                span = ("-t", f"{p['end'] - p['trim']:.2f}") if p["end"] > p["trim"] else ()
                ff("-ss", f"{p['trim']:.2f}", "-i", src, *span, "-c:v", "libx264", "-crf", "14", "-c:a", "aac", cut)
                cur = cut
                _log(project_id, order, "trim", shot=c.shot_id, seconds=p["trim"], end=p["end"] or None)
            # ② 放大
            if up is None:
                w, h, dur, _ = probe(cur)
                up = work / f"{k:03d}_up.mp4"
                if h >= th:
                    up = cur
                elif has_model:
                    _log(project_id, order, "upscale", shot=c.shot_id, method="model", index=k + 1, of=len(plan))
                    _upscale_comfy(cur, up, tw, th, project_id, lambda i, n, sid=c.shot_id: None, model=model)
                else:
                    ff("-i", cur, "-vf", f"scale={tw}:{th}:flags=lanczos", "-c:v", "libx264", "-crf", "16", "-c:a", "copy", up)
                    _log(project_id, order, "upscale", shot=c.shot_id, method="lanczos", index=k + 1, of=len(plan))
                cache.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(up, cache)
            # ②b 剪完再聽一次：聽寫給的秒數會偏（範例 2 s007_02：「ど」被標成 0.74 秒起、拉了半秒，
            #    照它剪在 0.64 秒，成片裡還留著「はい。はい。」）。拿剪好放大好的這一鏡重聽，台詞前面還有字就再剪 ——
            #    剪的是放大好的檔，不用重新放大；剪過的存回快取，下次重聽就乾淨了
            if p["trim"] > 0 and p["shot"] is not None and (p["shot"].dialogue or "").strip():
                try:
                    from ..verify import asr, checks
                    heard = asr.transcribe(str(up), checks.WHISPER_LANG.get(state.film_language, state.film_language))
                    lead = checks._line_start(heard, p["shot"].dialogue, min_lead=2)   # 已經剪過一次，剩下的兩個音也是亂語（s007_02「でで」）
                except Exception as exc:  # noqa: BLE001 - 重聽失敗不擋成片，照原本剪的
                    logger.warning("re-listen failed: %s", exc)
                    lead = None
                more = 0.0
                if lead:
                    at = float(lead[1])
                    # 台詞第一個音被拉得很長（s007_02「ど」0.20～0.72 秒）＝前面的亂語被併進去了，真正開口在它結束前
                    first = next(((w, a, b) for w, a, b in heard if abs(a - at) < 0.01), None)
                    if first and (first[2] - first[1]) > 0.35 * max(1, len(checks._norm(first[0]))):
                        at = first[2] - 0.22 * max(1, len(checks._norm(first[0])))
                    more = round(at - 0.08, 2)
                if more > 0.05:
                    cut2 = work / f"{k:03d}_retrim.mp4"
                    ff("-ss", f"{more:.2f}", "-i", up, "-c:v", "libx264", "-crf", "14", "-c:a", "aac", cut2)
                    shutil.copyfile(cut2, cache)
                    up = cut2
                    p["trim"] += more
                    _log(project_id, order, "retrim", shot=c.shot_id, seconds=more, extra=lead[0])
            # ③ 轉場：淡只放在安靜段（不吃口白）
            _, _, dur, _ = probe(up)
            sp = p["speech"]
            # 聽寫量到的開口／講完會偏 0.2～0.3 秒（最後一鏡：淡出照「講完」算，「見られるよ」的「れるよ」被淡掉）→ 前後各留 0.3 秒
            head_quiet = max(0.0, (sp[0] - p["trim"] - 0.3) if sp else dur)
            tail_quiet = max(0.0, (dur - (sp[1] - p["trim"]) - 0.3) if sp else dur)
            first, last = k == 0, k == len(plan) - 1
            new_in = not first and p["scene"] != plan[k - 1]["scene"]
            new_out = not last and p["scene"] != plan[k + 1]["scene"]
            fin = min(FADE_FILM_IN if first else FADE_SCENE if new_in else 0.0, head_quiet)
            fout = min(FADE_FILM_OUT if last else FADE_SCENE if new_out else 0.0, tail_quiet)
            vf = [f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p"]
            af = ["aresample=48000"]
            # 閃白（分鏡標 flash_before）：前一鏡最後 0.2 秒淡成白、這一鏡頭 0.25 秒從白淡回來，聲音不淡 —— 照正式管線 s3_assemble
            flash_in = bool(p["shot"] and p["shot"].flash_before) and not first
            flash_out = not last and bool(plan[k + 1]["shot"] and plan[k + 1]["shot"].flash_before)
            if flash_in:
                vf.append(f"fade=t=in:st=0:d={FLASH_IN}:color=white")
                fin = 0.0
            if flash_out:
                vf.append(f"fade=t=out:st={dur - FLASH_OUT:.3f}:d={FLASH_OUT}:color=white")
                fout = 0.0
            if flash_in or flash_out:
                _log(project_id, order, "flash", shot=c.shot_id, flash_in=flash_in, flash_out=flash_out)
            if fin > 0.05:
                vf.append(f"fade=t=in:st=0:d={fin:.2f}")
                af.append(f"afade=t=in:st=0:d={fin:.2f}")
            if fout > 0.05:
                vf.append(f"fade=t=out:st={dur - fout:.2f}:d={fout:.2f}")
                af.append(f"afade=t=out:st={dur - fout:.2f}:d={fout:.2f}")
            norm = work / f"{k:03d}_norm.mp4"
            ff("-i", up, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex",
               f"[0:v]{','.join(vf)}[v];[0:a]{','.join(af)}[a0];[a0][1:a]amix=inputs=2:duration=first[a]", "-map", "[v]",
               "-map", "[a]", "-c:v", "libx264", "-crf", "16", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", f"{dur:.3f}", norm)
            if fin > 0.05 or fout > 0.05:
                _log(project_id, order, "transition", shot=c.shot_id, fade_in=round(fin, 2), fade_out=round(fout, 2))
            ready.append((norm, probe(norm)[2], p))
        # ④ 接片
        lst = work / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p, _, _ in ready), encoding="utf-8")
        master = work / "master.mp4"
        ff("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", master)
        total = probe(master)[2]
        outputs = {"master": media_store.save(project_id, f"part{order}_master", master.read_bytes(), "video/mp4")}
        _log(project_id, order, "assembled", shots=len(ready), seconds=round(total, 1))
        # ⑤ 燒字幕（學語言模式）。字幕時間有台詞就算、存起來：之後對這支成片燒字幕／下載字幕檔要用同一份
        items = _cues(seg, ready, state.film_language) if seg.lines else []
        if state.study_mode and seg.lines:
            from ..language import jobs, study_subs

            for lang in [x for x in state.learner_languages if x != state.film_language]:
                _log(project_id, order, "subtitles", lang=lang, lines=len(items))
                out = work / f"study_{lang}.mp4"
                study_subs.burn(master, items, state.film_language, lang, out, names=jobs.glossary(state))
                outputs[f"{state.film_language}-{lang}"] = media_store.save(
                    project_id, f"part{order}_{state.film_language}-{lang}", out.read_bytes(), "video/mp4")

        def done(st):
            sg = _seg(st, order)
            sg.finish_outputs, sg.finish_status, sg.finish_cues = outputs, "done", items
            sg.output_uri, sg.output_seconds = outputs["master"], total   # 原本的「接片完成」狀態一起成立
            st.record_decision(choice=f"第 {order} 段成片：{len(ready)} 鏡、{total:.0f} 秒",
                               rationale="剪頭→放大→轉場→接片→燒字幕")

        store.mutate(project_id, done)
        _log(project_id, order, "done", outputs=list(outputs), seconds=round(total, 1))
    except Exception as exc:  # noqa: BLE001 - 背景工作：錯誤寫進紀錄給畫面看
        logger.exception("finish failed")
        _log(project_id, order, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        store.mutate(project_id, lambda st: setattr(_seg(st, order), "finish_status", "failed"))
    finally:
        # 剪頭／放大／正規化／接片的中間檔：產出都已存進媒體庫，暫存資料夾一律清掉（一段可以到好幾 GB）
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def _run_subs(project_id: str, order: int) -> None:
    """只重燒字幕（見 start 的 subs_only）。母片或字幕時間不在 → 失敗、請使用者跑完整成片。"""
    from ..language import jobs, study_subs

    store = project_store.get_store()
    work = None
    try:
        state = store.load(project_id)
        seg = _seg(state, order)
        master = media_store.path_for(seg.finish_outputs.get("master") or "") if seg.finish_outputs.get("master") else None
        if not master or not master.exists() or not seg.finish_cues:
            raise RuntimeError("no finished master to re-subtitle — run the full finish first")
        cues = seg.finish_cues
        # 字幕時間沿用上次的，台詞內容（讀音、字幕）用現在的：上次之後改過台詞，要燒的是改過的版本
        now = {ln["id"]: ln for ln in seg.lines}
        items = [{**c, "line": now.get(c["line"].get("id"), c["line"])} for c in cues]
        work = Path(tempfile.mkdtemp(prefix="gl_finish_"))
        outputs = dict(seg.finish_outputs)
        for lang in [x for x in state.learner_languages if x != state.film_language]:
            _log(project_id, order, "subtitles", lang=lang, lines=len(items), only=True)
            out = work / f"study_{lang}.mp4"
            study_subs.burn(master, items, state.film_language, lang, out, names=jobs.glossary(state))
            outputs[f"{state.film_language}-{lang}"] = media_store.save(
                project_id, f"part{order}_{state.film_language}-{lang}", out.read_bytes(), "video/mp4")

        def done(st):
            sg = _seg(st, order)
            sg.finish_outputs, sg.finish_status = outputs, "done"

        store.mutate(project_id, done)
        _log(project_id, order, "done", outputs=list(outputs), subs_only=True)
    except Exception as exc:  # noqa: BLE001 - 背景工作：錯誤寫進紀錄給畫面看
        logger.exception("re-subtitle failed")
        _log(project_id, order, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        store.mutate(project_id, lambda st: setattr(_seg(st, order), "finish_status", "failed"))
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def _cues(seg, ready, film: str) -> list[dict]:
    """字幕時間＝查證聽寫量到的開口／講完（扣掉剪頭、加上前面幾鏡的長度）；同一鏡兩句照長度分。"""
    from ..language import gates

    by_shot: dict[str, list[dict]] = {}
    for ln in seg.lines:
        by_shot.setdefault(ln["shot_id"], []).append(ln)
    items, cursor = [], 0.0
    for _, dur, p in ready:
        lines = sorted(by_shot.get(p["clip"].shot_id, []), key=lambda x: x.get("k", 0))
        if lines:
            a, b = (p["speech"][0] - p["trim"], p["speech"][1] - p["trim"]) if p["speech"] else (0.2, dur - 0.15)
            a, b = max(0.0, a), min(dur - 0.05, b + 0.3)        # 講完多留 0.3 秒（第三世同一個數）
            w = [max(1, gates.speak_units(x["spoken"], film)) for x in lines]
            t = a
            for ln, wi in zip(lines, w):
                d = (b - a) * wi / sum(w)
                items.append({"start": round(cursor + t, 2), "end": round(cursor + t + d, 2), "line": ln})
                t += d
        cursor += dur
    return items


def _has_upscale_model(url: str, model: str = UPSCALE_MODEL) -> bool:
    """問 ComfyUI 自己有沒有這個放大模型（不憑記憶）。"""
    UPSCALE = model  # noqa: N806
    try:
        import httpx

        d = httpx.get(f"{url}/object_info/UpscaleModelLoader", timeout=10).json()
        opt = d["UpscaleModelLoader"]["input"]["required"]["model_name"]
        names = opt[1].get("options", []) if isinstance(opt[0], str) else opt[0]
        return UPSCALE in names
    except Exception:  # noqa: BLE001
        return False
