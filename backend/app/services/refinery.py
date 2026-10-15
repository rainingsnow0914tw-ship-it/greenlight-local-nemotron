"""提煉層：把生成紀錄變成「這樣寫成功率高」的可用知識。

提煉的對象是技術規律，不是使用者：

    系統變聰明的地方在於提升成功率，不是記住某個作者的偏好。
    所以這一層學的是「什麼樣的鏡頭與寫法會失敗」，不是「這個人偏好什麼」。
    知識因此是跨專案的：不存在專案狀態裡。
    換一部片、換一個使用者，學到的東西照樣適用。

三層記憶：

    層級                         生命週期
    ─────────────────────────────────────────
    generation_ledger 逐筆       18 個月 TTL
    craft_notes 技術規律         每次提煉覆蓋
    constraints.yaml 規則        永久、人決定
    memory_block() 注入          每次跑帶著

四條紀律：

  1. 提煉是重寫覆蓋，不是累積：整合舊筆記＋新資訊、不重複、不冗長，
     並且有硬字數上限。累積式記憶最後會膨脹到沒人讀得完，包括模型。

  2. 樣本不足要大聲說，不能靜默跳過。零筆時整個 job 空轉、回 HTTP 200
     卻什麼都沒做，筆記停在幾個月前沒有人發現。靜默的零筆是最危險的失敗。

  3. 提煉失敗不能拖死主流程。它是加值不是必要路徑。

  4. 讀取要快。跑的時候帶著記憶 = 每次規劃都要讀，
     慢一秒就是整個產品慢一秒。

這一層刻意不做的事：自動改 constraints.yaml 的 expected_lift

有實測了就覆蓋估計值看起來很自然，但那是錯的，理由在
ledger.constraint_observations() 的說明裡：

    約束是依鏡頭內容自動觸發的。會套用 hand_anatomy 的鏡頭，
    本來就是有手部特寫的鏡頭，那種鏡頭先天就難。
    直接比較「有套用 vs 沒套用」會得到「套了約束反而比較差」的反向結論。

那是選擇偏誤，不是效益。要算因果得做 A/B（隨機停用某條約束再比較），
本版沒有那個實驗開關。

所以提煉層只產出知識文字，數字要不要改由人決定。
一個會自己改自己成功率參數、而且改錯方向的系統，比不會學的系統危險。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import rates
from . import api_retry, config_loader, ledger

logger = logging.getLogger(__name__)

# 低於這個筆數不提煉。
#
# 為什麼是 20：提煉的價值來自「分組之後每組還有幾筆」。20 筆分成
# 鏡別 × 運鏡兩個維度之後，每格大概 2–4 筆，已經看得出方向，
# 但還不到能下結論。低於 20 連方向都是雜訊，讓 LLM 去讀只會生出
# 聽起來很篤定的廢話，那比沒有知識更糟。
MIN_SAMPLES_TO_REFINE = 20

# 提煉結果的硬上限。
# 這段文字會被塞進每一次 shot_planner 與 prompt_writer 的
# system instruction，長度直接換算成每次規劃的成本與服從度損失。
MAX_NOTES_CHARS = 1_200

_BREAKDOWN_DIMENSIONS = [
    "shot_type",
    "camera_move",
    "character_count",
    "has_dialogue",
    "has_first_frame",
]


def _notes_dir() -> Path:
    """跟 project_store / media_store 用同一個家目錄，方便整包備份或清空。

    但不在 projects/ 底下：技術規律是跨專案的資產，
    刪掉一個專案不該把系統學到的東西一起刪掉。

    只有本機模式走這裡。雲端一定要走 Firestore，見 `_use_firestore()`。
    """
    from .env_boot import data_home
    root = Path(os.getenv("GREENLIGHT_DATA_DIR") or data_home())
    path = root / "craft_notes"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 存哪裡
# ---------------------------------------------------------------------------
#
# 本機 JSON 存在 ~/.greenlight/craft_notes/，但在 Cloud Run 上那是容器檔案系統：
# 換一個 revision 就沒了、每個 instance 各存各的、instance 回收也沒了。
#
# 對照同一個專案的另外兩個儲存層：
#
#     專案狀態   project_store   GREENLIGHT_STORE=firestore   活得下來
#     媒體檔案   media_store     GCS_BUCKET=…                 活得下來
#     提煉筆記   refinery        ~/.greenlight/craft_notes/   每次 deploy 清空
#
# 代價不是「筆記不見了」而已：`shot_planner` 用 `memory_block(style_id)`
# 把學到的規律注入每一次規劃。存不住的意思是那裡永遠讀到空字串，
# 「系統會學、下次帶著經驗」在雲端就不會發生，而且是靜默的：
# 沒有錯誤、沒有 log，只是每次都從零開始。所以雲端一律走 Firestore。
#
# 用跟 project_store 同一個環境變數（`GREENLIGHT_STORE`），不要多發明一個：
# 兩個開關管同一件事「這是雲端還是本機」，遲早會有人只設了其中一個。

_NOTES_COLLECTION = "greenlight_craft_notes"
_db_cache: Any = None


def _use_firestore() -> bool:
    return os.getenv("GREENLIGHT_STORE", "local").lower() == "firestore"


def _db() -> Any:
    """Firestore client（快取一份，每次建都要開連線）。"""
    global _db_cache
    if _db_cache is None:
        from google.cloud import firestore  # 延遲 import，本機開發不需要

        _db_cache = firestore.Client(project=os.getenv("GCP_PROJECT_ID"))
    return _db_cache


def _from_dict(data: dict[str, Any], style_id: str) -> CraftNotes:
    return CraftNotes(
        style_id=data.get("style_id", style_id),
        notes=data.get("notes", ""),
        sample_size=data.get("sample_size", 0),
        refined_at=data.get("refined_at", ""),
        evidence=data.get("evidence", {}),
    )


@dataclass
class CraftNotes:
    """一個風格學到的技術規律。"""

    style_id: str
    notes: str = ""
    sample_size: int = 0
    refined_at: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "style_id": self.style_id,
            "notes": self.notes,
            "sample_size": self.sample_size,
            "refined_at": self.refined_at,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# 蒐證：$0，純 ClickHouse 聚合
# ---------------------------------------------------------------------------

def gather_evidence(style_id: str) -> dict[str, Any]:
    """把 ledger 聚合成提煉的原料。不呼叫任何 AI，零成本。

    這一步單獨拆出來是有用的：它本身就是 UI 上「系統學到什麼」那一頁的
    資料來源，而且可以在不花錢的情況下先看有沒有東西值得提煉。
    """
    started = time.monotonic()

    overall = ledger.overall_stats(style_id)
    if not overall.get("available"):
        return {"available": False, "reason": "ClickHouse 連不上"}

    breakdowns: dict[str, list[dict[str, Any]]] = {}
    for dim in _BREAKDOWN_DIMENSIONS:
        rows = ledger.feature_breakdown(dim, style_id)
        if rows:
            breakdowns[dim] = rows

    # 校準要排除回填的列，它們的預測值是事後重算的。
    calibration = ledger.calibration_report(style_id)

    evidence = {
        "available": True,
        "style_id": style_id,
        "overall": overall,
        "breakdowns": breakdowns,
        "calibration": calibration,
        "gathered_in_ms": int((time.monotonic() - started) * 1000),
    }
    logger.info(
        "蒐證完成：%s 風格 %s 筆首次生成、%d ms",
        style_id,
        overall.get("first_tries", 0),
        evidence["gathered_in_ms"],
    )
    return evidence


def _format_evidence(evidence: dict[str, Any]) -> str:
    """把統計排成給模型讀的文字。

    刻意用「N 筆裡過 M 筆」而不是只給百分比：樣本數要跟數字綁在一起，
    否則模型會把「2 筆裡過 1 筆」講成「五成的通過率」那種聽起來很篤定的話。
    """
    o = evidence["overall"]
    lines = [
        f"整體：{o['first_tries']} 次首度生成，過了 {o['first_pass']} 次"
        f"（{o['first_pass_rate']:.0%}）；被打回重拍 {o['rejected']} 次；"
        f"共花 ${o['spent_usd']}。",
        "",
        "按鏡頭特徵分組的一次過關率（由低到高）：",
    ]
    for dim, rows in evidence["breakdowns"].items():
        label = rows[0]["dimension_label"]
        parts = [
            f"{r['bucket']} {r['pass_rate']:.0%}（{r['sample_size']} 筆）" for r in rows
        ]
        lines.append(f"  {label}：" + "、".join(parts))

    if evidence.get("calibration"):
        lines.append("")
        lines.append("預測 vs 實際：")
        for c in evidence["calibration"]:
            lines.append(
                f"  預測 {c['predicted_range']} 的那一批，實際過了"
                f" {c['actual_rate']:.0%}（{c['sample_size']} 筆）"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 提煉：一次 Flash 呼叫，約 $0.001
# ---------------------------------------------------------------------------

_SYSTEM = """You turn generation statistics into short, actionable craft rules
for an AI video shot planner and prompt writer.

