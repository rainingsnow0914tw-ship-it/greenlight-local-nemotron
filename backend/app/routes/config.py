"""設定相關的唯讀 endpoint —— 風格、畫質、比例、約束規則。

這些都是 config 檔的內容，前端拿去渲染選項按鈕（設計原則：使用者只按按鈕、不打自由文字）。
"""

from __future__ import annotations

import os

from fastapi import APIRouter

from ..services import config_loader, i18n

router = APIRouter()


@router.get("/styles")
def styles() -> dict:
    """影片風格清單。

    一併回傳 difficulty_modifier —— 前端要能顯示
    「換成動畫風成功率高 15 個百分點」這種比較。
    這是本產品最反直覺也最有價值的資訊，不能藏在後端。
    """
    presets = config_loader.styles().get("presets", [])
    return {
        "default": config_loader.styles().get("default_style"),
        "presets": [
            {
                "id": p["id"],
                "label": i18n.pair(p, "label"),
                "one_liner": i18n.pair(p, "one_liner"),
                "best_for": i18n.pair(p, "best_for"),
                "caution": i18n.pair(p, "caution"),
                "difficulty_modifier": p.get("difficulty_modifier", 0.0),
            }
            for p in presets
        ],
    }


@router.get("/aspect-ratios")
def aspect_ratios() -> dict:
    """畫面比例。Veo 只支援 16:9 與 9:16，沒有其他選項。"""
    ratios = config_loader.pricing().get("veo", {}).get("aspect_ratios", {})
    return {
        "options": [
            {
                "value": key,
                "label": i18n.pair(cfg, "label"),
                "hint": i18n.pair(cfg, "hint"),
                "difficulty_modifier": cfg.get("difficulty_modifier", 0.0),
            }
            for key, cfg in ratios.items()
        ]
    }


@router.get("/tiers")
def tiers() -> dict:
    """畫質檔位與單價。"""
    veo = config_loader.pricing().get("veo", {}).get("tiers", {})
    guard = config_loader.guardrails()
    return {
        "budget_ceiling_usd": guard.get("total_budget_usd"),
        "warn_threshold_usd": guard.get("warn_threshold_usd"),
        "block_threshold_usd": guard.get("block_threshold_usd"),
        "tiers": [
            {
                "id": key,
                "label": cfg.get("label"),
                "per_second_usd": cfg.get("per_second_usd"),
                "resolution": cfg.get("resolution"),
                "use_case": cfg.get("use_case", "").strip(),
            }
            for key, cfg in veo.items()
        ],
    }


@router.get("/constraints")
def constraints() -> dict:
    """物理約束規則庫。

    前端用來在提示詞頁面展開「系統加了什麼、為什麼」——
    這是「降低門檻」跟「把人當傻瓜」的分界線：
    使用者不需要懂，但應該看得到。
    """
    cfg = config_loader.constraints()
    categories = cfg.get("categories", {})
    return {
        "categories": [
            {
                "id": key,
                "label": i18n.pair(c, "label"),
                "description": i18n.pair(c, "description"),
            }
            for key, c in categories.items()
        ],
        "rules": [
            {
                "id": r["id"],
                "category": r.get("category"),
                "principle": " ".join(str(r.get("principle", "")).split()),
                "expected_lift": r.get("expected_lift", 0.0),
                "evidence_source": r.get("evidence", {}).get("source", "unknown"),
            }
            for r in cfg.get("rules", [])
        ],
    }


@router.get("/casting-directions")
def casting_directions() -> dict:
    """選角的重抽方向按鈕，依角色類型分組。"""
    from ..schemas.casting import CharacterKind
    from ..services import casting

    return {
        kind.value: [
            {
                "id": d.direction_id,
                "label": d.label,
                "hint": d.hint,
            }
            for d in casting.available_directions(kind)
        ]
        for kind in CharacterKind
    }


@router.get("/formats")
def list_formats() -> dict:
    """敘事形式清單（劇情／紀錄片／訪談／導覽）。$0。

    跟 /styles 是兩個維度，前端要並排但講清楚差別 ——
       使用者最容易搞混的就是「紀錄片」跟「實拍寫實」不是同一件事：
       前者是用什麼方式講，後者是看起來像什麼。
       一部紀錄片可以是實拍寫實，也可以是手繪繪本。
    """
    data = config_loader.formats()
    return {
        "default": data.get("default", "drama"),
        "formats": [
            {
                "id": f.get("id"),
                "label": i18n.pair(f, "label"),
                "one_liner": i18n.pair(f, "one_liner"),
            }
            for f in data.get("formats", [])
        ],
    }


