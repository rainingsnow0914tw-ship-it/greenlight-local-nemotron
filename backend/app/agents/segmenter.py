"""分段 —— 偵測劇本自帶的章節，或建議怎麼切。

設計理由：
    一份 45 分鐘的劇本，最便宜的檔位全片也要 $135，超過整個專案預算。
    但劇本本身有 8 個章節 —— 拍第一章只要約 $15，滿意再拍第二章。

    「就是有個選擇權，就是給它看全貌嘛。」

成本策略：
    能用 code 做的不花錢。

    章節標記（`Chapter 1`、`第三章`、`## 第二幕`）是**字面上的東西**，
    用 regex 就能抓，準確度比 LLM 高、成本是零、而且結果可預測。

    只有「這份劇本沒有章節，該怎麼切成 5 段」才需要語意判斷 ——
    那時候輸入也只是場次摘要（幾 KB），不是整份劇本，成本約 $0.03。
"""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.screenplay import Screenplay
from ..schemas.segment import (
    Segment,
    SegmentationOptions,
    SegmentPlan,
    SegmentSource,
)
from ..services import rates
from ..services import config_loader

# ---------------------------------------------------------------------------
# 章節偵測（純 code，零成本）
# ---------------------------------------------------------------------------

# 各種章節標記的樣式。刻意寫得寬鬆 —— 真實稿件的格式很亂，
# 寧可多抓再讓使用者刪，也不要漏抓整個章節結構。
#
# 順序有意義：先試最明確的（Chapter N），再試中文的，最後才是 markdown 標題。
# 不要用 `\b`（word boundary）—— 實際踩到。
#
#   原本寫 `Chapter\s+(\d+)\b`，遇到 `Chapter 10世界的對抗` 就匹配失敗，
#   整個第 10 章被靜默漏掉。
#
#   原因：Python 的 `re` 預設是 Unicode 模式，**中文字算 word character**。
#   所以 `0` 和 `世` 都是 word char，兩者之間沒有 word boundary，`\b` 不成立。
#
#   中文稿件裡「數字後面直接接中文」是常態（`第3章寫作` / `Chapter 10世界`），
#   在處理中文的 regex 裡用 `\b` 幾乎一定會出事。
_CHAPTER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Chapter N", re.compile(r"^\s*#*\s*Chapter\s+(\d+)\s*(.*)$", re.IGNORECASE)),
    ("第N章", re.compile(r"^\s*#*\s*第\s*([一二三四五六七八九十百\d]+)\s*章\s*(.*)$")),
    ("第N幕", re.compile(r"^\s*#*\s*第\s*([一二三四五六七八九十百\d]+)\s*幕\s*(.*)$")),
    ("第N話", re.compile(r"^\s*#*\s*第\s*([一二三四五六七八九十百\d]+)\s*[話回]\s*(.*)$")),
    ("PART N", re.compile(r"^\s*#*\s*PART\s+([IVX\d]+)\s*(.*)$", re.IGNORECASE)),
]

_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_chapter_number(raw: str) -> int | None:
    """把章節編號轉成整數。支援阿拉伯數字與簡單中文數字。

    只處理到「二十九」—— 再上去的中文數字組合規則變複雜，
    而且實務上不常見。轉不出來就回 None，由連續性檢查略過。
    """
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)

    if not raw:
        return None
    if raw == "十":
        return 10
    if raw.startswith("十"):                      # 十一 ~ 十九
        return 10 + _CN_DIGITS.get(raw[1:], 0)
    if len(raw) == 3 and raw[1] == "十":          # 二十一 ~ 九十九
        return _CN_DIGITS.get(raw[0], 0) * 10 + _CN_DIGITS.get(raw[2], 0)
    if len(raw) == 2 and raw[1] == "十":          # 二十 ~ 九十
        return _CN_DIGITS.get(raw[0], 0) * 10
    return _CN_DIGITS.get(raw)


class ChapterMark(BaseModel):
    """在原文裡找到的一個章節標記。"""

    line_number: int
    title: str
    pattern_name: str
    number: int | None = Field(
        default=None, description="解析出來的章節編號。連續性檢查用。"
    )


