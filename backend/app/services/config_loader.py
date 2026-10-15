"""設定檔載入與一致性檢查。

設計理由：
    價目表跟成功率先驗都放 YAML、不寫死在 code 裡。
    但這帶來一個新風險：**schema 加了新的列舉值，卻忘記在 priors.yaml 補對應項。**

    那種錯誤最惡劣的地方是它不會爆炸 —— success_predictor 查不到修正項就用 0.0，
    估出來的數字看起來完全正常，只是悄悄少吃了一個修正。等到發現的時候，
    已經拿錯誤的數字做過決策了。

    所以這裡在載入時就做一次硬檢查，兩邊對不上直接 raise，不給它靜默的機會。
    能用架構解的問題就不用文件解 ——
    與其寫文件叮嚀「記得同步兩個檔案」，不如讓不同步的程式根本啟動不了。
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

# 專案根目錄：問 env_boot（唯一的家；打包成安裝檔後位置不一樣，那邊處理）
from .env_boot import project_root as _project_root

_PROJECT_ROOT = _project_root()
_CONFIG_DIR = _PROJECT_ROOT / "config"


class ConfigError(RuntimeError):
    """設定檔本身有問題 —— 缺檔、格式錯、或與 schema 不同步。"""


def _load_yaml(name: str) -> dict[str, Any]:
    """讀一個 config/*.yaml。

    刻意不做 fallback 到預設值：設定檔不見了就是部署出錯，
    這時候用一組硬寫在 code 裡的預設值繼續跑，只會讓錯誤更難查。
    """
    path = _CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"找不到設定檔：{path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"設定檔格式錯誤（最外層應為 mapping）：{path}")
    return data


@functools.lru_cache(maxsize=1)
def pricing() -> dict[str, Any]:
    """價目表。快取起來，一個 process 只讀一次。

    要在執行中改價格的話，呼叫 reload() —— 不要直接改回傳的 dict，
    那是共用的物件，改了會污染其他呼叫端。
    """
    return _load_yaml("pricing.yaml")


@functools.lru_cache(maxsize=1)
def priors() -> dict[str, Any]:
    """成功率先驗值。"""
    return _load_yaml("priors.yaml")


@functools.lru_cache(maxsize=1)
def constraints() -> dict[str, Any]:
    """提示詞約束規則庫。"""
    return _load_yaml("constraints.yaml")


@functools.lru_cache(maxsize=1)
def styles() -> dict[str, Any]:
    """影片風格預設。"""
    return _load_yaml("styles.yaml")


@functools.lru_cache(maxsize=1)
def symptoms() -> list[dict[str, Any]]:
    """症狀庫（查證沒過時 agent 唯一可以引用的知識，verify/doctor.py）。
    讀使用者知識庫那一份（services/knowledge.py：安裝檔外面、隨時可改、不快取 —— 改完下一鏡就用新的）。"""
    from . import knowledge

    return knowledge.load_yaml("symptoms.yaml").get("symptoms", [])


def casting() -> dict[str, Any]:
    """選角關卡設定（重抽方向、成本護欄）。"""
    return _load_yaml("casting.yaml")


@functools.lru_cache(maxsize=1)
def content_policy() -> dict[str, Any]:
    """內容安全預審規則庫（政策類別、判準、給編劇的建議）。

    這份**不是違禁詞表** —— 實測同一句台詞在不同語境下
       一次被擋一次通過，所以規則寫的是類別與判準，語境交給模型判。
    """
    return _load_yaml("content_policy.yaml")


@functools.lru_cache(maxsize=1)
def formats() -> dict[str, Any]:
    """敘事形式（劇情／紀錄片／訪談／導覽）。

    跟 styles 是兩個維度：styles 管「看起來像什麼」，
       formats 管「用什麼方式講」。一部紀錄片可以是實拍也可以是手繪。
    """
    return _load_yaml("formats.yaml")


def format_preset(format_id: str) -> dict[str, Any]:
    """取一個敘事形式。查不到回預設那個 —— 理由跟 style_preset 不同：

    style 查不到要拋（風格 id 打錯會生出完全不對的畫面），
       但 format 是後來才加的欄位，**舊專案沒有這個值**。
       對它們拋例外等於讓所有既有專案打不開。
    """
    items = formats().get("formats", [])
    fallback = formats().get("default", "drama")
    found = next((f for f in items if f.get("id") == format_id), None)
    if found is None:
        found = next((f for f in items if f.get("id") == fallback), None)
    return found or {}


def style_preset(style_id: str) -> dict[str, Any]:
    """取一個風格預設。查不到直接拋，理由同 veo_tier()。"""
    presets = styles().get("presets", [])
    found = next((p for p in presets if p.get("id") == style_id), None)
    if found is None:
        available = ", ".join(str(p.get("id")) for p in presets)
        raise ConfigError(
            f"styles.yaml 沒有風格 '{style_id}'。可用的風格：{available}"
        )
    return found


def reload() -> None:
    """清掉快取，下次呼叫會重新讀檔。改完 YAML 想立刻生效時用。"""
    pricing.cache_clear()
    priors.cache_clear()
    constraints.cache_clear()
    styles.cache_clear()
    # 每個 accessor 都要在這裡清：漏掉的那個改了 YAML 再 reload 也拿不到新值
    #    —— 而且不會報錯，只是安靜地繼續用舊設定。新增 accessor 一定要同步加在這裡。
    casting.cache_clear()
    content_policy.cache_clear()
    formats.cache_clear()


# ---------------------------------------------------------------------------
# 一致性檢查
# ---------------------------------------------------------------------------

def verify_config_integrity() -> list[str]:
    """檢查 schema 的列舉值與 YAML 的鍵是否對得起來。

    回傳問題清單（空的代表沒問題）。由 FastAPI 的 startup hook 呼叫，
    有問題就讓服務起不來 —— 帶著錯誤設定跑起來，比起不來更危險。

    為什麼在函式內 import schema：
        避免 module 層級的循環 import（schemas 未來可能會反過來用 config）。
        這種延遲 import 在啟動路徑上跑一次，效能無所謂。
    """
    from ..schemas.plan import QualityTier
    from ..schemas.shot import CameraMove, ContentFlag, ShotType

    problems: list[str] = []
    p = priors()
    pr = pricing()
    mods = p.get("modifiers", {})

    # --- ShotType ---
    shot_type_mods = mods.get("shot_type", {})
    for member in ShotType:
        if member.value not in shot_type_mods:
            problems.append(
                f"priors.yaml modifiers.shot_type 缺少 '{member.value}'"
                f"（ShotType 有定義但先驗沒對應，會被當成 0.0 修正）"
            )

    # --- CameraMove ---
    move_mods = mods.get("camera_move", {})
    for member in CameraMove:
        if member.value not in move_mods:
            problems.append(f"priors.yaml modifiers.camera_move 缺少 '{member.value}'")

    # --- ContentFlag ---
    flag_mods = mods.get("content_flags", {})
    for member in ContentFlag:
        if member.value not in flag_mods:
            problems.append(f"priors.yaml modifiers.content_flags 缺少 '{member.value}'")

    # --- QualityTier 要同時存在於 pricing 與 priors ---
    veo_tiers = pr.get("veo", {}).get("tiers", {})
    tier_mods = mods.get("tier", {})
    for member in QualityTier:
        if member.value not in veo_tiers:
            problems.append(
                f"pricing.yaml veo.tiers 缺少 '{member.value}'（沒有價格就無法估價）"
            )
        if member.value not in tier_mods:
            problems.append(f"priors.yaml modifiers.tier 缺少 '{member.value}'")

    # --- Extend 秒數：schema 的常數必須與 pricing.yaml 一致 ---
    from ..schemas.shot import _SECONDS_PER_EXTENSION

    yaml_ext_seconds = pr.get("veo", {}).get("extend", {}).get("seconds_per_extension")
    if yaml_ext_seconds != _SECONDS_PER_EXTENSION:
        problems.append(
            f"Extend 秒數不一致：pricing.yaml veo.extend.seconds_per_extension="
            f"{yaml_ext_seconds}，schemas/shot.py _SECONDS_PER_EXTENSION="
            f"{_SECONDS_PER_EXTENSION}。片長會算錯。"
        )

    # --- 合法的單鏡長度必須與 priors.yaml 的修正項對得上 ---
    allowed_seconds = pr.get("veo", {}).get("clip_limits", {}).get("allowed_seconds", [])
    duration_mods = mods.get("duration_seconds", {})
    for sec in allowed_seconds:
        if str(sec) not in duration_mods:
            problems.append(
                f"priors.yaml modifiers.duration_seconds 缺少 '{sec}' 秒的修正項"
            )

    # --- 兩個檔案都有「重試上限」，必須一致 ---
    guard_retries = pr.get("guardrails", {}).get("max_retries_per_shot")
    prior_retries = p.get("retry_model", {}).get("max_retries")
    if guard_retries != prior_retries:
        problems.append(
            f"重試上限不一致：pricing.yaml guardrails.max_retries_per_shot="
            f"{guard_retries}，priors.yaml retry_model.max_retries={prior_retries}。"
            f"兩邊必須相同，否則估價與實際執行會對不上。"
        )

    problems.extend(_verify_constraints(ContentFlag))
    problems.extend(_verify_principle_audience())
    return problems


# 工程日誌洩漏到使用者文案的特徵。
#
# 為什麼要用機器擋，不是寫在檔頭拜託大家遵守：
#    `principle` 是**顯示給編劇看的**，但它就在 code 旁邊，
#    改規則的人順手把「為什麼這樣改」寫進去是最自然的動作。
#    實際檢查過：三分之一的規則混著日期、鏡頭編號、回報原話 ——
#    每一條都不是誰偷懶，是那個欄位同時面對兩種讀者，寫的人分不清。
#
#    規矩寫在註釋裡會被跳過，寫成啟動檢查就跳不過。
_ENGINEERING_LEAK_PATTERNS: list[tuple[str, str]] = [
    (r"20\d{2}-\d{2}-\d{2}", "日期（改動歷程屬於 engineering_note）"),
    (r"\bs\d{3}_\d{2}\b", "鏡頭編號（使用者不知道那是什麼）"),
    (r"\.(yaml|py|tsx?|json)\b", "檔名（實作細節）"),
    (r"`[a-z_]{4,}`", "程式識別字（例如規則 id、欄位名）"),
]


def _verify_principle_audience() -> list[str]:
    """擋住工程日誌混進 `principle`（那個欄位只寫給使用者看）。

    為什麼要有這個函式？
        `constraints.yaml` 的 `principle` 會原封不動顯示在畫面上。
        如果它同時被當成「給使用者的說明」與「給開發者的修改紀錄」，
        編劇就會在 UI 上讀到帶著鏡頭編號與回報細節、對他毫無意義的句子。

        欄位分工是：
            principle          → 使用者。白話，不含日期／檔名／鏡頭編號／人名
            engineering_note   → 開發者。改動歷程、規則互動、實測細節（不進 API）
            advisory           → 使用者的行動提醒。**明確欄位**，不從 emoji 猜
            evidence           → 實測來源與數據

    設計來源：UI 文案盤點。

    不能刪：刪了之後工程日誌會再次慢慢滲回 principle，
       而那個滲入過程沒有任何一步會報錯 —— 只有使用者看得到後果。

    Returns:
        問題清單。空的代表所有 principle 都只寫給使用者。
    """
    import re

    problems: list[str] = []
    for rule in constraints().get("rules", []):
        principle = str(rule.get("principle", ""))
        rule_id = rule.get("id", "?")
        for pattern, why in _ENGINEERING_LEAK_PATTERNS:
            if re.search(pattern, principle):
                problems.append(
                    f"constraints.yaml「{rule_id}」的 principle 含"
                    f"{why} —— 那個欄位會直接顯示給編劇看，"
                    f"工程內容請移到 engineering_note。"
                )
                break
    return problems


# 約束規則的 when 區塊支援的條件鍵。
# 與 constraint_engine._matches() 的實作必須一致 ——
# 那邊遇到未知的鍵會回 False（規則永遠不觸發），這裡負責在啟動時就抓出來。
_KNOWN_TRIGGER_KEYS = {
    "always",
    "has_content_flag",
    "character_count_min",
    "character_count_max",
    "has_dialogue",
    "has_music_cue",
    "has_reference_image",
    "has_first_frame",
    "extensions_min",
    # 景深/視線規則要按鏡別觸發
    "shot_type",
}


def _verify_constraints(content_flag_enum) -> list[str]:
    """檢查 constraints.yaml 的完整性。

    這裡抓的都是「不會爆炸但會靜默失效」的錯誤：
      - 規則引用了不存在的 content_flag → 永遠不觸發，但看起來規則庫很齊全
      - 用了 constraint_engine 不認識的條件鍵 → 同上
      - id 重複 → ClickHouse 的實測統計會把兩條規則混在一起

    這些都是「文件寫得很好、實際上沒在跑」的那種錯誤，最難用眼睛發現。
    """
    problems: list[str] = []
    cfg = constraints()
    rules = cfg.get("rules", [])
    categories = cfg.get("categories", {})
    valid_flags = {f.value for f in content_flag_enum}

    seen_ids: set[str] = set()
    # 風格的 constraint_weight_overrides 只能引用真實存在的規則 id，
    # 否則那個 override 是死的 —— 檔案看起來調校得很細，實際上沒作用。
    rule_ids = {r.get("id") for r in rules}
    for preset in styles().get("presets", []):
        for ref_id in preset.get("constraint_weight_overrides", {}):
            if ref_id not in rule_ids:
                problems.append(
                    f"styles.yaml 風格 '{preset.get('id')}' 的 "
                    f"constraint_weight_overrides 引用了不存在的規則 "
                    f"'{ref_id}' —— 這個調整永遠不會生效"
                )

    default_style = styles().get("default_style")
    if default_style not in {p.get("id") for p in styles().get("presets", [])}:
        problems.append(
            f"styles.yaml default_style '{default_style}' 不在 presets 裡"
        )

    for rule in rules:
        rule_id = rule.get("id")
        if not rule_id:
            problems.append("constraints.yaml 有規則缺少 id")
            continue
        if rule_id in seen_ids:
            problems.append(
                f"constraints.yaml 規則 id 重複：'{rule_id}'"
                f"（實測統計會把兩條規則混在一起）"
            )
        seen_ids.add(rule_id)

        category = rule.get("category")
        if category not in categories:
            problems.append(
                f"constraints.yaml 規則 '{rule_id}' 的 category '{category}' "
                f"不在 categories 區塊裡（UI 分組會顯示原始代碼）"
            )

        when = rule.get("when", {})
        for key in when:
            if key not in _KNOWN_TRIGGER_KEYS:
                problems.append(
                    f"constraints.yaml 規則 '{rule_id}' 用了未知的觸發條件 "
                    f"'{key}' —— constraint_engine 不認識它，這條規則永遠不會生效"
                )

        for flag in when.get("has_content_flag", []):
            if flag not in valid_flags:
                problems.append(
                    f"constraints.yaml 規則 '{rule_id}' 引用了不存在的 "
                    f"content_flag '{flag}' —— 這條規則永遠不會觸發"
                )

        # 格式規則用 inject_template，附加句規則用 inject，兩者必須有其一
        if not rule.get("inject") and not rule.get("inject_template"):
            problems.append(
                f"constraints.yaml 規則 '{rule_id}' 沒有 inject 也沒有 "
                f"inject_template，套用了也不會產生任何效果"
            )

    return problems


def assert_config_integrity() -> None:
    """一致性檢查不過就拋例外。FastAPI startup 用這個。"""
    problems = verify_config_integrity()
    if problems:
        raise ConfigError(
            "設定檔與 schema 不同步，服務拒絕啟動：\n  - " + "\n  - ".join(problems)
        )


# ---------------------------------------------------------------------------
# 常用取值的便利函式
# ---------------------------------------------------------------------------

def default_veo_tier() -> str:
    """產品預設的畫質檔位。

    預設值必須跟著 config 走，不寫死在 code 裡 ——
       檔位下架時（例如 Lite），「沒選過檔位」的專案
       才不會掉回一個已下架的檔。
    """
    # Greenlight Local：設定頁選本機引擎 → 預設就是本機 H3 檔位
    try:
        from . import settings_store

        if settings_store.load().get("engine") == "local" and "h3_local_4step" in pricing().get("veo", {}).get("tiers", {}):
            return "h3_local_4step"
    except Exception:  # noqa: BLE001 - 設定讀不到就照原本的預設
        pass
    return str(pricing().get("veo", {}).get("default_tier", "fast_720p"))


def selectable_veo_tiers() -> list[str]:
    """使用者可以選的檔位（方案表列這些）。順序照 config。"""
    veo = pricing().get("veo", {})
    listed = veo.get("selectable_tiers")
    if listed:
        return [str(t) for t in listed]
    # config 沒列的話退回「所有沒被停用的」
    return [
        key
        for key, cfg in veo.get("tiers", {}).items()
        if cfg.get("enabled", True)
    ]


def veo_tier(tier_value: str) -> dict[str, Any]:
    """取某個畫質檔位的價格設定。查不到直接拋，不回 None。

    回 None 會讓錯誤延後到「拿 None 去乘法」的那一行才爆，
    堆疊追蹤會指向數學運算而不是真正的原因（設定檔缺項）。
    """
    tiers = pricing().get("veo", {}).get("tiers", {})
    if tier_value not in tiers:
        raise ConfigError(f"pricing.yaml 沒有畫質檔位 '{tier_value}'")
    return tiers[tier_value]


def aspect_ratio(value: str) -> dict[str, Any]:
    """取畫面比例的設定。Veo 只接受 16:9 與 9:16。"""
    ratios = pricing().get("veo", {}).get("aspect_ratios", {})
    if value not in ratios:
        available = ", ".join(ratios.keys())
        raise ConfigError(f"pricing.yaml 沒有畫面比例 '{value}'。可用的：{available}")
    return ratios[value]


def guardrails() -> dict[str, Any]:
    """預算護欄設定。"""
    return pricing().get("guardrails", {})
