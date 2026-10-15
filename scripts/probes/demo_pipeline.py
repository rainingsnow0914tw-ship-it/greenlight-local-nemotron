"""完整流程示範：狀態機 → 持久化 → 提示詞導出。零成本。

驗證四件事：
    1. 閘門會擋住 —— 沒選完角色不能出提示詞、沒看提示詞不能試拍
    2. 狀態存得住 —— 存檔後重新載入，模擬「關掉瀏覽器隔天回來」
    3. 提示詞出得來 —— 三種格式，可以拿去別的工具用
    4. 回退會警告 —— 改風格會讓已選定的角色失效，系統要誠實說出代價

用法：
    python scripts/probes/demo_pipeline.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT / "scripts"))

from app.schemas.casting import (  # noqa: E402
    CastingGate,
    CastingSession,
    CharacterCandidate,
    CharacterKind,
)
from app.schemas.plan import QualityTier  # noqa: E402
from app.schemas.project import ProjectState, Stage  # noqa: E402
from app.services import (  # noqa: E402
    casting,
    cost_engine,
    project_store,
    prompt_export,
)
from demo_plan import build_demo_shotlist  # noqa: E402


def _section(title: str) -> None:
    print()
    print(title)
    print("-" * 68)


def main() -> int:
    # 用暫存目錄，不要污染使用者的 ~/.greenlight
    with tempfile.TemporaryDirectory() as tmpdir:
        project_store.set_store(project_store.LocalJsonStore(tmpdir))
        store = project_store.get_store()

        # ── 建專案，走到分鏡完成 ─────────────────────────────────────────
        shot_list = build_demo_shotlist()
        state = ProjectState(project_id="demo-001", title=shot_list.screenplay_title)
        state.stage = Stage.SHOTS_PLANNED
        state.shot_list = shot_list
        state.plan = cost_engine.build_plan(
            plan_id="demo-001",
            shot_list=shot_list,
            principal_count=2,
            supporting_count=2,
            screenplay_tokens=18_000,
        )

        _section("一、閘門：跳關會被擋下來")
        for target in [Stage.PROMPTS_READY, Stage.TEST_SHOT_PASSED, Stage.GENERATING]:
            blockers = state.blockers_for(target)
            print(f"  想跳到「{target.value}」→ {'、'.join(blockers)}")

        # ── 依序過關 ─────────────────────────────────────────────────────
        _section("二、依序過關")

        state.advance(Stage.LOOK_CHOSEN)
        state.record_decision(
            choice="實拍寫實 · 橫式 16:9",
            rationale="現代都市寫實劇情",
        )
        print(f"  ✓ 定調：{state.stage.value}")

        state.chosen_tier = QualityTier.FAST_720P
        state.advance(Stage.TIER_CHOSEN)
        rec = state.plan.variant(state.chosen_tier)
        state.record_decision(
            choice=f"{rec.tier_label}（${rec.cost.total_usd}）",
            rationale=state.plan.recommendation_reason,
            followed_recommendation=state.chosen_tier == state.plan.recommended_tier,
        )
        print(f"  ✓ 定案：{state.stage.value}")

        # 選角
        gate = CastingGate(plan_id="demo-001")
        for name, kind in [("林曉薇", CharacterKind.HUMAN), ("陳志遠", CharacterKind.HUMAN)]:
            session = CastingSession(character_name=name, kind=kind, is_principal=True)
            casting.add_round(
                session,
                [
                    CharacterCandidate(
                        candidate_id=f"{name}_r1_c{i}",
                        character_name=name,
                        round_number=1,
                        interpretation=f"{name} 詮釋 {i}",
                        cost_usd=0.02,
                    )
                    for i in (1, 2, 3)
                ],
                direction_id="initial",
            )
            gate.sessions.append(session)
        state.casting = gate

        print(f"  ✗ 選角未完成 → {'、'.join(state.blockers_for(Stage.CAST_LOCKED))}")

        for session in gate.sessions:
            casting.select(session, f"{session.character_name}_r1_c1")
        state.spend.images_usd = gate.total_spent_usd
        state.advance(Stage.CAST_LOCKED)
        print(f"  ✓ 選角：{state.stage.value}（花費 ${gate.total_spent_usd:.2f}）")

        # ── 提示詞關卡 ───────────────────────────────────────────────────
        _section("三、提示詞導出（關卡）")

        print(f"  試拍前檢查 → {'、'.join(state.blockers_for(Stage.TEST_SHOT_PASSED))}")
        print()

        text = prompt_export.export_plain_text(shot_list, gate)
        md = prompt_export.export_markdown(shot_list, gate)
        js = prompt_export.export_json(shot_list, gate)

        out_dir = Path(tmpdir) / "exports"
        out_dir.mkdir()
        (out_dir / "prompts.txt").write_text(text, encoding="utf-8")
        (out_dir / "prompts.md").write_text(md, encoding="utf-8")
        (out_dir / "prompts.json").write_text(js, encoding="utf-8")

        print(f"  純文字 {len(text):>6,} 字元 —— 貼到任何影片生成工具都能用")
        print(f"  Markdown {len(md):>4,} 字元 —— 含每條約束的原理說明")
        print(f"  JSON   {len(js):>6,} 字元 —— 保留分段結構，可程式化串接")
        print()

        sample = prompt_export.build_prompt(shot_list.shots[3], shot_list, gate)
        print(f"  範例（{sample['shot_id']}）：")
        print()
        for line in _wrap(sample["full"], 64):
            print(f"    {line}")
        print()
        print("  系統自動加上的物理約束：")
        for c in sample["applied_constraints"]:
            print(f"    · [{c['category']}] {c['rule_id']}")

        state.prompts_exported_at = state.updated_at
        state.advance(Stage.PROMPTS_READY)
        print()
        print(f"  ✓ 提示詞：{state.stage.value}")

        # ── 存檔 → 重新載入 ──────────────────────────────────────────────
        _section("四、關掉瀏覽器，隔天回來")

        store.save(state)
        print(f"  已存檔。專案清單：{store.list_ids()}")

        del state
        reloaded = store.load("demo-001")
        summary = reloaded.progress_summary()
        print()
        print(f"  重新載入：{summary['title']}")
        print(
            f"  目前在第 {summary['step']}/{summary['total_steps']} 步 —— "
            f"{summary['stage_label']}（{summary['stage_description']}）"
        )
        print(f"  下一步：{summary['next_stage_label']}")
        print(f"  累計花費：${summary['spent_usd']:.2f}")
        print(f"  鏡頭表還在：{reloaded.shot_list.clip_count} 個鏡頭")
        print(f"  選角結果還在：{len(reloaded.casting.sessions)} 個角色已鎖定")
        print()
        print("  決策歷史：")
        for d in reloaded.decisions:
            mark = "採納建議" if d.followed_recommendation else "自行決定"
            print(f"    · [{d.stage.value}] {d.choice} —— {mark}")

        # ── 回退警告 ─────────────────────────────────────────────────────
        _section("五、想改風格？系統誠實說出代價")

        invalidated = reloaded.rewind_to(Stage.LOOK_CHOSEN)
        print("  回退到「定調」會讓這些失效：")
        for item in invalidated:
            print(f"    · {item}")
        print()
        print("  🔴 只回報後果、不自動清資料 —— 誤刪十輪選角的結果不可接受。")
        print("     實際要不要清由使用者確認後才執行。")

    return 0


def _wrap(text: str, width: int) -> list[str]:
    """簡單斷行，避免長提示詞把終端機撐爆。"""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
