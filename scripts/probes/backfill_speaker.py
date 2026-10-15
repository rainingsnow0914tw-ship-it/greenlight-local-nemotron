"""替既有鏡頭表補上 `speaker`（誰在說話）。約 $0.005。

為什麼需要：

    `speaker` 欄位是 才加的。在那之前分好的鏡頭表沒有它，
    系統只能從 `characters_in_frame`（畫面上有誰）取說話者 ——
    但那兩件事經常不同。

    實例：s001_06 畫面是深息伸手觸碰光幕，台詞卻是「（月音聲音）你會夢嗎？」
    於是提示詞變成「深息的男聲說出月音的台詞」。

## 為什麼不重新分鏡

    重新分鏡會產生**新的鏡號**，已經生成的鏡頭就對不上了 ——
    那等於把已經花掉的錢作廢。這支腳本只補欄位，不動鏡頭表結構。

    新分的鏡頭表不需要跑這支：分鏡 agent 的規則已經要求填 speaker。

用法：
    python scripts/probes/backfill_speaker.py shensi-001
    python scripts/probes/backfill_speaker.py shensi-001 --segment 1
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import env_boot  # noqa: E402

env_boot.load()

from pydantic import BaseModel, Field  # noqa: E402

from app.services import api_retry, config_loader, project_store  # noqa: E402


class _Tagged(BaseModel):
    shot_id: str
    speaker: str = Field(
        description="說這句話的角色名，必須來自提供的角色清單。判斷不出來就填空字串。"
    )
    reason: str = Field(default="", description="一句話說明依據，給人核對用。")


class _Result(BaseModel):
    shots: list[_Tagged]


_SYSTEM = """你在替一份已經分好的鏡頭表補上「誰在說這句台詞」。

判斷依據，依可靠度排序：
1. 台詞裡的明確標記 —— 例如「（月音聲音）」「（旁白）」「A說：」
2. 台詞內容的稱謂與語氣 —— 對誰說話、自稱什麼
3. 畫面上有誰（只有一個人時，多半是他說的）

🔴 **畫面上有誰不等於誰在說話。** 畫外音、旁白、電話、訊息、
   隔壁房間傳來的聲音，說話的人都不在畫面上。
   不要因為畫面只有 A，就把 B 的台詞算成 A 說的。

🔴 判斷不出來就填空字串，不要猜。
   猜錯會讓系統把錯的音色鎖給那個角色，之後每一鏡都用錯的聲音 ——
   那比不填更難救。留空的話系統會用中性處理。

角色名必須完全照抄提供的清單，不要自己改寫或翻譯。"""


def main() -> int:
    parser = argparse.ArgumentParser(description="補上鏡頭的說話者")
    parser.add_argument("project_id")
    parser.add_argument("--segment", type=int, default=None, help="只處理某一段")
    parser.add_argument("--dry-run", action="store_true", help="只看結果不存檔")
    args = parser.parse_args()

    from google import genai
    from google.genai import types

    store = project_store.get_store()
    state = store.load(args.project_id)
    if state is None:
        print(f"找不到專案 {args.project_id}")
        return 1

    characters = [c.name for c in (state.screenplay.characters if state.screenplay else [])]
    if not characters:
        print("這個專案沒有角色資料")
        return 1

    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("prompt_writing", "flash")
    model = pricing.get("tiers", {}).get(tier_key, {}).get("model_id", "gemini-3.6-flash")

    client = genai.Client(
        vertexai=True,
        project=os.getenv("GCP_PROJECT_ID"),
        location="global",
        http_options=types.HttpOptions(timeout=600_000),
    )

    total_cost = 0.0
    total_filled = 0

    for segment in state.segments:
        if args.segment and segment.order != args.segment:
            continue
        if not segment.shot_list:
            continue

        pending = [s for s in segment.shot_list.shots if s.dialogue and not s.speaker]
        if not pending:
            print(f"第 {segment.order} 段：沒有需要補的鏡頭")
            continue

        lines = [
            f"[{s.shot_id}]\n畫面：{s.description}\n"
            f"畫面上有：{'、'.join(s.characters_in_frame) or '（無人）'}\n"
            f"台詞：{s.dialogue}"
            for s in pending
        ]
        user_prompt = (
            f"這部片的角色清單：{'、'.join(characters)}\n\n"
            f"請判斷以下每一鏡的台詞是誰說的。\n\n" + "\n\n".join(lines)
        )

        response = api_retry.governed(
            model,
            lambda: client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM,
                    response_mime_type="application/json",
                    response_schema=_Result,
                    temperature=0.1,
                    max_output_tokens=4_096,
                ),
            ),
            label="backfill_speaker",
        )

        result = _Result.model_validate_json(response.text)
        by_id = {t.shot_id: t for t in result.shots}

        print(f"\n第 {segment.order} 段（{len(pending)} 鏡需要補）")
        for shot in pending:
            tag = by_id.get(shot.shot_id)
            if not tag:
                continue
            name = tag.speaker.strip()
            # 只接受角色清單裡真的有的名字。模型偶爾會回
            #    「旁白」「系統」這種不是角色的字串 —— 那會讓
            #    casting.session_for() 查不到，靜靜地退回無音色。
            if name and name not in characters:
                print(f"  {shot.shot_id}: 「{name}」不在角色清單，留空")
                name = ""

            shot.speaker = name or None
            if name:
                total_filled += 1
            mark = name or "（留空）"
            infr = " ← 不在畫面上" if name and name not in shot.characters_in_frame else ""
            print(f"  {shot.shot_id}: {mark}{infr}")
            if tag.reason:
                print(f"      {tag.reason[:60]}")

        meta = getattr(response, "usage_metadata", None)
        cfg = next(
            (c for c in pricing.get("tiers", {}).values() if c.get("model_id") == model),
            {},
        )
        cost = (
            (getattr(meta, "prompt_token_count", 0) or 0)
            * float(cfg.get("input_per_1m_usd", 0.3))
            + (getattr(meta, "candidates_token_count", 0) or 0)
            * float(cfg.get("output_per_1m_usd", 2.5))
        ) / 1_000_000
        total_cost += cost

    if args.dry_run:
        print(f"\n[dry-run] 沒有存檔。預估花費 ${total_cost:.5f}")
        return 0

    store.save(state)
    print(f"\n補上 {total_filled} 個說話者，花費 ${total_cost:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