You are NOT summarising a user's preferences. You are extracting technical
regularities: which kinds of shots fail, and what a planner should do
differently because of it.

Hard rules:
- Every claim must be traceable to a number you were given. Never generalise
  beyond the data. If a bucket has 2 samples, say so and hedge accordingly.
- Write rules the planner can ACT on ("prefer static framing when the shot has
  dialogue"), not observations it cannot use ("dolly shots are harder").
- Never name a character, a place, or anything specific to one screenplay.
  These notes are injected into every future project, for every user.
- If the evidence does not support a rule, write fewer rules. An empty-handed
  honest answer is worth more than a confident guess.
- No preamble, no headings, no emoji. Plain lines, each one a rule.
- Traditional Chinese output."""


def _client():
    """Vertex genai client。timeout 統一在 services/genai_client 管。

    提煉用短逾時：timeout 若跟 Cloud Run 的請求上限一樣長，等於沒有保護。
    """
    from . import genai_client

    return genai_client.client(genai_client.QUICK_MS, action="提煉經驗")


def _model() -> str:
    pricing = config_loader.pricing().get("gemini", {})
    tier_key = pricing.get("assignments", {}).get("refinery", "flash")
    tiers = pricing.get("tiers", {})
    return tiers.get(tier_key, {}).get("model_id", "gemini-3.6-flash")


def refine(style_id: str) -> dict[str, Any]:
    """把統計提煉成技術規律，覆蓋舊的 craft_notes。

    回傳 {"ok": bool, "reason": str, "notes": str, "cost_usd": float}。

    樣本不足時不是靜默跳過，是回一個說得出理由的失敗：
    靜默跳過會讓排程空轉很久沒人發現。
    """
    evidence = gather_evidence(style_id)
    if not evidence.get("available"):
        logger.warning("提煉中止：%s", evidence.get("reason"))
        return {"ok": False, "reason": evidence.get("reason", "蒐證失敗"), "cost_usd": 0.0}

    n = evidence["overall"].get("first_tries", 0)
    if n < MIN_SAMPLES_TO_REFINE:
        reason = (
            f"樣本只有 {n} 筆，低於門檻 {MIN_SAMPLES_TO_REFINE}，不提煉。"
            "資料太少時提煉出來的規律是雜訊，而它會被注入每一次規劃 —— "
            "錯的知識比沒有知識貴。"
        )
        logger.warning("提煉中止（%s 風格）：%s", style_id, reason)
        return {"ok": False, "reason": reason, "sample_size": n, "cost_usd": 0.0}

    previous = load_notes(style_id)
    prompt = f"""這是一個 AI 影片生成系統的實測統計，風格是「{style_id}」。

{_format_evidence(evidence)}

現有的規律筆記（你要更新這一份，不是往後面加）：
{previous.notes if previous and previous.notes else "（尚未累積）"}

寫一份新的規律筆記，上限 {MAX_NOTES_CHARS} 字，要求：
- 整合舊筆記與新統計，不重複、不冗長
- 每一條都要規劃器做得到，並且附上支撐它的數字與樣本數
- 樣本少的地方要明講「樣本還少」，不要寫成定論
- 只輸出規律本身，一行一條"""

    from google.genai import types

    model = _model()
    # client 一定要先存進變數再用：
    # 寫成 `_client().models.generate_content(...)` 的話 Client 是暫時物件，
    # CPython 取完 `.models` 屬性就把它回收，SDK 的 `__del__` 順手關掉 httpx 連線，
    # 然後才真的送請求，得到「Cannot send a request, as the client has been closed」。
    client = _client()
    try:
        response = api_retry.governed(
            model,
            lambda: client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM,
                    # 這是在整理事實，不是創作，溫度壓低
                    temperature=0.2,
                    # 8_192 不是 2_048：模型會思考，思考 token 跟輸出共用這個上限。
                    # 2_048 時實測輸出被截成碎片，筆記本身才三百多字上限卻已到頂。
                    # 筆記硬上限仍是 MAX_NOTES_CHARS（存檔前截）。
                    max_output_tokens=8_192,
                ),
            ),
            label="refinery",
        )
    except Exception as exc:  # noqa: BLE001 - 提煉是加值，不能拖死任何主流程
        logger.error("提煉呼叫失敗：%s", exc)
        return {"ok": False, "reason": f"模型呼叫失敗：{exc}", "cost_usd": 0.0}

    text = (response.text or "").strip()[:MAX_NOTES_CHARS]
    if not text:
        logger.warning("提煉回了空字串，保留舊筆記不覆蓋")
        return {"ok": False, "reason": "模型沒有回傳內容", "cost_usd": 0.0}

    meta = getattr(response, "usage_metadata", None)
    in_tok = getattr(meta, "prompt_token_count", 0) or 0
    out_tok = getattr(meta, "candidates_token_count", 0) or 0
    model = rates.model_used(meta, model)      # 費用照真的回答的模型算（rates.py 說明為什麼）
    cfg = rates.rate_cfg(model)
    cost = (
        in_tok * float(cfg.get("input_per_1m_usd", 0.3))
        + out_tok * float(cfg.get("output_per_1m_usd", 2.5))
    ) / 1_000_000

    notes = CraftNotes(
        style_id=style_id,
        notes=text,
        sample_size=n,
        refined_at=datetime.now(timezone.utc).isoformat(),
        evidence=evidence["overall"],
    )
    save_notes(notes)
    logger.info("提煉完成：%s 風格、%d 字、來自 %d 筆、$%.5f", style_id, len(text), n, cost)

    return {
        "ok": True,
        "notes": text,
        "sample_size": n,
        "cost_usd": round(cost, 5),
        "chars": len(text),
    }


# ---------------------------------------------------------------------------
# 存取
# ---------------------------------------------------------------------------

def save_notes(notes: CraftNotes) -> None:
    """覆蓋寫入。雲端走 Firestore、本機走 JSON 檔（見上面那段說明）。

    本機是原子寫入：先寫 tmp 再 rename，跟 project_store 同一個做法。
    """
    if _use_firestore():
        _db().collection(_NOTES_COLLECTION).document(notes.style_id).set(
            notes.to_dict()
        )
        logger.info(
            "craft_notes 已存進 Firestore：%s（%d 筆樣本）",
            notes.style_id,
            notes.sample_size,
        )
        return

    path = _notes_dir() / f"{notes.style_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(notes.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


def load_notes(style_id: str) -> CraftNotes | None:
    """讀某個風格的規律筆記。沒有就回 None。

    讀不到一律回 None 讓呼叫端照常跑（記憶是加值不是必要路徑），
    但失敗要記 log：「沒有筆記」與「讀筆記壞了」在畫面上
    長得一模一樣，只有 log 分得出來。
    """
    try:
        if _use_firestore():
            doc = _db().collection(_NOTES_COLLECTION).document(style_id).get()
            if not doc.exists:
                return None
            return _from_dict(doc.to_dict() or {}, style_id)

        path = _notes_dir() / f"{style_id}.json"
        if not path.exists():
            return None
        return _from_dict(json.loads(path.read_text(encoding="utf-8")), style_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("讀 craft_notes 失敗（當成沒有）：%s", exc)
        return None


# ---------------------------------------------------------------------------
# 帶著記憶跑：注入 system instruction
# ---------------------------------------------------------------------------

def load_best_notes() -> CraftNotes | None:
    """跨專案時要顯示哪一份筆記：取樣本數最多的那份。

    為什麼需要這個：
       筆記是按風格提煉的（寫實風學到的東西不能拿去教動畫風），
       但首頁沒有專案、也就沒有風格。與其在首頁把這一區留白，
       不如顯示證據最強的那一份：首頁要回答的是「這個系統學到了什麼」，
       不是「這個風格學到了什麼」。

    挑 sample_size 最大的，不是最新的：首頁是拿來取信於人的，
    樣本數才是說服力的來源。
    """
    best: CraftNotes | None = None
    try:
        if _use_firestore():
            candidates = [
                _from_dict(doc.to_dict() or {}, doc.id)
                for doc in _db().collection(_NOTES_COLLECTION).stream()
            ]
        else:
            candidates = [
                n
                for n in (load_notes(p.stem) for p in _notes_dir().glob("*.json"))
                if n is not None
            ]
    except Exception as exc:  # noqa: BLE001
        logger.error("列出 craft_notes 失敗（當成沒有）：%s", exc)
        return None

    for notes in candidates:
        if not notes.notes:
            continue
        if best is None or notes.sample_size > best.sample_size:
            best = notes
    return best


def memory_block(style_id: str) -> str:
    """組一段給 agent 帶著跑的記憶。沒有筆記就回空字串。

    一定要標明樣本數與提煉時間。模型讀到「這些是實測規律」
    與讀到「這些是 15 筆樣本歸納的初步觀察」，該有的服從度不一樣。
    不標的話，一條 2 筆樣本推出來的規律會被當成硬規則照做。

    回空字串時呼叫端什麼都不用做：冷啟動、ClickHouse 掛掉、
    樣本不足，三種情況的表現都是「就照原本的方式跑」。
    """
    notes = load_notes(style_id)
    if not notes or not notes.notes:
        return ""

    when = notes.refined_at[:10] if notes.refined_at else "未知時間"
    return (
        f"\n\n【實測學到的規律 —— {notes.sample_size} 筆生成紀錄，"
        f"{when} 提煉】\n"
        f"{notes.notes}\n"
        "（這是統計歸納，不是硬性規則。與物理約束衝突時以物理約束為準。）"
    )
