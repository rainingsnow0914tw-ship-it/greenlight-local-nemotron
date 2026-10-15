"""往 ClickHouse 灌一批模擬紀錄，驗證 ledger 的查詢邏輯。

這是**模擬資料，不是實測資料**。用途只有一個：
   在還沒花錢跑 Veo 之前，驗證「先驗 → 實測」的切換邏輯是對的。

這批資料絕對不能出現在 demo 或成果展示裡。
   影片裡的成功率數字必須來自真實跑過的生成 ——
   拿模擬資料當實測結果，就是我們自己在 PROJECT_GENESIS 裡
   罵過的「假裝有資料」。

   驗證完之後用 --clear 清掉，正式資料從第一次真實生成開始累積。

用法：
    # 先起 ClickHouse
    docker compose -f deployment/clickhouse/docker-compose.yml up -d

    python scripts/probes/seed_ledger.py           # 灌 400 筆
    python scripts/probes/seed_ledger.py --clear   # 清空
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

from app.schemas.plan import QualityTier  # noqa: E402
from app.schemas.shot import CameraMove, ContentFlag, Shot, ShotType  # noqa: E402
from app.services import constraint_engine, ledger, success_predictor  # noqa: E402

# 固定種子 —— 每次跑出同一批資料，這樣「查詢結果變了」一定是 code 改壞了，
# 不會是資料抖動。除錯時這件事很重要。
_RNG = random.Random(20260807)

_STYLES = ["photoreal", "anime", "animated_3d", "stop_motion", "painterly"]
_TIERS = [
    QualityTier.LITE_720P,
    QualityTier.FAST_720P,
    QualityTier.STANDARD_1080P,
]
_SHOT_TYPES = list(ShotType)
_CAMERA_MOVES = list(CameraMove)
_FLAG_POOL = list(ContentFlag)

_REJECTION_REASONS = [
    "character_drift",      # 角色長相跑掉
    "anatomy_error",        # 手指、肢體錯誤
    "unwanted_object",      # 憑空出現的物件
    "text_garbled",         # 畫面文字亂碼
    "motion_artifact",      # 動態變形
    "composition_off",      # 構圖不對（例如螢幕背對鏡頭）
]


def _random_shot(index: int) -> Shot:
    """造一個特徵隨機但合理的鏡頭。"""
    character_count = _RNG.choices([0, 1, 2, 3, 4], weights=[15, 40, 28, 12, 5])[0]
    flags = _RNG.sample(_FLAG_POOL, k=_RNG.choices([0, 1, 2], weights=[50, 35, 15])[0])

    return Shot(
        shot_id=f"seed_{index:04d}",
        scene_number=index // 6 + 1,
        order_in_scene=index % 6 + 1,
        base_duration_seconds=_RNG.choice([4, 6, 8]),
        extensions=_RNG.choices([0, 1, 2], weights=[70, 22, 8])[0],
        shot_type=_RNG.choice(_SHOT_TYPES),
        camera_move=_RNG.choice(_CAMERA_MOVES),
        description=f"模擬鏡頭 {index}",
        characters_in_frame=[f"角色{i}" for i in range(character_count)],
        dialogue="模擬台詞" if _RNG.random() < 0.35 else None,
        reference_image_uri=(
            "gs://seed/ref.png" if character_count and _RNG.random() < 0.7 else None
        ),
        first_frame_from=f"seed_{index - 1:04d}" if _RNG.random() < 0.4 else None,
        content_flags=flags,
    )


def _simulate_outcome(predicted_rate: float) -> tuple[str, str]:
    """依預測成功率擲一次骰子，決定這次生成的結果。

    刻意讓實際通過率比預測值略低（×0.92）。

    為什麼要這樣造：
        這批 seed 的用途是驗證「校準報告能不能抓出系統偏樂觀」。
        如果模擬結果剛好等於預測值，calibration_report 會回報零誤差，
        我們就無法確認那個報告真的在算東西。
        故意造一個已知的偏差，跑完檢查報告有沒有抓到 —— 這才叫驗證。

    另外 4% 的機率回 error（API 故障），用來驗證
    「error 不進成功率統計」這條規則有沒有生效。
    """
    if _RNG.random() < 0.04:
        return "error", ""

    if _RNG.random() < predicted_rate * 0.92:
        return "accepted", ""
    return "rejected", _RNG.choice(_REJECTION_REASONS)


def seed(count: int) -> int:
    if not ledger.is_available():
        print("ClickHouse 連不上。先起容器：")
        print(
            "  docker compose -f deployment/clickhouse/docker-compose.yml up -d"
        )
        return 1

    written = 0
    for i in range(count):
        shot = _random_shot(i)
        style_id = _RNG.choice(_STYLES)
        tier = _RNG.choice(_TIERS)

        # 用系統自己的預測器算出當時的預測值 ——
        # 這樣校準報告比較的才是真的「我們的預測」對上「實際結果」。
        estimate = success_predictor.predict_shot(shot, tier, style_id=style_id)
        cplan = constraint_engine.plan_for_shot(shot, style_id)

        outcome, reason = _simulate_outcome(estimate.success_rate)

        ok = ledger.record_generation(
            ledger.GenerationRecord(
                plan_id=f"seed-plan-{i // 12:03d}",
                shot_id=shot.shot_id,
                attempt=1,
                style_id=style_id,
                tier=tier.value,
                shot_type=shot.shot_type.value,
                camera_move=shot.camera_move.value,
                character_count=len(shot.characters_in_frame),
                base_duration_seconds=shot.base_duration_seconds,
                extensions=shot.extensions,
                has_dialogue=bool(shot.dialogue),
                has_reference_image=bool(shot.reference_image_uri),
                has_first_frame=bool(shot.first_frame_from),
                content_flags=[f.value for f in shot.content_flags],
                applied_constraints=[c.rule_id for c in cplan.applied],
                predicted_success_rate=estimate.success_rate,
                outcome=outcome,
                rejection_reason=reason,
                cost_usd=round(shot.total_seconds * 0.10, 3),
                latency_seconds=_RNG.randint(40, 180),
                model_id="seed-simulation",
                prompt_excerpt="（模擬資料，非真實提示詞）",
            )
        )
        written += int(ok)

    return 0 if written else 1


def clear() -> int:
    client = ledger._get_client()  # noqa: SLF001 - 維運腳本，直接用內部連線
    if client is None:
        print("ClickHouse 連不上。")
        return 1
    client.command("TRUNCATE TABLE IF EXISTS greenlight.generation_ledger")
    client.command("TRUNCATE TABLE IF EXISTS greenlight.constraint_stats")
    client.command("TRUNCATE TABLE IF EXISTS greenlight.calibration_stats")
    print("已清空 generation_ledger / constraint_stats / calibration_stats。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="灌模擬紀錄到 ClickHouse")
    parser.add_argument("--count", type=int, default=400)
    parser.add_argument("--clear", action="store_true", help="清空所有紀錄")
    args = parser.parse_args()

    if args.clear:
        return clear()

    print(f"灌 {args.count} 筆模擬紀錄……（⚠️ 模擬資料，不可用於成果展示）")
    code = seed(args.count)
    if code == 0:
        print(f"完成。跑 scripts/ledger_report.py 看查詢結果。")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