def check_continuity(marks: list[ChapterMark]) -> list[str]:
    """檢查章節編號有沒有斷號。

    為什麼一定要有這個檢查：

       regex 漏掉一章時，系統會**若無其事地繼續跑** ——
       分段方案照樣產出、成本照樣算、每一段看起來都很正常。
       漏掉的那章內容被併進前一段，只有標題對不上，
       不比對原文根本看不出來。

       這種「安靜的錯誤」比當場爆掉危險得多。
       所以偵測到斷號就要明確講出來，讓使用者決定要不要手動補。
    """
    numbered = [(m.number, m) for m in marks if m.number is not None]
    if len(numbered) < 2:
        return []

    problems: list[str] = []
    for (prev_n, prev_m), (curr_n, curr_m) in zip(numbered, numbered[1:]):
        if curr_n == prev_n:
            problems.append(
                f"第 {curr_n} 章出現兩次（第 {prev_m.line_number} 行與 "
                f"第 {curr_m.line_number} 行）"
            )
        elif curr_n != prev_n + 1:
            missing = list(range(prev_n + 1, curr_n))
            if missing:
                problems.append(
                    f"章節編號斷號：第 {prev_n} 章（第 {prev_m.line_number} 行）"
                    f"之後直接跳到第 {curr_n} 章（第 {curr_m.line_number} 行），"
                    f"中間的第 {'、'.join(map(str, missing))} 章沒被偵測到 —— "
                    f"可能是標題格式不同，那些內容會被併進前一段"
                )
    return problems


def detect_chapters(text: str) -> list[ChapterMark]:
    """從原文找章節標記。純字串比對，不花錢。

    只採用「命中數最多」的那一種樣式，不混用。

    為什麼不混用：一份稿子如果同時有 `Chapter 1` 和 `## 第一節`，
    混在一起會切出錯亂的層級（章底下的節被當成跟章同級）。
    取命中最多的那種，通常就是作者實際使用的層級。
    """
    lines = text.splitlines()
    best: list[ChapterMark] = []

    for name, pattern in _CHAPTER_PATTERNS:
        marks: list[ChapterMark] = []
        for i, line in enumerate(lines, 1):
            m = pattern.match(line)
            if m:
                marks.append(
                    ChapterMark(
                        line_number=i,
                        title=line.strip(" 　#*"),
                        pattern_name=name,
                        number=_parse_chapter_number(m.group(1)),
                    )
                )
        if len(marks) > len(best):
            best = marks

    # 只有一個標記等於沒有分章（整份就一章），不值得當成分段方案
    return best if len(best) >= 2 else []


