"""跑真實劇本解析。約 $0.10–0.40（依劇本長度）。

用法：
    python scripts/parse_script.py "路徑/劇本.txt"
    python scripts/parse_script.py "路徑/劇本.txt" --save my-project
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.agents import script_parser  # noqa: E402
from app.schemas.project import ProjectState, Stage  # noqa: E402
from app.services import project_store  # noqa: E402


def _w(t: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in t)


def _lpad(t: str, n: int) -> str:
    return t + " " * max(0, n - _w(t))


def _rpad(t: str, n: int) -> str:
    return " " * max(0, n - _w(t)) + t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--save", help="存成專案（給 project id）")
    ap.add_argument("--scenes", type=int, default=12, help="列出前幾場")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"找不到檔案：{path}")
        return 1

    text = path.read_text(encoding="utf-8")
    lines = len(text.splitlines())
    print(f"劇本：{path.name}")
    print(f"{lines} 行 · {len(text):,} 字元")
    print("解析中……（Gemini 3.1 Pro，約 30–90 秒）\n")

    screenplay, usage = script_parser.parse(text, title_hint=path.stem)

    # ---- 用量 ----
    print(
        f"用量：輸入 {usage['prompt_tokens']:,} tokens · "
        f"輸出 {usage['output_tokens']:,} tokens · "
        f"**${usage['cost_usd']}**  （{usage['model']}）"
    )
    print()

    # ---- 總覽 ----
    total = screenplay.total_estimated_seconds
    print(f"《{screenplay.title}》")
    print(
        f"  {len(screenplay.scenes)} 場 · {len(screenplay.characters)} 個角色 · "
        f"推估銀幕時間 {total / 60:.1f} 分鐘（{total:.0f} 秒）"
    )
    print()

    # ---- 角色 ----
    print("角色")
    hdr = (
        "  " + _lpad("名字", 14) + _lpad("類型", 8) + _rpad("台詞", 6)
        + "   " + _lpad("劇本寫明的外貌", 44)
    )
    print(hdr)
    print("-" * _w(hdr))
    non_human = set(usage.get("non_human_characters", []))
    for c in screenplay.characters:
        kind = "非人" if c.name in non_human else "人物"
        role = "主角" if c.is_principal else "配角"
        desc = c.visual_description or "（劇本未寫）"
        print(
            "  " + _lpad(c.name, 14) + _lpad(f"{kind}·{role}", 8)
            + _rpad(str(c.line_count), 6) + "   " + _lpad(desc[:40], 44)
        )
    print()

    notes = usage.get("character_notes", {})
    if notes:
        print("角色判斷依據")
        for name, note in notes.items():
            print(f"  · {name}：{note}")
        print()

    # ---- 場次表 ----
    n = min(args.scenes, len(screenplay.scenes))
    print(f"場次表（前 {n} 場，共 {len(screenplay.scenes)} 場）")
    hdr = (
        "  " + _rpad("#", 4) + _rpad("行號", 12) + _rpad("秒", 6)
        + "  " + _lpad("地點", 18) + _lpad("時間", 8) + _lpad("摘要", 40)
    )
    print(hdr)
    print("-" * _w(hdr))
    for s in screenplay.scenes[:n]:
        print(
            "  " + _rpad(str(s.scene_number), 4)
            + _rpad(f"{s.source_line_start}–{s.source_line_end}", 12)
            + _rpad(f"{s.estimated_screen_seconds:.0f}", 6)
            + "  " + _lpad((s.location or "—")[:16], 18)
            + _lpad(s.time_of_day.value, 8)
            + _lpad(s.action[:36], 40)
        )
    print()

    # ---- 警告 ----
    if screenplay.parse_warnings:
        print("解析時的疑慮（誠實回報，不是沉默猜測）")
        for w in screenplay.parse_warnings:
            print(f"  ⚠ {w}")
        print()

    # ---- 抽驗：行號切出來的原文對不對 ----
    #
    # 這一項很重要。行號是模型給的，如果它數錯，
    #    切出來的原文就會對不上場次 —— 而且錯得很安靜。
    sample = screenplay.scenes[0]
    excerpt = sample.raw_text.strip().splitlines()
    print(f"抽驗第 1 場的原文（第 {sample.source_line_start}–{sample.source_line_end} 行）")
    for line in excerpt[:4]:
        print(f"  │ {line[:70]}")
    if len(excerpt) > 4:
        print(f"  │ …（共 {len(excerpt)} 行）")
    print()

    if args.save:
        store = project_store.get_store()
        state = ProjectState(
            project_id=args.save, title=screenplay.title, screenplay=screenplay
        )
        state.stage = Stage.PARSED
        state.spend.llm_usd = usage["cost_usd"]
        state.record_decision(
            choice=f"解析完成：{len(screenplay.scenes)} 場",
            rationale=f"{usage['model']}，${usage['cost_usd']}",
        )
        store.save(state)
        print(f"已存成專案「{args.save}」。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
