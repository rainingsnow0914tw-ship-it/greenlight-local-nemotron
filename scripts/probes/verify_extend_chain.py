"""連續續拍的漂移實驗 —— 「一直往後接 7 秒」到底能接幾次。

這支腳本要回答三個目前**沒有證據**的問題：

    1. 續拍幾次之後畫面才真的崩？
       `constraints.yaml` 的 extension_drift_guard 說「超過兩次建議拆鏡」，
       `base.yaml` 的分鏡規則也照抄了這句 —— 但那是沿用業界說法，
       我們一次都沒實測過。**我們正在用一條沒有證據的規則，
       勸使用者不要用他最想要的長鏡頭功能。**

    2. `priors.yaml` 的 per_extension_penalty（-0.07）準不準？

    3. **Extend 怎麼計費？**（目前完全未知）
       連續續拍會在帳單上留下清楚的階梯：
         按新增秒數 → 每次都是固定的 7 秒錢
         按輸出總長 → 每次越來越貴
       跑完之後對一下 Cloud Billing 就知道是哪一種。

用法：
    python scripts/verify_extend_chain.py                 # 4 秒起，續 3 次
    python scripts/verify_extend_chain.py --rounds 6      # 推到極限
    python scripts/verify_extend_chain.py --tier fast_720p

每一輪都會存檔（extend_chain_00.mp4, _01, _02…），
   這樣可以逐段比對漂移，而不是只看最後那支「已經爛掉」的成品。
   漂移是**漸進**的，只看最後一支說不出它是第幾次開始壞的。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

from app.services import env_boot  # noqa: E402

env_boot.load()

from app.services import config_loader, video_gen  # noqa: E402

OUT_DIR = _ROOT / "outputs"

# 刻意用「單一主體 + 緩慢動作 + 有臉」的鏡頭：
# 臉是漂移最看得出來的地方（角色一致性正是這個產品的核心賣點），
# 緩慢動作讓畫面內容不會自然改變太多，漂移才歸因得清楚。
BASE_PROMPT = (
    "A young woman with shoulder-length dark hair sits by a window in a quiet room, "
    "soft daylight on her face, looking down at a book. She turns a page slowly. "
    "Static locked-off camera, medium close-up."
)
EXTEND_PROMPT = (
    "She continues reading, turning another page. Same person, same clothing, "
    "same room, same lighting. Static locked-off camera."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="連續續拍的漂移實驗")
    parser.add_argument("--rounds", type=int, default=3, help="續拍幾次")
    parser.add_argument("--tier", default="lite_720p")
    parser.add_argument("--base-seconds", type=int, default=4, choices=[4, 6, 8])
    parser.add_argument("--aspect", default="16:9")
    parser.add_argument("--yes", action="store_true", help="跳過花費確認")
    args = parser.parse_args()

    tier_cfg = config_loader.veo_tier(args.tier)
    per_second = float(tier_cfg.get("per_second_usd", 0.05))
    add_seconds = int(
        config_loader.pricing().get("veo", {}).get("extend", {}).get(
            "seconds_per_extension", 7
        )
    )

    # 兩種計費假設各算一次 —— 跑完對帳單就知道是哪一種
    lengths = [args.base_seconds]
    for _ in range(args.rounds):
        lengths.append(lengths[-1] + add_seconds)

    cost_per_new = args.base_seconds * per_second + args.rounds * add_seconds * per_second
    cost_per_total = sum(length * per_second for length in lengths)

    print("連續續拍漂移實驗")
    print(f"檔位 {tier_cfg.get('label', args.tier)} · ${per_second}/秒")
    print(f"長度階梯：{' → '.join(f'{n}s' for n in lengths)}")
    print()
    print(f"  若按新增秒數計費：約 ${cost_per_new:.2f}")
    print(f"  若按輸出總長計費：約 ${cost_per_total:.2f}")
    print(f"  🔴 這個價差本身就是計費方式的答案 —— 跑完對 Cloud Billing。")
    print()

    if not args.yes:
        input("按 Enter 開始，Ctrl+C 取消 ")

    OUT_DIR.mkdir(exist_ok=True)
    project_id = "_extend_chain"
    history: list[dict] = []

    # ---- 第 0 步：基底 ----
    print(f"\n[0] 生成基底 {args.base_seconds} 秒…")
    try:
        result = video_gen.generate_clip(
            project_id,
            "extend_chain_00",
            BASE_PROMPT,
            tier=args.tier,
            aspect_ratio=args.aspect,
            duration_seconds=args.base_seconds,
        )
    except video_gen.VideoGenError as exc:
        print(f"  基底生成失敗：{exc}")
        return 1

    data = _read(result.uri)
    _save(data, 0)
    history.append({"round": 0, "seconds": result.seconds, "size": result.size_bytes})
    print(f"  {result.seconds} 秒 · {result.width}×{result.height} · "
          f"{result.size_bytes / 1024:.0f} KB")

    # ---- 逐次續拍 ----
    for i in range(1, args.rounds + 1):
        print(f"\n[{i}] 續拍第 {i} 次（+{add_seconds} 秒）…")
        try:
            result = video_gen.extend_clip(
                project_id,
                f"extend_chain_{i:02d}",
                data,
                EXTEND_PROMPT,
                tier=args.tier,
                seconds=add_seconds,
            )
        except video_gen.VideoGenError as exc:
            print(f"  第 {i} 次續拍失敗：{exc}")
            print(f"  🔴 這本身就是答案：續拍在第 {i} 次就不行了。")
            break

        data = _read(result.uri)
        _save(data, i)
        history.append(
            {"round": i, "seconds": result.seconds, "size": result.size_bytes}
        )
        print(f"  {result.seconds} 秒 · {result.width}×{result.height} · "
              f"{result.size_bytes / 1024:.0f} KB")
        for w in result.warnings:
            print(f"  ⚠️  {w}")

    # ---- 報告 ----
    print("\n" + "=" * 68)
    print("輪次   實際長度   增加    檔案")
    prev = 0.0
    for h in history:
        delta = h["seconds"] - prev if prev else 0.0
        prev = h["seconds"]
        print(
            f"  {h['round']}    {h['seconds']:>6.2f}s   "
            f"{('+' + format(delta, '.2f') + 's') if delta else '  —   '}   "
            f"extend_chain_{h['round']:02d}.mp4"
        )

    print()
    print("🔴 接下來要用眼睛看，這部分沒有辦法自動化：")
    print("   逐段打開 outputs/extend_chain_*.mp4，找出**第幾支開始**出現")
    print("   臉部變形／光線飄移／背景物件消失／衣服顏色改變。")
    print("   那個數字就是 constraints.yaml 裡『超過幾次要拆鏡』該填的值。")
    print()
    print("   如果到最後一支都還好 —— 那更值得知道，")
    print("   代表我們現在的規則太保守，白白擋掉了使用者最想要的長鏡頭。")
    return 0


def _read(uri: str) -> bytes:
    from app.services import media_store

    loaded = media_store.load(uri)
    if not loaded:
        raise RuntimeError(f"讀不回剛存的檔案：{uri}")
    return loaded[0]


def _save(data: bytes, index: int) -> None:
    """另存一份到 outputs/ 方便逐段比對。

    media_store 那份是給系統用的，這份是給人看的 ——
    放在專案目錄裡才好一支一支點開來比。
    """
    (OUT_DIR / f"extend_chain_{index:02d}.mp4").write_bytes(data)


if __name__ == "__main__":
    raise SystemExit(main())
