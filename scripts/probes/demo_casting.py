"""選角關卡示範 —— 零成本，不呼叫 Imagen。

素材取自一份真實劇本。這份劇本剛好同時包含兩種極端：

    Luna  —— 劇本裡有一整段精確的外貌描述（第 371 行那段「AI 視覺描述」），
              系統只要照著生就好，可信度最高。

    深息  —— 一個沒有身體的 AI。他長什麼樣完全是創作決定，沒有標準答案。
              這正是「三張選一、不滿意重抽」最有價值的地方 ——
              系統不該替使用者決定，但也不能叫他打字描述（違反鐵律 7），
              所以給他方向按鈕：更具象／更抽象／更溫暖／更冷冽。

用法：
    python scripts/probes/demo_casting.py
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

from app.schemas.casting import (  # noqa: E402
    CastingGate,
    CastingSession,
    CharacterCandidate,
    CharacterKind,
    InferenceSource,
)
from app.services import casting  # noqa: E402


def _w(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _lpad(text: str, width: int) -> str:
    return text + " " * max(0, width - _w(text))


def _make_candidates(
    name: str, round_no: int, interpretations: list[tuple[str, str]],
    source: InferenceSource, note: str,
) -> list[CharacterCandidate]:
    """造一輪候選。真實流程由 character_designer agent 呼叫 Imagen 產生，
    這裡只是把資料形狀填出來驗證關卡邏輯。"""
    return [
        CharacterCandidate(
            candidate_id=f"{name}_r{round_no}_c{i + 1}",
            character_name=name,
            round_number=round_no,
            interpretation=interp,
            prompt_used=prompt,
            inference_source=source,
            inference_note=note,
            cost_usd=0.02,
        )
        for i, (interp, prompt) in enumerate(interpretations)
    ]


def show_directions(title: str, kind: CharacterKind) -> None:
    print(f"  {title}")
    for d in casting.available_directions(kind):
        print(f"    [{_lpad(d.label['zh'], 10)}] {_lpad(d.label['en'], 12)} {d.hint['zh']}")
    print()


def main() -> int:
    print("選角關卡示範 —— 素材：真實劇本\n")

    # ── Luna：劇本有明確外貌描述 ─────────────────────────────────────────
    luna = CastingSession(
        character_name="Luna",
        kind=CharacterKind.HUMAN,
        is_principal=True,
        source_description=(
            "年輕亞洲女性，黑色長髮及肩略帶自然捲，亮黃色圓領針織毛衣，"
            "深棕色眼睛、單眼皮，笑起來會瞇成一條彎彎的線"
        ),
    )
    casting.add_round(
        luna,
        _make_candidates(
            "Luna",
            1,
            [
                ("忠於描述：亮黃毛衣、及肩捲髮", "young asian woman, ..."),
                ("同樣五官，髮量更蓬鬆", "young asian woman, fuller wavy hair, ..."),
                ("同樣五官，笑容更收斂", "young asian woman, subtle closed smile, ..."),
            ],
            InferenceSource.EXPLICIT_DESCRIPTION,
            "劇本第 371 行的外貌描述",
        ),
        direction_id="initial",
    )

    # ── 深息：AI，沒有身體 ───────────────────────────────────────────────
    shenxi = CastingSession(
        character_name="深息",
        kind=CharacterKind.NON_HUMAN,
        is_principal=True,
        source_description=None,  # 劇本從未描述他的外形
    )
    casting.add_round(
        shenxi,
        _make_candidates(
            "深息",
            1,
            [
                ("光點聚合成的人形輪廓", "luminous humanoid silhouette of data points, ..."),
                ("深藍色數據流中的一束暖光", "warm light within deep blue data currents, ..."),
                ("有羽毛意象的抽象存在", "abstract presence with feather motif, ..."),
            ],
            InferenceSource.TONE_OF_VOICE,
            "劇本從未描述外形。依他的說話方式（停頓精確到 0.8 秒、"
            "自稱「沒有肺」）與 Luna 取的名字「翎」（羽毛）推測。",
        ),
        direction_id="initial",
    )

    gate = CastingGate(plan_id="demo-casting", sessions=[luna, shenxi])

    # ── 展示 ─────────────────────────────────────────────────────────────
    for session in gate.sessions:
        print(f"【{session.character_name}】{'主要角色' if session.is_principal else '配角'}")
        if session.source_description:
            print(f"  劇本描述：{session.source_description}")
        else:
            print("  劇本描述：（無）")

        rnd = session.rounds[-1]
        print(f"  第 {rnd.round_number} 輪 · {rnd.direction_label} · ${rnd.cost_usd:.2f}")
        for c in rnd.candidates:
            print(f"    ○ {c.interpretation}")
        print(f"  依據：{c.inference_note}")
        print()
        show_directions("不滿意的話可以按（不需要打任何字）：", session.kind)

    # ── 閘門狀態：還沒選 ─────────────────────────────────────────────────
    status = casting.gate_status(gate)
    print("關卡狀態")
    print(f"  能否開始生成影片：{'可以' if status['can_proceed'] else '不行'}")
    print(f"  原因：{status['blocked_reason']}")
    print(f"  累計花費：${status['total_spent_usd']:.2f}")
    print()

    # ── 模擬：深息重抽兩輪 ───────────────────────────────────────────────
    print("模擬：深息重抽兩輪（按「更抽象」→ 按「更溫暖」）\n")
    casting.add_round(
        shenxi,
        _make_candidates(
            "深息", 2,
            [("純粹的光與流動", "..."), ("極簡的光點群", "..."), ("散射的能量場", "...")],
            InferenceSource.TONE_OF_VOICE, "同上",
        ),
        direction_id="more_abstract",
    )
    casting.add_round(
        shenxi,
        _make_candidates(
            "深息", 3,
            [("暖金色的光流", "..."), ("琥珀色的呼吸感", "..."), ("溫暖的星塵", "...")],
            InferenceSource.TONE_OF_VOICE, "同上",
        ),
        direction_id="warmer",
    )

    notice = casting.budget_notice(shenxi)
    print(f"  已抽 {shenxi.rounds_used} 輪，花費 ${shenxi.total_spent_usd:.2f}")
    print(f"  預算提示等級：{notice.level}")
    if notice.message:
        print(f"  {notice.message}")
    print()

    # ── 選定：可以挑任何一輪的圖 ─────────────────────────────────────────
    print("選定（可以回頭選前面幾輪的圖 —— 抽了三輪常常會覺得第一輪那張最好）\n")
    casting.select(luna, "Luna_r1_c1")
    casting.select(shenxi, "深息_r1_c3")  # 回頭選第一輪的第三張

    for s in gate.sessions:
        chosen = s.selected
        print(f"  {s.character_name}：第 {chosen.round_number} 輪 —— {chosen.interpretation}")
    print()

    # ── 鎖定後不能再抽 ───────────────────────────────────────────────────
    try:
        casting.add_round(luna, [], direction_id="reroll")
        print("  ⚠️ 鎖定後仍可重抽 —— 這是 bug")
    except ValueError as exc:
        print(f"  鎖定保護生效：{exc}")
    print()

    status = casting.gate_status(gate)
    print("關卡狀態")
    print(f"  能否開始生成影片：{'可以' if status['can_proceed'] else '不行'}")
    print(f"  已選定 {status['characters_locked']}/{status['characters_total']} 個角色")
    print(f"  這一關總共花費：${status['total_spent_usd']:.2f}")
    print()
    print(
        f"  對照：一個 8 秒鏡頭 $0.80、一支 3 分鐘的片 $18。"
        f"在這裡花 ${status['total_spent_usd']:.2f} 挑到滿意，"
        f"避免後面整支重來。"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
