"""導演的工具箱：Nemotron 寫分鏡時自己決定要不要用、什麼時候用。

為什麼給工具，而不是把答案塞進提示詞：
- 量得出來的東西（一句台詞要幾秒、這份分鏡過不過得了關卡）交給模型「猜」，它每一場都猜錯
  （學習記憶庫：規則 8 秒數不夠，9 場第一回合 9 場都犯）。給它一把尺，讓它要用的時候自己量。
- 自己先檢查再交件：以前是交出整份 → 被關卡擋 → 整份重寫，一來一回就是一整個回合、幾萬 token。
  現在它可以交件前自己跑關卡，在同一個回合裡改好。
- 用了哪些工具、用了幾次都記下來 —— 示範時看得到「它自己量了五次秒數、交件前自檢兩次」。

工具只回事實，不替它做決定：check_shots 回關卡的原文，怎麼改是它的事。
"""

import json
import pathlib
import re

from . import gates

ROOT = pathlib.Path(__file__).parent

SCHEMAS = [
    {"type": "function", "function": {
        "name": "speech_seconds",
        "description": "Measured minimum shot length for lines of dialogue: speaking time at the render model's "
                       "calibrated rate plus time for the action to land. Use it whenever you set a duration.",
        "parameters": {"type": "object", "properties": {
            "lines": {"type": "array", "items": {"type": "string"}, "description": "the exact dialogue text of each line"}},
            "required": ["lines"]}}},
    {"type": "function", "function": {
        "name": "check_shots",
        "description": "Run the pre-render gates on a draft shot list and get every ERROR and WARN back. "
                       "Call it on your draft before giving your final answer, fix what it reports, and check again if needed.",
        "parameters": {"type": "object", "properties": {
            "shots": {"type": "array", "items": {"type": "object"}, "description": "the full draft shot list"}},
            "required": ["shots"]}}},
    {"type": "function", "function": {
        "name": "lookup_rule",
        "description": "Read the full text of a directing rule, e.g. 'rule 21'.",
        "parameters": {"type": "object", "properties": {"rule": {"type": "string"}}, "required": ["rule"]}}},
]


def run(name: str, args_json: str, scene: dict) -> str:
    """執行一個工具，回傳給模型看的文字。參數壞掉就照實說，讓它重叫，不要整場崩掉。"""
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError as e:
        return f"error: arguments are not valid JSON ({e})"
    if name == "speech_seconds":
        rows = []
        for line in args.get("lines") or []:
            need = gates.speech_seconds(line) + gates.ACTION_TAIL
            note = "  (over 10 s: split at a sentence end)" if need > gates.MAX_SECONDS else ""
            rows.append(f"{need:.1f} s  「{line}」{note}")
        return "\n".join(rows) or "error: give at least one line"
    if name == "check_shots":
        shots = args.get("shots")
        if not isinstance(shots, list) or not shots:
            return "error: give the full shot list as 'shots'"
        issues = gates.check(scene, shots)
        errors = sum(i["level"] == "ERROR" for i in issues)
        head = "All gates pass." if not errors else f"{errors} ERROR(s) — fix all of them:"
        return head + ("\n" + gates.summarize(issues) if issues else "")
    if name == "lookup_rule":
        n = re.search(r"\d+", args.get("rule") or "")
        if not n:
            return "error: name a rule like 'rule 21'"
        from ..services import knowledge   # 使用者知識庫那一份（安裝檔外面、隨時可改）
        text = knowledge.read_text("directing_rules.md")
        m = re.search(rf"^{n.group()}\. .*?(?=^\d+\. |^## |\Z)", text, re.S | re.M)
        return m.group().strip() if m else f"rule {n.group()} not found"
    return f"error: unknown tool {name!r}"