def _plan_from_chapters(
    screenplay: Screenplay, marks: list[ChapterMark]
) -> SegmentPlan | None:
    """把章節標記對應到場次，組成一個分段方案。

    對應方式：一個章節標記在第 N 行，就從「起始行號 >= N」的第一場開始算新段。
    這依賴 script_parser 有回報 source_line_start —— 那是我們刻意保留行號的
    原因之一（另一個原因是省輸出成本）。
    """
    if not marks:
        return None

    scenes = sorted(screenplay.scenes, key=lambda s: s.source_line_start)
    if not scenes or all(s.source_line_start == 0 for s in scenes):
        return None

    segments: list[Segment] = []
    for idx, mark in enumerate(marks):
        next_line = marks[idx + 1].line_number if idx + 1 < len(marks) else 10**9
        member = [
            s
            for s in scenes
            if mark.line_number <= s.source_line_start < next_line
        ]
        if not member:
            continue

        segments.append(
            Segment(
                segment_id=f"chapter-{idx + 1:02d}",
                order=len(segments) + 1,
                title=mark.title[:80],
                scene_numbers=[s.scene_number for s in member],
                estimated_seconds=sum(s.estimated_screen_seconds for s in member),
                synopsis=member[0].action[:120],
                # 章節是作者自己劃的敘事單位，預設能獨立成立。
                # 這是合理的預設 —— 作者分章通常就是按敘事段落分的。
                is_self_contained=True,
            )
        )

    if len(segments) < 2:
        return None

    rationale = (
        f"劇本自己就有 {len(segments)} 個章節（偵測樣式：{marks[0].pattern_name}）。"
        f"這是作者劃的敘事單位，通常每段都能獨立成立。"
    )

    # 斷號要講出來，不能沉默。漏掉的章節內容會被併進前一段，
    #    只有標題對不上 —— 不比對原文根本看不出來。
    problems = check_continuity(marks)
    if problems:
        rationale += "\n⚠️ " + "\n⚠️ ".join(problems)

    return SegmentPlan(
        plan_id="by-chapter",
        label=f"按劇本章節分（{len(segments)} 段）",
        source=SegmentSource.CHAPTER,
        segments=segments,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# LLM 建議分段
# ---------------------------------------------------------------------------


class SuggestedSegment(BaseModel):
    """模型建議的一段。"""

    title: str = Field(description="這一段的標題，中文，10 字以內")
    first_scene: int = Field(description="起始場次編號")
    last_scene: int = Field(description="結束場次編號（含）")
    synopsis: str = Field(description="這一段講什麼，一到兩句")
    is_self_contained: bool = Field(
        description="單獨拍出來看得懂嗎？有沒有起承轉合？"
    )


class SuggestedPlan(BaseModel):
    segments: list[SuggestedSegment]
    rationale: str = Field(description="為什麼這樣切，一到兩句")


_SYSTEM = """你是一位資深的剪輯師，擅長把長篇故事切成能獨立播映的段落。

切分的原則：
1. 每一段要有自己的起承轉合，單獨看也成立
2. 在情緒或劇情的轉折處切，不要切在一段對話中間
3. 每段長度盡量接近，但**敘事完整性優先於長度平均**

🔴 誠實原則：如果某一段其實無法獨立成立（只是半截劇情），
   把 is_self_contained 標成 false。使用者會拿這個判斷
   「先拍這段划不划算」—— 謊報會害他花錢拍出看不懂的東西。"""


def _client():
    """Vertex genai client。**timeout 統一在 services/genai_client 管。**

    分段：要讀整份劇本。
    原本這裡是複製十份的四行建構，timeout 寫死 900_000 毫秒 ——
       跟 Cloud Run 的請求上限一模一樣，等於沒有保護。
    """
    from ..services import genai_client

    return genai_client.client(genai_client.LONG_MS, action="分段")


def _scene_digest(screenplay: Screenplay) -> str:
    """把場次表壓成給模型看的摘要。

    只給編號、秒數、摘要 —— 不給原文。
    分段判斷需要的是「劇情走向」，不是逐字內容，
    餵整份劇本進去是浪費（那會讓這一步從 $0.03 變成 $0.15）。
    """
    return "\n".join(
        f"{s.scene_number}. [{s.estimated_screen_seconds:.0f}s] {s.action}"
        for s in screenplay.scenes
    )


def suggest_plan(
    screenplay: Screenplay, target_count: int
) -> tuple[SegmentPlan, dict[str, Any]]:
    """請模型把劇本切成指定段數。回傳 (方案, 用量)。"""
    from google.genai import types

    client = _client()
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("segmentation", "pro")
    model = pricing.get("tiers", {}).get(tier_key, {}).get("model_id", "gemini-3.1-pro-preview")

    prompt = f"""把以下 {len(screenplay.scenes)} 場戲切成 **{target_count} 段**。

作品：《{screenplay.title}》
總長約 {screenplay.total_estimated_seconds / 60:.0f} 分鐘

場次表（編號. [推估秒數] 劇情摘要）：
{_scene_digest(screenplay)}

要求：
- 剛好 {target_count} 段
- 每段連續、不跳號、涵蓋全部場次
- 在情緒或劇情轉折處切"""

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            response_schema=SuggestedPlan,
            temperature=0.3,
        ),
    )
    suggested: SuggestedPlan = response.parsed

    by_number = {s.scene_number: s for s in screenplay.scenes}
    segments: list[Segment] = []
    for idx, ss in enumerate(suggested.segments):
        member = [
            by_number[n]
            for n in range(ss.first_scene, ss.last_scene + 1)
            if n in by_number
        ]
        if not member:
            continue
        segments.append(
            Segment(
                segment_id=f"seg{target_count}-{idx + 1:02d}",
                order=len(segments) + 1,
                title=ss.title,
                scene_numbers=[s.scene_number for s in member],
                estimated_seconds=sum(s.estimated_screen_seconds for s in member),
                synopsis=ss.synopsis,
                is_self_contained=ss.is_self_contained,
            )
        )

    plan = SegmentPlan(
        plan_id=f"suggested-{target_count}",
        label=f"分成 {len(segments)} 段",
        source=SegmentSource.SUGGESTED,
        segments=segments,
        rationale=suggested.rationale,
    )

    meta = getattr(response, "usage_metadata", None)
    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    p_tok = getattr(meta, "prompt_token_count", 0) or 0
    o_tok = getattr(meta, "candidates_token_count", 0) or 0
    cost = (p_tok / 1_000_000) * float(cfg.get("input_per_1m_usd", 2.0)) + (
        o_tok / 1_000_000
    ) * float(cfg.get("output_per_1m_usd", 12.0))

    return plan, {
        "model": model,
        "prompt_tokens": p_tok,
        "output_tokens": o_tok,
        "cost_usd": round(cost, 4),
    }


