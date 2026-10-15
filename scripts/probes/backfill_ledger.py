"""把已經跑完的真實生成回填進 ClickHouse ledger。

為什麼需要這支：
    ledger.record_generation() 在接線之前，全專案只有 seed_ledger.py
    （模擬資料）呼叫過。真實的批次生成一次都沒寫 ——
    第一章 12 鏡花了 $5.4，哪些約束套用了、重拍幾次，全部只留在專案 JSON 裡。

    那些是**真實資料**，而且是目前唯一的真實資料。丟掉太可惜。

誠實聲明（這支腳本最重要的一段）：

    回填的 outcome 與鏡頭特徵是**純事實** —— 直接從專案狀態讀，沒有推測。

    但 `predicted_success_rate` 是**現在重算的**，不是生成當下記下來的
    （當時根本沒有這個欄位）。而 priors.yaml 與 constraints.yaml 在
     那一輪是**看著這些鏡頭的失敗結果調過的**。
    所以拿這些列去看校準報告，等於拿答案回去改考卷 ——
    它會顯得比真實情況準。

    因此回填的 plan_id 一律帶 `#backfill` 後綴，
    要做校準分析時可以用 `plan_id NOT LIKE '%#backfill'` 排除。
    成功率統計（特徵 → 結果）不受影響，那部分是乾淨的。

    從今以後不需要這支腳本 —— routes/video.py 已經會在裁決當下寫入。
       這是一次性的補課。

用法：
    ./.venv/Scripts/python.exe scripts/probes/backfill_ledger.py shensi-001
    ./.venv/Scripts/python.exe scripts/probes/backfill_ledger.py shensi-001 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.schemas.plan import QualityTier  # noqa: E402
from app.schemas.segment import PendingLedgerFacts  # noqa: E402
from app.services import (  # noqa: E402
    constraint_engine,
    env_boot,
    ledger,
    project_store,
    prompt_export,
    success_predictor,
)

env_boot.load()

BACKFILL_SUFFIX = "#backfill"


def _tier_enum(tier: str) -> QualityTier:
    try:
        return QualityTier(tier)
    except ValueError:
        return QualityTier.LITE_720P


def _facts_for(state, segment, shot, attempt: int, tier: str, cost: float, prompt: str):
    """重建一次生成的事實。

    預測值用**空歷史**重算，理由是：這些鏡頭生成的當下 ledger 就是空的，
       所以純先驗正是系統當時會報出來的數字。用現在已經回填一半的 ledger
       去算，反而會讓越後面的鏡頭吃到越多前面鏡頭的結果 —— 那是假的。
    """
    estimate = success_predictor.predict_shot(
        shot,
        _tier_enum(tier),
        style_id=state.style_id or "",
        aspect_ratio=segment.shot_list.aspect_ratio,
        observed_rate=None,
        sample_size=0,
    )
    rules = [
        c.rule_id
        for c in constraint_engine.plan_for_shot(shot, state.style_id or "").applied
    ]
    return PendingLedgerFacts(
        attempt=attempt,
        predicted_success_rate=estimate.success_rate,
        applied_constraints=rules,
        model_id="",  # 當時沒記，不編一個上去
        latency_seconds=0,
        cost_usd=cost,
        has_first_frame=bool(shot.first_frame_from),
        prompt_excerpt=prompt[:500],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("--dry-run", action="store_true", help="只印要寫什麼，不寫入")
    args = ap.parse_args()

    if not args.dry_run and not ledger.is_available():
        print("X ClickHouse 連不上。先起容器：")
        print("  docker compose -f deployment/clickhouse/docker-compose.yml up -d")
        return 1

    state = project_store.get_store().load(args.project_id)
    if state is None:
        print(f"X 找不到專案 {args.project_id}")
        return 1

    print(f"專案 {args.project_id} · 風格 {state.style_id} · {len(state.segments)} 段")
    print()

    written = 0
    for segment in state.segments:
        if not segment.clips or not segment.shot_list:
            continue

        plan_id = f"{state.project_id}:{segment.segment_id}{BACKFILL_SUFFIX}"
        print(f"[第 {segment.order} 段] {segment.title} — {len(segment.clips)} 鏡")

        for clip in segment.clips:
            shot = next(
                (s for s in segment.shot_list.shots if s.shot_id == clip.shot_id), None
            )
            if shot is None or not clip.uri:
                print(f"  - {clip.shot_id} 跳過（沒有鏡頭定義或沒生成）")
                continue

            prompt = prompt_export.build_prompt(shot, segment.shot_list, state.casting)[
                "full"
            ]

            # attempts=N 代表 N-1 次被打回來、第 N 次通過。
            #    只寫最後那筆通過的話，一次過關率會變成 100% ——
            #    而真相是 12 鏡裡有 2 鏡沒有一次過。
            #    那 2 鏡正是最有學習價值的資料。
            attempts = max(1, clip.attempts)
            per_attempt = round(clip.cost_usd / attempts, 3) if attempts else 0.0

            for attempt in range(1, attempts + 1):
                is_last = attempt == attempts
                outcome = "accepted" if is_last else "rejected"
                facts = _facts_for(
                    state, segment, shot, attempt, clip.tier, per_attempt, prompt
                )

                if args.dry_run:
                    print(
                        f"  - {clip.shot_id} 第 {attempt}/{attempts} 次 → {outcome}"
                        f" · 預測 {facts.predicted_success_rate:.0%}"
                        f" · {len(facts.applied_constraints)} 條約束 · ${per_attempt}"
                    )
                    written += 1
                    continue

                ok = ledger.record_adjudicated(
                    plan_id=plan_id,
                    shot=shot,
                    facts=facts,
                    style_id=state.style_id or "",
                    tier=clip.tier or "",
                    outcome=outcome,
                    rejection_reason="" if is_last else "回填：重拍過，原因未逐次記錄",
                    output_uri=clip.uri if is_last else "",
                )
                if ok:
                    written += 1
                else:
                    print(f"  X {clip.shot_id} 第 {attempt} 次寫入失敗")

            mark = "一次過" if attempts == 1 else f"第 {attempts} 次才過"
            print(f"  OK {clip.shot_id} {mark}")

    print()
    print(f"{'（乾跑）' if args.dry_run else ''}共 {written} 筆")
    if not args.dry_run:
        print()
        # 不用 emoji：Windows console 是 cp950，會直接拋 UnicodeEncodeError
        # 把整支腳本的最後一行炸掉（寫入其實已經完成，看起來卻像失敗了）。
        print("[注意] 這些列的 plan_id 帶 #backfill —— 做校準分析時記得排除，")
        print("       它們的預測值是事後重算的，不是生成當下記下來的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
