"""替某一段落分鏡，並跑完整的成本/成功率估算。約 $0.03–0.08。

用法：
    python scripts/plan_segment.py shensi-001 "路徑/劇本.txt" --segment 1
    python scripts/plan_segment.py shensi-001 "路徑/劇本.txt" --segment 1 --style anime
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

from app.agents import segmenter, shot_planner  # noqa: E402
from app.schemas.plan import BudgetVerdict, QualityTier  # noqa: E402
from app.services import config_loader, cost_engine, project_store  # noqa: E402


def _w(t: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in t)


def _lp(t: str, n: int) -> str:
    return t + " " * max(0, n - _w(t))


def _rp(t: str, n: int) -> str:
    return " " * max(0, n - _w(t)) + t


_MARK = {BudgetVerdict.OK: "  ", BudgetVerdict.WARN: "! ", BudgetVerdict.BLOCK: "!!"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("script_path")
    ap.add_argument("--segment", type=int, default=1, help="第幾段（1-based）")
    ap.add_argument("--style", default="photoreal")
    ap.add_argument("--ratio", default="16:9")
    args = ap.parse_args()

    store = project_store.get_store()
    state = store.load(args.project_id)
    if not state or not state.screenplay:
        print(f"找不到專案「{args.project_id}」或尚未解析劇本。")
        return 1

    raw = Path(args.script_path).read_text(encoding="utf-8")
    sp = state.screenplay

    # 用章節方案（零成本，不重跑 LLM 分段）
    marks = segmenter.detect_chapters(raw)
    plan = segmenter._plan_from_chapters(sp, marks)  # noqa: SLF001
    if not plan or args.segment > len(plan.segments):
        print("找不到指定的段落。")
        return 1

    segment = plan.segments[args.segment - 1]
    style = config_loader.style_preset(args.style)

    print(f"《{sp.title}》")
    print(f"  段落 {segment.order}／{len(plan.segments)}：{segment.title}")
    print(
        f"  場次 {segment.scene_numbers} · 推估 {segment.estimated_seconds:.0f} 秒 · "
        f"{style.get('label')} · {args.ratio}"
    )
    print("\n分鏡中……（Gemini 3.1 Pro）\n")

    shot_list, usage = shot_planner.plan_segment(
        sp, segment, style_id=args.style, aspect_ratio=args.ratio
    )

    print(
        f"用量：輸入 {usage['prompt_tokens']:,} / 輸出 {usage['output_tokens']:,} tokens"
        f" · **${usage['cost_usd']}**"
    )
    print()
    print(
        f"鏡頭表：{shot_list.clip_count} 個鏡頭 · 成片 {shot_list.total_seconds} 秒 · "
        f"{shot_list.api_call_count} 次 Veo 呼叫"
    )
    print()

    # ---- 鏡頭表 ----
    hdr = (
        "  " + _lp("鏡號", 12) + _rp("秒", 5) + _lp("鏡別", 15) + _lp("運鏡", 10)
        + _lp("接續", 6) + "  " + _lp("內容", 40)
    )
    print(hdr)
    print("-" * _w(hdr))
    for s in shot_list.shots:
        dur = f"{s.total_seconds}"
        if s.extensions:
            dur += f"({s.base_duration_seconds}+{s.extensions})"
        print(
            "  " + _lp(s.shot_id, 12) + _rp(dur, 5)
            + _lp(s.shot_type.value, 15) + _lp(s.camera_move.value, 10)
            + _lp("接" if s.first_frame_from else "—", 6)
            + "  " + _lp(s.description[:36], 40)
        )
    print()

    # ---- 成本與成功率（不呼叫 AI）----
    plan_result = cost_engine.build_plan(
        plan_id=f"{args.project_id}-seg{args.segment}",
        shot_list=shot_list,
        principal_count=2,
        supporting_count=1,
        screenplay_tokens=usage["prompt_tokens"],
        tiers=[QualityTier.LITE_720P, QualityTier.FAST_720P, QualityTier.STANDARD_1080P],
    )

    hdr = (
        "  " + _lp("檔位", 20) + _rp("標價", 9) + _rp("重拍", 9)
        + _rp("總計", 10) + _rp("一次過關", 11)
    )
    print(hdr)
    print("-" * _w(hdr))
    for v in plan_result.variants:
        print(
            _MARK[v.budget_verdict] + _lp(v.tier_label, 20)
            + _rp(f"${v.cost.video_base_usd:.2f}", 9)
            + _rp(f"${v.cost.retry_expected_usd:.2f}", 9)
            + _rp(f"${v.cost.total_usd:.2f}", 10)
            + _rp(f"{v.overall_success_rate * 100:.0f}%", 11)
        )
    print()
    print(f"建議：{plan_result.recommendation_reason}")
    print()

    rec = plan_result.variant(plan_result.recommended_tier)
    if rec:
        print("風險鏡頭")
        for est in rec.riskiest_shots:
            shot = next(s for s in shot_list.shots if s.shot_id == est.shot_id)
            print(f"  {est.shot_id}  {est.success_rate * 100:>3.0f}%  {shot.description[:40]}")
            for r in est.risk_factors[:3]:
                print(f"        · {r}")
        print()
        if plan_result.test_shot_required:
            print(f"建議先試拍：{plan_result.test_shot_candidate}")
            print()

    # ---- 提示詞範例 ----
    sample = shot_list.shots[min(1, len(shot_list.shots) - 1)]
    print(f"英文提示詞範例（{sample.shot_id}）")
    print(f"  中文：{sample.description}")
    print(f"  英文：{sample.visual_prompt}")
    print()

    if shot_list.planning_notes:
        print("規劃說明")
        for n in shot_list.planning_notes:
            print(f"  · {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
