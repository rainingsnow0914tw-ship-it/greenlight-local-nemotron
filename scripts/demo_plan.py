"""零成本示範：跑一次完整的製片方案估算，不呼叫任何 API。

設計理由：
    先把基地做穩、不花錢。
    這支腳本就是那個「穩」的證明 —— 它把 config → schema → 預測 → 估價
    整條鏈路跑一遍，全程不碰網路、不花一毛錢。

    在 $100 存進 Google Cloud 之前，這支腳本應該要能跑出正確的數字。
    跑不出來就代表地基有問題，這時候去接 Veo 只會把問題埋得更深。

用法（在專案根目錄）：
    python scripts/demo_plan.py
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

# Windows 終端機預設是 cp950，直接印中文會變亂碼。
# Windows 上一定會遇到，所以在腳本裡處理掉，
# 不要求使用者自己去改 chcp 或環境變數。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 讓 scripts/ 底下的腳本能 import backend 的模組
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))

from app.schemas.plan import BudgetVerdict, EstimateConfidence, QualityTier  # noqa: E402
from app.schemas.shot import (  # noqa: E402
    CameraMove,
    ContentFlag,
    Shot,
    ShotList,
    ShotType,
)
from app.services import config_loader, constraint_engine, cost_engine  # noqa: E402


def build_demo_shotlist() -> ShotList:
    """手刻一份鏡頭表，模擬一段約 90 秒的短片。

    刻意做出難易差距很大的鏡頭，這樣才看得出成功率預估有沒有在工作：
      - 空鏡（最好生）
      - 單人特寫 + 參考圖（本架構的甜蜜點）
      - 15 秒續拍鏡頭
      - 群眾 + 手持 + 畫面內文字（地獄難度，應該被標成風險鏡頭）
    """
    shots = [
        Shot(
            shot_id="s001_01",
            scene_number=1,
            order_in_scene=1,
            base_duration_seconds=6,
            shot_type=ShotType.EXTREME_WIDE,
            camera_move=CameraMove.STATIC,
            description="清晨的城市天際線，薄霧未散",
            characters_in_frame=[],
            content_flags=[],
        ),
        Shot(
            shot_id="s001_02",
            scene_number=1,
            order_in_scene=2,
            base_duration_seconds=8,
            shot_type=ShotType.CLOSE_UP,
            camera_move=CameraMove.STATIC,
            description="女主角在窗邊，睜開眼睛",
            characters_in_frame=["林曉薇"],
            reference_image_uri="gs://demo/char_linxiaowei.png",
            first_frame_from="s001_01",
        ),
        # 「一段 15 秒」：8 秒基底 + 一次續拍
        Shot(
            shot_id="s002_01",
            scene_number=2,
            order_in_scene=1,
            base_duration_seconds=8,
            extensions=1,
            shot_type=ShotType.MEDIUM,
            camera_move=CameraMove.DOLLY,
            description="她走過長廊，一路上的燈依序亮起",
            characters_in_frame=["林曉薇"],
            reference_image_uri="gs://demo/char_linxiaowei.png",
            dialogue="今天……會不一樣嗎？",
        ),
        Shot(
            shot_id="s003_01",
            scene_number=3,
            order_in_scene=1,
            base_duration_seconds=8,
            shot_type=ShotType.MEDIUM_CLOSE,
            camera_move=CameraMove.PAN,
            description="兩人在咖啡廳對坐，陳志遠把筆電轉向她，氣氛僵持",
            characters_in_frame=["林曉薇", "陳志遠"],
            reference_image_uri="gs://demo/char_linxiaowei.png",
            dialogue="你到底想說什麼。",
            content_flags=[ContentFlag.HANDS_VISIBLE, ContentFlag.SCREEN_IN_FRAME],
        ),
        # 地獄鏡頭：應該被標成風險鏡頭
        Shot(
            shot_id="s004_01",
            scene_number=4,
            order_in_scene=1,
            base_duration_seconds=8,
            extensions=2,
            shot_type=ShotType.WIDE,
            camera_move=CameraMove.HANDHELD,
            description="車站大廳，人群川流，看板上跑著班次資訊",
            characters_in_frame=["林曉薇", "陳志遠", "路人甲", "路人乙"],
            content_flags=[
                ContentFlag.CROWD,
                ContentFlag.TEXT_IN_FRAME,
                ContentFlag.FAST_ACTION,
            ],
        ),
        Shot(
            shot_id="s005_01",
            scene_number=5,
            order_in_scene=1,
            base_duration_seconds=6,
            shot_type=ShotType.INSERT,
            camera_move=CameraMove.STATIC,
            description="桌上一杯冷掉的咖啡，特寫",
            characters_in_frame=[],
        ),
    ]

    return ShotList(
        screenplay_title="《晨霧》（示範用鏡頭表）",
        shots=shots,
        style_id="photoreal",
        planning_notes=[
            "第 2 場拆成單一續拍鏡頭（15 秒），因為走廊移動是連續動作，硬切會斷氣。",
            "第 4 場含群眾與畫面內文字，建議改成不入鏡的拍法。",
        ],
    )


def _display_width(text: str) -> int:
    """算字串在等寬終端機上佔幾格。

    中日韓文字是全形（佔兩格），Python 的 str.ljust 只會數字元數，
    所以混中英文的表格用 f-string 的 :<18 對齊必定歪掉。
    這個函式讓下面的表格能真的對齊。
    """
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def _lpad(text: str, width: int) -> str:
    """靠左對齊，用顯示寬度補空白。"""
    return text + " " * max(0, width - _display_width(text))


def _rpad(text: str, width: int) -> str:
    """靠右對齊，用顯示寬度補空白。"""
    return " " * max(0, width - _display_width(text)) + text


_CONFIDENCE_LABEL = {
    EstimateConfidence.PRIOR_ONLY: "先驗估計",
    EstimateConfidence.BLENDED: "部分實測",
    EstimateConfidence.MEASURED: "實測統計",
}

_VERDICT_MARK = {
    BudgetVerdict.OK: "  ",
    BudgetVerdict.WARN: "! ",
    BudgetVerdict.BLOCK: "!!",
}


def main() -> int:
    # 先驗證設定檔與 schema 有沒有對上 —— 對不上就不要往下跑，
    # 帶著錯誤設定算出來的數字比算不出來更危險。
    problems = config_loader.verify_config_integrity()
    if problems:
        print("設定檔一致性檢查未通過：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("設定檔一致性檢查：通過\n")

    shot_list = build_demo_shotlist()

    print(f"劇本：{shot_list.screenplay_title}")
    print(
        f"鏡頭數 {shot_list.clip_count} · "
        f"成片長度 {shot_list.total_seconds} 秒 · "
        f"Veo 呼叫次數 {shot_list.api_call_count}"
    )
    print()

    plan = cost_engine.build_plan(
        plan_id="demo-001",
        shot_list=shot_list,
        principal_count=2,
        supporting_count=2,
        screenplay_tokens=18_000,
    )

    # ---- 方案比較表 ----
    header = (
        "  "
        + _lpad("檔位", 20)
        + _rpad("標價", 9)
        + _rpad("重拍", 9)
        + _rpad("總計", 10)
        + _rpad("一次過關", 11)
        + _rpad("資料來源", 11)
    )
    print(header)
    print("-" * _display_width(header))
    for v in plan.variants:
        print(
            _VERDICT_MARK[v.budget_verdict]
            + _lpad(v.tier_label, 20)
            + _rpad(f"${v.cost.video_base_usd:.2f}", 9)
            + _rpad(f"${v.cost.retry_expected_usd:.2f}", 9)
            + _rpad(f"${v.cost.total_usd:.2f}", 10)
            + _rpad(f"{v.overall_success_rate * 100:.0f}%", 11)
            + _rpad(_CONFIDENCE_LABEL[v.confidence], 11)
        )
    print()
    print("  !  = 進入預算警戒區    !! = 超過護欄上限")
    print()

    # ---- 風格比較 ----
    #
    # 這一段是產品最反直覺、也最有價值的資訊：
    #    同一份劇本、同一個畫質檔位，換個風格成功率可能差 20 個百分點。
    #    寫實風最難（恐怖谷），繪本風最寬容。
    #    使用者只需要按按鈕切換 —— 不用打任何字（鐵律 7）。
    compare_tier = QualityTier.FAST_720P
    print(f"風格比較（同一份鏡頭表 · 同樣的 {compare_tier.value} 檔位）")
    print()
    style_header = (
        "  "
        + _lpad("風格", 14)
        + _rpad("總計", 10)
        + _rpad("一次過關", 11)
        + _rpad("差距", 9)
        + "   "
        + _lpad("適合", 30)
    )
    print(style_header)
    print("-" * _display_width(style_header))

    baseline_rate = None
    for preset in config_loader.styles().get("presets", []):
        styled_list = shot_list.model_copy(update={"style_id": preset["id"]})
        variant = cost_engine.estimate_variant(
            styled_list,
            compare_tier,
            principal_count=2,
            supporting_count=2,
            screenplay_tokens=18_000,
        )
        if baseline_rate is None:
            baseline_rate = variant.overall_success_rate
        delta = variant.overall_success_rate - baseline_rate
        delta_text = f"{delta * 100:+.0f}pt" if abs(delta) >= 0.01 else "—"
        print(
            "  "
            + _lpad(preset["label"], 14)
            + _rpad(f"${variant.cost.total_usd:.2f}", 10)
            + _rpad(f"{variant.overall_success_rate * 100:.0f}%", 11)
            + _rpad(delta_text, 9)
            + "   "
            + _lpad(preset.get("best_for", ""), 30)
        )
    print()

    # ---- 推薦 ----
    rec = plan.variant(plan.recommended_tier) if plan.recommended_tier else None
    if rec:
        print(f"建議檔位：{rec.tier_label}")
        print(f"  {plan.recommendation_reason}")
        print()

        if plan.test_shot_required:
            print(f"強制試拍：是 —— 建議先拍 {plan.test_shot_candidate}")
            test_shot = next(
                (s for s in shot_list.shots if s.shot_id == plan.test_shot_candidate),
                None,
            )
            if test_shot:
                cfg = config_loader.veo_tier(rec.tier.value)
                test_cost = test_shot.total_seconds * float(cfg["per_second_usd"])
                print(
                    f"  試拍成本 ${test_cost:.2f}"
                    f"（相對於整片 ${rec.cost.total_usd:.2f}）"
                )
            print()

        # ---- 風險鏡頭 ----
        print("風險鏡頭（成功率最低的三個）：")
        for est in rec.riskiest_shots:
            shot = next(s for s in shot_list.shots if s.shot_id == est.shot_id)
            print(
                f"  {est.shot_id}  {est.success_rate * 100:>4.0f}%  "
                f"預期拍 {est.expected_attempts} 次   {shot.description}"
            )
            for risk in est.risk_factors:
                print(f"        · {risk}")
        print()

    # ---- 提示詞邊界 ----
    #
    # 這一段是產品最核心的展示：編劇寫「兩人在咖啡廳對坐，他把筆電轉向她」，
    # 系統自動判斷出這個鏡頭需要哪些物理約束，並說明每一條的原理。
    # 編劇不需要懂這些 —— 這正是要降低的門檻。
    sample = next((s for s in shot_list.shots if s.shot_id == "s003_01"), None)
    if sample:
        cplan = constraint_engine.plan_for_shot(sample)
        print(f"提示詞邊界（以 {sample.shot_id} 為例：{sample.description}）")
        print(
            f"  套用 {len(cplan.applied)} 條約束，"
            f"預估把成功率拉高 {cplan.total_lift * 100:.0f} 個百分點"
        )
        print()
        for c in cplan.applied:
            print(f"  [{c.category_label}] {c.rule_id}  +{c.effective_lift * 100:.0f}%")
            print(f"      注入：{c.inject_text}")
            print(f"      原理：{' '.join(c.principle.split())}")
        if cplan.format_rules:
            print()
            print("  格式規則（改變提示詞寫法本身，不是附加句）：")
            for fr in cplan.format_rules:
                print(f"      {fr.rule_id}：{fr.template}")
        if cplan.skipped:
            print()
            # skipped 是 SkippedConstraint 物件（帶 rule_id / category_label / principle），不是字串
            print(f"  已略過（超過疊加上限）：{', '.join(s.rule_id for s in cplan.skipped)}")
        print()

    # ---- 規劃說明 ----
    if shot_list.planning_notes:
        print("規劃說明：")
        for note in shot_list.planning_notes:
            print(f"  · {note}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
