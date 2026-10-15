"""看系統學到什麼，以及把統計提煉成規律。

三種用法：

    # 只看證據，$0，不呼叫任何模型
    ./.venv/Scripts/python.exe scripts/refine_memory.py anime

    # 提煉（約 $0.001）。樣本不足會拒絕並說明理由
    ./.venv/Scripts/python.exe scripts/refine_memory.py anime --refine

    # 看目前帶著跑的記憶長什麼樣（agent 實際讀到的字串）
    ./.venv/Scripts/python.exe scripts/refine_memory.py anime --show-memory

為什麼「只看證據」要單獨做成一種用法：
   提煉會花錢，而且會覆蓋掉現有的規律筆記。先看一眼證據，
   確認有東西值得提煉再動手 —— 這跟整個產品的成本階梯是同一個道理。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services import env_boot, ledger, refinery  # noqa: E402

env_boot.load()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("style_id", help="風格 id，例如 anime / photoreal")
    ap.add_argument("--refine", action="store_true", help="真的提煉（會花錢、會覆蓋）")
    ap.add_argument("--show-memory", action="store_true", help="印出 agent 實際帶著跑的字串")
    args = ap.parse_args()

    if args.show_memory:
        block = refinery.memory_block(args.style_id)
        if not block:
            print("（目前沒有記憶 —— agent 會照原本的方式跑）")
        else:
            print(block)
        return 0

    if not ledger.is_available():
        print("X ClickHouse 連不上。先起容器：")
        print("  docker compose -f deployment/clickhouse/docker-compose.yml up -d")
        print()
        print("  連得上但仍失敗的話檢查 .env 有沒有 CLICKHOUSE_USER / PASSWORD / DB")
        print("  —— 少了它們密碼會預設空字串，認證失敗但只 log 一行 info。")
        return 1

    evidence = refinery.gather_evidence(args.style_id)
    if not evidence.get("available"):
        print(f"X 蒐證失敗：{evidence.get('reason')}")
        return 1

    print(f"=== {args.style_id} 風格的實測證據（$0）===")
    print()
    print(refinery._format_evidence(evidence))
    print()

    current = refinery.load_notes(args.style_id)
    if current:
        print(f"--- 現有規律筆記（{current.sample_size} 筆、{current.refined_at[:10]}）---")
        print(current.notes)
        print()

    if not args.refine:
        n = evidence["overall"].get("first_tries", 0)
        if n < refinery.MIN_SAMPLES_TO_REFINE:
            print(
                f"[樣本] {n} / {refinery.MIN_SAMPLES_TO_REFINE} —— 還不夠提煉。"
                "\n       繼續生成鏡頭，資料會自己累積（每次裁決寫一筆）。"
            )
        else:
            print("[可以提煉] 加 --refine 執行（約 $0.001）")
        return 0

    print("--- 提煉中 ---")
    result = refinery.refine(args.style_id)
    if not result["ok"]:
        print(f"X 沒有提煉：{result['reason']}")
        return 1

    print(f"OK 提煉完成 · {result['chars']} 字 · 來自 {result['sample_size']} 筆 · ${result['cost_usd']}")
    print()
    print(result["notes"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