@router.get("/learning-overview")
def learning_overview() -> dict:
    """跨專案的學習迴路概況。$0，唯讀。

    為什麼放首頁：
       學習迴路是這個產品的主張。若只出現在專案內頁，
       第一個畫面（上傳框與專案列表）完全看不出這裡有一套會累積經驗的系統。
       主張要放在第一個畫面上，不然它只是文件裡的一句話。

    聚合範圍是這個使用者自己的所有專案，不是所有人的資料。
       措辭要小心：不能寫成「新使用者吃得到前面所有人累積的經驗」——
       那暗示不同使用者的資料互相餵食，是隱私問題，實際上也不是那樣：
       這是你自己歷次拍片的實測，用得越多越準、越省錢。
       多租戶時這一層要按租戶隔離，文案先講對，架構才不會被文案帶歪。

    回傳形狀必須與 `/projects/{id}/learning` 完全一致。
       前端首頁與專案內頁共用同一個 LearningPanel 組件，
       形狀一分岔那個組件就得長出兩套分支 —— 而兩套 UI 必然不同步
       （例如一邊把 `distinct_shots` 讀成 `stats["shots"]`，那格就靜默顯示 0，
       另一邊是對的，因為它是另一份 code）。
    """
    from ..services import ledger, refinery

    stats = ledger.overall_stats()
    available = bool(stats.get("available"))

    notes = None
    try:
        loaded = refinery.load_best_notes()
        if loaded and loaded.notes:
            notes = {
                "text": loaded.notes,
                "sample_size": loaded.sample_size,
                "refined_at": loaded.refined_at,
            }
    except Exception:  # noqa: BLE001
        pass  # 筆記讀不到不影響統計顯示

    return {
        "project_id": None,
        "style_id": "",
        "database": ledger.database_label(),
        "connected": available,
        "records": stats.get("total", 0) if available else 0,
        "first_pass_rate": stats.get("first_pass_rate"),
        "first_tries": stats.get("first_tries", 0),
        "rejected": stats.get("rejected", 0),
        "distinct_shots": stats.get("distinct_shots", 0),
        "spent_usd": stats.get("spent_usd", 0.0),
        "refined_notes": notes,
        "refine_threshold": refinery.MIN_SAMPLES_TO_REFINE,
    }


@router.post("/learning/refine/{style_id}")
def refine_now(style_id: str) -> dict:
    """手動觸發一次提煉：把某個風格在 ClickHouse 的實測統計提煉成技術規律筆記，
    存進 Firestore（`greenlight_craft_notes`），之後每一次分鏡都會帶著它。

    為什麼需要這條 route：
        提煉原本只在接片完成那一刻觸發（video.py 的 assemble）。
        若長時間沒有接片，樣本數早已過門檻、craft_notes 集合卻一直是空的 ——
        寫入路徑沒有任何人呼叫。
        這條 route 讓提煉不必等下一次接片；也讓「系統學到了什麼」
        可以隨時被人為刷新、被親眼看到。

    費用：一次 Gemini 呼叫（回應裡帶 `cost_usd`）。
    Demo 站：`refinery.refine()` 走 `genai_client.client()`，第一道守衛會
        直接拋 `DemoBlocked` → 402 帶說明。這裡刻意不包 except Exception，
        讓 refine() 自己回的 {"ok": False, "reason": ...} 原樣送出去
        —— 樣本不足、ClickHouse 不通，都是說得出理由的失敗，不是靜默。
    """
    from ..services import refinery

    result = refinery.refine(style_id)
    result.setdefault("style_id", style_id)
    return result


@router.get("/learning-records")
def learning_records(limit: int = 40) -> dict:
    """跨專案的原始生成紀錄。$0，唯讀。

    專案內頁那條 `/projects/{id}/learning/records` 的全域版。
    首頁要能展開看原始列，理由跟專案內頁一模一樣：
    統計可以編，帶時間戳、鏡號、模型 id 的原始列編不出來，是可信度的來源。

    不帶 project_id 就是全表最近 N 筆 —— 這正是首頁要的範圍：
       「這套系統累積到現在的樣子」，不是某一個專案的樣子。
    """
    from ..services import ledger

    data = ledger.recent_records(limit=limit)
    return {
        "project_id": None,
        **data,
        # 連線目標也攤開 —— 「它真的連著一個外部資料庫」本身就是證據
        "endpoint": ledger.endpoint_label(),
    }


@router.get("/runtime")
def runtime_mode() -> dict:
    """這個部署是什麼身分。$0，唯讀。

    為什麼前端需要知道：
       上傳本身不花錢（只是讀檔＋章節偵測），所以 demo 站的訪客
       丟得進去、也看得到章節偵測結果，然後在「解析」那一步撞牆。
       那個體感是「這東西壞了」，不是「這是展示版」——
       中途撞牆比一開始就說清楚糟得多。

       所以前端要能在畫面上先講明白，而不是等使用者撞上去。
    """
    from ..services import demo_mode

    return {
        "demo_mode": demo_mode.is_demo(),
        # 展示版把已生成的成果攤開，但不再呼叫任何 AI、也不產生新檔案。
        "can_generate": not demo_mode.is_demo(),
        "can_upload": not demo_mode.is_demo(),
    }
