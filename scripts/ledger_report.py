"""驗證 ClickHouse 查詢層：先驗 vs 實測、校準誤差、約束觀察值。

這支腳本回答三個問題：
    1. 有資料之後，成功率預估真的會從先驗切換到實測嗎？
    2. 我們的預測準不準？（校準報告）
    3. 每條約束被套用時的實際通過率是多少？

用法：
    docker compose -f deployment/clickhouse/docker-compose.yml up -d
    python scripts/probes/seed_ledger.py
    python scripts/ledger_report.py
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.schemas.plan import EstimateConfidence, QualityTier  # noqa: E402
from app.schemas.shot import CameraMove, Shot, ShotType  # noqa: E402
from app.services import ledger, success_predictor  # noqa: E402


def _w(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _lpad(text: str, width: int) -> str:
    return text + " " * max(0, width - _w(text))


def _rpad(text: str, width: int) -> str:
    return " " * max(0, width - _w(text)) + text


_CONFIDENCE_LABEL = {
    EstimateConfidence.PRIOR_ONLY: "先驗估計",
    EstimateConfidence.BLENDED: "部分實測",
    EstimateConfidence.MEASURED: "實測統計",
}


def main() -> int:
    if not ledger.is_available():
        print("ClickHouse 連不上 —— 系統會走降級模式，全程使用先驗值。")
        print("要驗證查詢層的話先起容器：")
        print("  docker compose -f deployment/clickhouse/docker-compose.yml up -d")
        print("  python scripts/probes/seed_ledger.py")
        return 1

    print("ClickHouse：已連線\n")

    # ── 1. 先驗 vs 實測 ───────────────────────────────────────────────────
    probe = Shot(
        shot_id="probe_001",
        scene_number=1,
        order_in_scene=1,
        base_duration_seconds=8,
        shot_type=ShotType.MEDIUM,
        camera_move=CameraMove.STATIC,
        description="測試用鏡頭：單人中景、靜態運鏡",
        characters_in_frame=["主角"],
        reference_image_uri="gs://probe/ref.png",
    )
    tier = QualityTier.FAST_720P

    print("一、同一個鏡頭，不同風格下的預估來源")
    print(f"   （{probe.description}）\n")

    header = (
        "  "
        + _lpad("風格", 14)
        + _rpad("預估成功率", 13)
        + _rpad("樣本數", 9)
        + _rpad("資料來源", 11)
        + "   "
        + _lpad("匹配層級", 40)
    )
    print(header)
    print("-" * _w(header))

    for style_id in ["photoreal", "anime", "animated_3d", "stop_motion", "painterly"]:
        sim = ledger.query_similar_shots(probe, tier, style_id)
        est = success_predictor.predict_shot(
            probe,
            tier,
            style_id=style_id,
            observed_rate=sim.observed_rate,
            sample_size=sim.sample_size,
        )
        print(
            "  "
            + _lpad(style_id, 14)
            + _rpad(f"{est.success_rate * 100:.0f}%", 13)
            + _rpad(str(sim.sample_size), 9)
            + _rpad(_CONFIDENCE_LABEL[est.confidence], 11)
            + "   "
            + _lpad(sim.match_description, 40)
        )
    print()

    # ── 2. 校準報告 ──────────────────────────────────────────────────────
    print("二、校準報告：我們預測的準不準")
    print("   （理想狀態：預測 30–40% 的那一列，實際也該接近 35%）\n")

    report = ledger.calibration_report()
    if not report:
        print("  尚無足夠資料。\n")
    else:
        cal_header = (
            "  "
            + _lpad("預測區間", 14)
            + _rpad("實際通過率", 13)
            + _rpad("誤差", 10)
            + _rpad("樣本數", 9)
        )
        print(cal_header)
        print("-" * _w(cal_header))
        total_weighted_error = 0.0
        total_samples = 0
        for row in report:
            print(
                "  "
                + _lpad(row["predicted_range"], 14)
                + _rpad(f"{row['actual_rate'] * 100:.0f}%", 13)
                + _rpad(f"{row['error'] * 100:+.0f}pt", 10)
                + _rpad(str(row["sample_size"]), 9)
            )
            total_weighted_error += row["error"] * row["sample_size"]
            total_samples += row["sample_size"]
        print()
        if total_samples:
            bias = total_weighted_error / total_samples
            direction = "偏樂觀" if bias < 0 else "偏保守"
            print(
                f"  整體偏差 {bias * 100:+.1f}pt（{direction}）"
                f"—— 系統性偏差超過 5pt 就該回頭調 priors.yaml 的 base_success_rate。"
            )
        print()

    # ── 3. 約束觀察值 ────────────────────────────────────────────────────
    print("三、約束規則的觀察通過率")
    print("   ⚠️ 這不是因果效益 —— 約束是依鏡頭難度自動觸發的，存在選擇偏誤。")
    print("      要得到真正的效益需要 A/B 隨機停用，本版尚未實作。\n")

    rule_ids = [
        "hand_anatomy",
        "screen_facing_camera",
        "no_morphing",
        "exclude_unspecified_props",
        "extension_drift_guard",
    ]
    rule_header = (
        "  " + _lpad("規則", 28) + _rpad("觀察通過率", 13) + _rpad("樣本數", 9)
    )
    print(rule_header)
    print("-" * _w(rule_header))
    for rule_id in rule_ids:
        obs = ledger.constraint_observations(rule_id)
        if not obs.get("available") or not obs.get("sample_size"):
            print("  " + _lpad(rule_id, 28) + _rpad("—", 13) + _rpad("0", 9))
            continue
        print(
            "  "
            + _lpad(rule_id, 28)
            + _rpad(f"{obs['observed_rate'] * 100:.0f}%", 13)
            + _rpad(str(obs["sample_size"]), 9)
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