# ---------------------------------------------------------------------------
# 組合：產出所有選項
# ---------------------------------------------------------------------------


def _suggest_target_counts(total_seconds: float) -> list[int]:
    """依總長度決定要建議哪幾種段數。

    邏輯：讓每段落在「一次看得完、一次拍得起」的範圍。
      約 4 分鐘/段  → 適合先試效果，單段成本低
      約 8 分鐘/段  → 比較完整的敘事單位

    刻意只給兩個數字。選項太多會造成選擇癱瘓，
    而且每多一個方案就多花一次 LLM 呼叫。
    """
    minutes = total_seconds / 60
    if minutes < 6:
        return []  # 太短了，不需要分段
    coarse = max(2, round(minutes / 8))
    fine = max(3, round(minutes / 4))
    return sorted({coarse, fine}) if coarse != fine else [coarse]


def build_options(
    screenplay: Screenplay, raw_text: str
) -> tuple[SegmentationOptions, dict[str, Any]]:
    """產出所有分段方案供使用者選擇。

    順序：
      1. 章節偵測（零成本）—— 有的話這通常就是最佳解
      2. 依總長度建議兩種段數（各一次 LLM 呼叫，約 $0.03/次）
    """
    options: list[SegmentPlan] = []
    total_cost = 0.0
    usages: list[dict[str, Any]] = []

    # ---- 1. 章節（零成本）----
    marks = detect_chapters(raw_text)
    chapter_plan = _plan_from_chapters(screenplay, marks)
    if chapter_plan:
        options.append(chapter_plan)

    # ---- 2. 建議段數 ----
    for target in _suggest_target_counts(screenplay.total_estimated_seconds):
        # 跟章節方案段數相同的話就跳過 —— 兩個一樣的選項只會讓人困惑
        if chapter_plan and chapter_plan.segment_count == target:
            continue
        plan, usage = suggest_plan(screenplay, target)
        options.append(plan)
        usages.append(usage)
        total_cost += usage["cost_usd"]

    # ---- 推薦 ----
    #
    # 有章節就推章節 —— 那是作者自己劃的敘事單位，
    # 比任何演算法切出來的都貼近原意。
    if chapter_plan:
        recommended = chapter_plan.plan_id
        reason = (
            f"劇本自己有 {chapter_plan.segment_count} 個章節，"
            f"平均每段 {chapter_plan.average_seconds / 60:.1f} 分鐘。"
            f"作者劃的段落比系統切的更貼近原意，建議照著走。"
        )
    elif options:
        # 沒章節的話，選「能獨立成立的段落最多」的方案
        best = max(options, key=lambda p: (p.self_contained_count, -p.segment_count))
        recommended = best.plan_id
        reason = (
            f"這個方案有 {best.self_contained_count}/{best.segment_count} 段"
            f"能獨立成立，最適合「先拍一段看效果」。"
        )
    else:
        recommended, reason = None, ""

    return (
        SegmentationOptions(
            screenplay_title=screenplay.title,
            total_scenes=len(screenplay.scenes),
            total_seconds=screenplay.total_estimated_seconds,
            options=options,
            recommended_plan_id=recommended,
            recommendation_reason=reason,
        ),
        {"cost_usd": round(total_cost, 4), "calls": usages},
    )
