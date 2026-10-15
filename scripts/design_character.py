"""生一輪角色候選圖，並存進專案狀態。

    python scripts/design_character.py shensi-001 月音
    python scripts/design_character.py shensi-001 深息 --direction more_abstract

這支腳本會花錢（一輪約 $0.065：文字 $0.005 + 三張圖 $0.06）。
   跑之前先看它印出的預估，確認要花這筆再按 Enter —— 跟 UI 上的閘門同一個原則：
   花錢的動作一律要能先回答「這一按花多少」。

為什麼要有這支腳本（而不是只從 UI 按）：
    UI 那條路徑會經過 route、狀態機、閘門檢查。
    agent 本身壞掉的時候，從 UI 按只會看到一個 500，
    要拆開才知道是模型輸出不合格、圖片模型不可用、還是狀態機擋住。
    這支只跑 agent，讓失敗訊息直接落在真正的失敗點上。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# Windows 的預設終端編碼是 cp950（繁中），塞不下日文中點「・」這類字元，
#    印出來會直接 UnicodeEncodeError 中斷整支腳本 ——
#    明明模型已經回應完、錢也花了，卻死在印結果那一行。
#    這個專案的內容天生就是多語言的（中／英／日三語房間），所以一律強制 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.services import env_boot  # noqa: E402

env_boot.load()  # 讀 .env，不依賴呼叫者的 shell 有沒有 export 過

from app.agents import character_designer  # noqa: E402
from app.schemas.casting import CastingGate, CastingSession, CharacterKind  # noqa: E402
from app.services import casting, config_loader, project_store  # noqa: E402


def _context_excerpt(state, character_name: str, max_scenes: int = 4) -> str:
    """挑幾段這個角色出現的場次摘要當語境。

    不整份劇本丟給模型 —— 一份 40K token 的劇本每抽一輪就重讀一次，
    抽十輪就是 400K token 的浪費。選角真正需要的資訊只有幾百字：
    這個人在什麼情境下出現、跟誰互動、劇情功能是什麼。
    """
    screenplay = state.screenplay
    if not screenplay:
        return ""

    picked = [
        s for s in screenplay.scenes if character_name in (s.characters_present or [])
    ][:max_scenes]

    return "\n".join(f"・{s.action}" for s in picked if s.action)


def main() -> int:
    parser = argparse.ArgumentParser(description="生一輪角色候選圖")
    parser.add_argument("project_id")
    parser.add_argument("character")
    parser.add_argument(
        "--direction",
        default="initial",
        help="重抽方向 id（見 config/casting.yaml 的 directions）",
    )
    parser.add_argument("--style", default=None, help="覆寫風格，預設用專案已定調的")
    parser.add_argument("--lang", default="zh")
    parser.add_argument(
        "--kind",
        choices=["human", "non_human"],
        default=None,
        help=(
            "覆寫角色類型。舊版解析的專案沒有存非人角色清單，"
            "AI／系統類角色要用這個指定，否則會拿到人物專用的方向按鈕。"
        ),
    )
    parser.add_argument("--yes", action="store_true", help="跳過花費確認")
    args = parser.parse_args()

    state = project_store.get_store().load(args.project_id)
    if state is None:
        print(f"找不到專案 {args.project_id}")
        return 1

    screenplay = state.screenplay
    if not screenplay:
        print("這個專案還沒解析劇本")
        return 1

    character = next(
        (c for c in screenplay.characters if c.name == args.character), None
    )
    if character is None:
        names = "、".join(c.name for c in screenplay.characters)
        print(f"找不到角色「{args.character}」。這個專案有：{names}")
        return 1

    # ---- 找出或建立這個角色的 session ----
    gate = state.casting or CastingGate(plan_id=args.project_id)
    session = gate.session_for(character.name)
    if session is None:
        # 非人角色的判斷來自劇本解析階段的附加資料（Character schema 沒有這個欄位）。
        # 拿不到就預設 human —— 猜錯的代價只是方向按鈕給錯組，使用者換一個方向就好。
        non_human = set(state.non_human_characters or [])
        if args.kind:
            kind = CharacterKind(args.kind)
        elif character.name in non_human:
            kind = CharacterKind.NON_HUMAN
        else:
            kind = CharacterKind.HUMAN

        session = CastingSession(
            character_name=character.name,
            kind=kind,
            is_principal=character.is_principal,
            source_description=character.visual_description,
        )
        gate.sessions.append(session)

    style_id = args.style or state.style_id or "photoreal"
    per_image = float(
        config_loader.pricing()
        .get("imagen", {})
        .get("tiers", {})
        .get("fast", {})
        .get("per_image_usd", 0.02)
    )
    count = int(config_loader.casting().get("candidates_per_round", 3))

    print(f"專案     {args.project_id}")
    print(f"角色     {character.name}（{session.kind.value}"
          f"{'・主要角色' if session.is_principal else ''}）")
    print(f"劇本外貌 {character.visual_description or '未提供 —— 將由 agent 推導'}")
    print(f"風格     {style_id}")
    print(f"方向     {args.direction}")
    print(f"第幾輪   {session.rounds_used + 1}")
    print(f"預估花費 ${count * per_image + 0.005:.3f}（{count} 張圖 + 文字推理）")

    if not args.yes:
        input("\n按 Enter 開始，Ctrl+C 取消 ")

    candidates, usage = character_designer.design_round(
        args.project_id,
        session,
        style_id=style_id,
        direction_id=args.direction,
        lang=args.lang,
        context_excerpt=_context_excerpt(state, character.name),
    )

    casting.add_round(session, candidates, direction_id=args.direction)
    state.casting = gate
    project_store.get_store().save(state)

    print("\n" + "=" * 70)
    for cand in candidates:
        print(f"\n[{cand.candidate_id}]  {cand.interpretation}")
        print(f"  標籤   {cand.appearance_tags}")
        print(f"  依據   {cand.inference_source.value} —— {cand.inference_note}")
        print(f"  圖片   {cand.image_uri or '❌ 生成失敗'}")

    print("\n" + "=" * 70)
    print(f"文字   {usage['input_tokens']} in / {usage['output_tokens']} out"
          f" = ${usage['cost_usd']}")
    print(f"圖片   {usage['images_generated']} 張 × {usage['image_model']}"
          f" = ${usage['image_cost_usd']}")
    print(f"合計   ${usage['total_cost_usd']}")
    if usage.get("image_failures"):
        print("\n⚠️  部分圖片生成失敗：")
        for line in usage["image_failures"]:
            print(f"   {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
