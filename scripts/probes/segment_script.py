"""把已解析的劇本切成可分段拍攝的方案，並顯示每段成本。

用法：
    python scripts/segment_script.py shensi-001 "路徑/劇本.txt"
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

from app.agents import segmenter  # noqa: E402
from app.schemas.plan import QualityTier  # noqa: E402
from app.services import config_loader, project_store  # noqa: E402


def _w(t: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in t)


def _lp(t: str, n: int) -> str:
    return t + " " * max(0, n - _w(t))


def _rp(t: str, n: int) -> str:
    return " " * max(0, n - _w(t)) + t


def _cost(seconds: float, tier: QualityTier) -> float:
    """粗算影片成本（不含重拍）。真正的估算要等分鏡完成。"""
    return seconds * float(config_loader.veo_tier(tier.value)["per_second_usd"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("script_path")
    args = ap.parse_args()

    store = project_store.get_store()
    state = store.load(args.project_id)
    if not state or not state.screenplay:
        print(f"找不到專案「{args.project_id}」或它還沒解析劇本。")
        return 1

    raw = Path(args.script_path).read_text(encoding="utf-8")
    sp = state.screenplay

    print(f"《{sp.title}》")
    print(
        f"  {len(sp.scenes)} 場 · "
        f"{sp.total_estimated_seconds / 60:.1f} 分鐘"
    )
    print()

    # 先看章節偵測（零成本）
    marks = segmenter.detect_chapters(raw)
    print(f"章節偵測（純字串比對，$0）：找到 {len(marks)} 個標記")
    for m in marks[:10]:
        print(f"  第 {m.line_number:>5} 行  {m.title[:52]}")
    print()

    print("產生分段方案中……")
    options, usage = segmenter.build_options(sp, raw)
    print(f"用量：${usage['cost_usd']}（{len(usage['calls'])} 次 LLM 呼叫）")
    print()

    lite = QualityTier.LITE_720P
    fast = QualityTier.FAST_720P

    # ---- 方案總覽 ----
    print("分段方案（給全貌，用按鈕選）")
    hdr = (
        "  " + _lp("方案", 26) + _rp("段數", 6) + _rp("平均長度", 11)
        + _rp("單段 Lite", 11) + _rp("單段 Fast", 11) + _rp("全部做完", 11)
        + _rp("可獨立", 8)
    )
    print(hdr)
    print("-" * _w(hdr))
    for p in options.options:
        mark = "★ " if p.plan_id == options.recommended_plan_id else "  "
        print(
            mark + _lp(p.label, 26)
            + _rp(str(p.segment_count), 6)
            + _rp(f"{p.average_seconds / 60:.1f} 分", 11)
            + _rp(f"${_cost(p.average_seconds, lite):.0f}", 11)
            + _rp(f"${_cost(p.average_seconds, fast):.0f}", 11)
            + _rp(f"${_cost(p.total_seconds, lite):.0f}", 11)
            + _rp(f"{p.self_contained_count}/{p.segment_count}", 8)
        )
    print()
    print("  ★ = 建議")
    print(f"  {options.recommendation_reason}")
    print()

    # ---- 推薦方案的逐段明細 ----
    rec = options.plan(options.recommended_plan_id) if options.recommended_plan_id else None
    if rec:
        print(f"「{rec.label}」逐段明細")
        hdr = (
            "  " + _rp("#", 4) + _lp("標題", 30) + _rp("場次", 10)
            + _rp("長度", 8) + _rp("Lite", 8) + _rp("獨立", 6)
            + "  " + _lp("內容", 34)
        )
        print(hdr)
        print("-" * _w(hdr))
        for s in rec.segments:
            scenes = (
                f"{s.scene_numbers[0]}–{s.scene_numbers[-1]}"
                if len(s.scene_numbers) > 1
                else str(s.scene_numbers[0])
            )
            print(
                "  " + _rp(str(s.order), 4)
                + _lp(s.title[:26], 30)
                + _rp(scenes, 10)
                + _rp(f"{s.estimated_seconds / 60:.1f} 分", 8)
                + _rp(f"${_cost(s.estimated_seconds, lite):.0f}", 8)
                + _rp("是" if s.is_self_contained else "否", 6)
                + "  " + _lp(s.synopsis[:30], 34)
            )
        print()

        first = rec.segments[0]
        print("先拍一段看效果的話：")
        print(
            f"  第 1 段「{first.title}」· {first.estimated_seconds / 60:.1f} 分鐘 · "
            f"Lite ${_cost(first.estimated_seconds, lite):.2f} / "
            f"Fast ${_cost(first.estimated_seconds, fast):.2f}"
        )
        print(
            f"  滿意再拍第 2 段 —— 角色長相、風格、畫質都沿用，不用重選。"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
