"""提示詞約束引擎 —— 把「物理現象要合乎常理」變成可計算的東西。

設計理由：
    邊界設得好成功率就越高，關鍵在物理現象要合乎常理。

    影片模型不會拒絕不合理的要求，它會照生。編劇寫「她坐在桌前打電腦」，
    模型就照物理常識把螢幕擺成背對觀眾 —— 完全正確，也完全不能用。
    編劇不知道要補一句「螢幕朝向鏡頭」，因為那是攝影師的知識不是編劇的。

    這個模組就是那個攝影師：依鏡頭內容自動判斷該補哪些物理約束。

為什麼不把約束硬寫成常數：
    用字串比對決定要不要注入（例如 `"cat" in visual.lower()`）
    只在特定示範劇本上能動，換一份劇本、換一種語言就失效。

    所以這裡做三件事：
      1. 規則放 config/constraints.yaml，可以新增、可以被實測校準
      2. 觸發條件用 Shot schema 的結構化欄位，不是字面比對
      3. 每條規則帶 expected_lift，讓「約束提升多少成功率」變成可計算、
         而且可以被 ClickHouse 的實測資料回頭修正

約束不是加越多越好。提示詞太長時模型對後段指令的服從度會下降，
   反而稀釋掉真正重要的約束。所以有 max_rules_per_shot 與遞減係數。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..schemas.shot import Shot
from . import config_loader


class AppliedConstraint(BaseModel):
    """一條被套用的約束規則。"""

    rule_id: str
    category: str
    category_label: str
    inject_text: str
    principle: str = Field(
        description=(
            "為什麼需要這條。🔴 **只寫給使用者看** —— 編劇讀得懂的白話，"
            "不放日期、檔名、鏡頭編號、規則名稱、修改歷程。"
            "\n\n"
            "那些屬於 `engineering_note`（留在 constraints.yaml 裡，不進 API）。"
            "兩種讀者混在同一個欄位時，使用者會在畫面上讀到"
            "「某鏡回報左下出現一個男人頭」這種對他毫無意義的句子。"
            "**一個欄位一個讀者**，跟「一功能一檔」是同一條原則。"
        )
    )
    raw_lift: float = Field(description="這條規則單獨的預期提升")
    effective_lift: float = Field(description="套用遞減後的實際提升")
    evidence_source: str


class SkippedConstraint(BaseModel):
    """匹配到了、但因為疊加上限沒被套用的規則。

    為什麼要帶這麼多欄位，不能只留 rule_id：

        `skipped` 若只是 `list[str]`，就算接到前端也沒人看得懂
        `no_subtitle_burn_in` 是什麼 —— 等於還是靜默丟棄。

        而這件事的資訊量比想像中大：規則按 expected_lift 排序後取前 N 條，
        所以 content_flags 越多的鏡頭匹配到越多規則、被丟掉的也越多 ——
        **上限會在最難的鏡頭上砍掉最多約束**。
        使用者有權知道「這一鏡其實還想講三件事，但講不下」。
    """

    rule_id: str
    category_label: str
    principle: str = Field(description="這條規則想避免什麼。給人看的，不是給模型的。")
    lost_lift: float = Field(description="這條原本預期能提升多少成功率（已套風格權重）")


class FormatRule(BaseModel):
    """格式規則 —— 改變提示詞的寫法本身，不是附加一句話。

    例如台詞要用「角色 says: 台詞」而不是「a voice says "台詞"」，
    因為引號會觸發模型燒進字幕。這種規則不能用「附加一句」實現，
    必須由 prompt_writer agent 在組裝提示詞時就照這個格式寫。
    """

    rule_id: str
    template: str
    principle: str


class ConstraintPlan(BaseModel):
    """一個鏡頭的完整約束方案。"""

    shot_id: str
    applied: list[AppliedConstraint] = Field(default_factory=list)
    format_rules: list[FormatRule] = Field(default_factory=list)
    skipped: list[SkippedConstraint] = Field(
        default_factory=list,
        description="因為超過疊加上限而被略過的規則。🔴 要顯示出來，不能靜默丟棄。",
    )
    matched_count: int = Field(
        default=0,
        description=(
            "總共匹配到幾條規則（含被上限擋掉的）。"
            "`matched_count > len(applied)` 就代表這一鏡的約束講不完。"
        ),
    )
    total_lift: float = 0.0
    negative_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "第二頻道（Pro 體檢 F 條）：禁止類規則貢獻的"
            "負面關鍵字，**不佔句子疊加額度、匹配到就全收**。"
            "影片模型對逗號分隔的負面詞反應比長句 Do not 好，"
            "也不會稀釋主要約束的注意力。prompt_export 把它們併進尾端的"
            "全域負面行。"
        ),
    )
    advisories: list[str] = Field(
        default_factory=list,
        description=(
            "不是靠提示詞能解決、需要改工作流的建議。"
            "例如「畫面內文字應該後製疊加」—— 這種事約束句只能減輕，不能修好。"
        ),
    )

    @property
    def injection_block(self) -> str:
        """把所有約束句串成一段，供 prompt_writer 附在提示詞尾端。"""
        return " ".join(c.inject_text.strip() for c in self.applied)


# ---------------------------------------------------------------------------
# 觸發條件判斷
# ---------------------------------------------------------------------------

def _matches(shot: Shot, when: dict[str, Any]) -> bool:
    """判斷一條規則的觸發條件是否成立。

    所有條件都必須成立（AND）。刻意不支援 OR ——
    需要 OR 的情況應該拆成兩條規則，這樣每條規則的 expected_lift
    才能被獨立地實測校準。可讀性也比巢狀邏輯好。

    未知的條件鍵一律回 False（而不是忽略），理由同 config_loader：
    寧可讓規則不觸發被發現，也不要靜默地當它成立。
    """
    flags = {f.value for f in shot.content_flags}

    for key, expected in when.items():
        if key == "always":
            if not expected:
                return False

        elif key == "has_content_flag":
            # 任一個列出的 flag 存在就算符合
            if not flags.intersection(set(expected)):
                return False

        elif key == "character_count_min":
            if len(shot.characters_in_frame) < int(expected):
                return False

        elif key == "character_count_max":
            if len(shot.characters_in_frame) > int(expected):
                return False

        elif key == "has_dialogue":
            if bool(shot.dialogue) is not bool(expected):
                return False

        elif key == "has_music_cue":
            if bool(shot.music_cue) is not bool(expected):
                return False

        elif key == "has_reference_image":
            if bool(shot.reference_image_uri) is not bool(expected):
                return False

        elif key == "has_first_frame":
            if bool(shot.first_frame_from) is not bool(expected):
                return False

        elif key == "extensions_min":
            if shot.extensions < int(expected):
                return False

        elif key == "shot_type":
            # 列出的鏡別任一符合就成立（景深/視線規則用）
            if shot.shot_type.value not in set(expected):
                return False

        else:
            # 未知條件鍵 —— 不觸發，並讓一致性檢查在啟動時抓出來
            return False

    return True


# ---------------------------------------------------------------------------
# 主要 API
# ---------------------------------------------------------------------------

def plan_for_shot(shot: Shot, style_id: str | None = None) -> ConstraintPlan:
    """替一個鏡頭挑出該套用的約束規則。

    步驟：
      1. 找出所有觸發條件成立的規則
      2. 依風格調整每條規則的權重（見下方說明）
      3. 格式規則另外歸類（它們不佔疊加額度 —— 改寫法不會讓提示詞變長）
      4. 附加句規則按調整後的 lift 由高到低排序
      5. 只取前 max_rules_per_shot 條，其餘記錄在 skipped
      6. 套用遞減係數算實際提升，並夾在 max_total_lift 以下

    風格為什麼會改變約束權重：
        寫實風要嚴格要求手部解剖（權重 1.3），因為觀眾對真人的手最敏感。
        2D 動畫的手本來就是簡化的（權重 0.5），過度要求「五根手指、
        自然關節角度」反而會破壞畫風。
        定格動畫的「輕微抖動」是風格特徵不是缺陷，所以 no_morphing 降權。

        這就是為什麼風格不能只是提示詞前面加個形容詞。
    """
    cfg = config_loader.constraints()
    rules: list[dict[str, Any]] = cfg.get("rules", [])
    categories: dict[str, Any] = cfg.get("categories", {})
    stacking: dict[str, Any] = cfg.get("stacking", {})

    max_rules = int(stacking.get("max_rules_per_shot", 6))
    decay = float(stacking.get("diminishing_factor", 0.75))
    max_lift = float(stacking.get("max_total_lift", 0.35))

    style = config_loader.style_preset(style_id) if style_id else {}
    weight_overrides: dict[str, float] = style.get("constraint_weight_overrides", {})

    matched = [r for r in rules if _matches(shot, r.get("when", {}))]

    # 風格自帶的額外約束（例如動畫風的「線寬與色盤要一致」）。
    # 做成一條 pseudo-rule 混進排序，讓它跟其他規則公平競爭疊加額度。
    extra = str(style.get("extra_inject", "")).strip()
    if extra:
        matched.append(
            {
                "id": f"style_{style.get('id', 'unknown')}",
                "category": "style_specific",
                "when": {"always": True},
                "inject": extra,
                "principle": (
                    f"「{style.get('label', '')}」風格的專屬約束 —— "
                    f"{style.get('one_liner', '')}"
                ),
                "evidence": {"source": "style_preset"},
                "expected_lift": 0.05,
            }
        )
        categories = {
            **categories,
            "style_specific": {"label": "風格專屬"},
        }

    format_rules: list[FormatRule] = []
    additive: list[dict[str, Any]] = []
    advisories: list[str] = []

    for rule in matched:
        if rule.get("is_format_rule"):
            format_rules.append(
                FormatRule(
                    rule_id=rule["id"],
                    template=str(rule.get("inject_template", "")).strip(),
                    principle=str(rule.get("principle", "")).strip(),
                )
            )
        else:
            additive.append(rule)

        # 提醒走**明確的 `advisory` 欄位**，不從文字裡猜。
        #
        #    若用「principle 裡有沒有某個 emoji」來判斷，等於把
        #    **排版符號當成結構訊號** —— 在 principle 裡補一句帶標記的工程註記，
        #    就會意外多一則給使用者的提醒，而且內容是修改歷程。
        #
        #    實際後果：真正該提醒使用者的規則只有少數幾條，
        #    但更多條會因為含 emoji 被送到畫面上，編劇讀到的是工程內部的句子。
        #
        #    這種耦合最難查：改註釋不該改變產品行為，所以沒有人會想到
        #    要去看那裡。
        advisory = str(rule.get("advisory", "")).strip()
        if advisory:
            advisories.append(advisory)

    # 先套用風格權重再排序 —— 順序很重要。
    # 如果先排序再調權重，動畫風被降權的 hand_anatomy 仍會佔住高順位，
    # 把真正重要的規則擠出疊加額度。
    def styled_lift(rule: dict[str, Any]) -> float:
        base = float(rule.get("expected_lift", 0.0))
        return base * float(weight_overrides.get(rule["id"], 1.0))

    additive.sort(key=styled_lift, reverse=True)

    applied: list[AppliedConstraint] = []
    skipped: list[SkippedConstraint] = []
    total = 0.0

    for index, rule in enumerate(additive):
        if index >= max_rules:
            category = str(rule.get("category", ""))
            skipped.append(
                SkippedConstraint(
                    rule_id=rule["id"],
                    category_label=str(
                        categories.get(category, {}).get("label", category)
                    ),
                    principle=str(rule.get("principle", "")).strip(),
                    lost_lift=round(styled_lift(rule), 3),
                )
            )
            continue

        raw = styled_lift(rule)
        effective = raw * (decay**index)
        total += effective

        category = str(rule.get("category", ""))
        applied.append(
            AppliedConstraint(
                rule_id=rule["id"],
                category=category,
                category_label=str(
                    categories.get(category, {}).get("label", category)
                ),
                inject_text=" ".join(str(rule.get("inject", "")).split()),
                principle=str(rule.get("principle", "")).strip(),
                raw_lift=round(raw, 3),
                effective_lift=round(effective, 3),
                evidence_source=str(rule.get("evidence", {}).get("source", "unknown")),
            )
        )

    # 第二頻道：負面關鍵字從**所有匹配到的規則**收（含被上限擋掉的）。
    #    這正是降上限而不失守的機制 —— 句子頻道降到 4 條保護注意力，
    #    禁止類的保護以幾個關鍵字的形式全數保留在尾端負面行。
    negative_keywords: list[str] = []
    for rule in additive:
        for kw in rule.get("negative_keywords", []) or []:
            kw = str(kw).strip()
            if kw and kw not in negative_keywords:
                negative_keywords.append(kw)

    return ConstraintPlan(
        shot_id=shot.shot_id,
        applied=applied,
        format_rules=format_rules,
        skipped=skipped,
        matched_count=len(additive),
        total_lift=round(min(total, max_lift), 3),
        advisories=advisories,
        negative_keywords=negative_keywords,
    )


def lift_for_shot(shot: Shot, style_id: str | None = None) -> float:
    """只要成功率提升值。success_predictor 用這個，不需要完整方案。"""
    return plan_for_shot(shot, style_id).total_lift
