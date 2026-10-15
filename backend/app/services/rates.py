"""AI 費用的唯一算法：哪個模型、每百萬 tokens 多少錢、這次花了多少。

為什麼要有：原版 15 個地方各自查 pricing.yaml、各自帶一組備用價格（2.0／12.0、0.3／2.5、1.25／10.0…）。
換成 Nemotron 之後，各代理傳進來的還是 Google 的模型名，費用照 Google 的價錢算 ——
實測劇本解析經 Nemotron 跑完，畫面上寫「gemini-3.1-pro、$0.0256」，兩個都是錯的。
費用要誠實（這是 Greenlight 的賣點：開拍前就說要花多少），所以收成一個家。
"""

from __future__ import annotations

from typing import Any

from . import config_loader


def model_used(response: Any, requested: str) -> str:
    """這次真的是哪個模型回答的：Nemotron 轉接會在回應上標 model_version；Google 的照代理傳的名字。"""
    return getattr(response, "model_version", None) or requested


def per_1m(model_id: str) -> tuple[float, float]:
    """(輸入, 輸出) 每百萬 tokens 美元。先查 Nemotron，再查 Google 各檔；都查不到回 (0, 0) 並由呼叫端照實顯示。"""
    p = config_loader.pricing()
    nem = (p.get("nemotron") or {}).get("models", {}).get(model_id)
    if nem:
        return float(nem["input_per_1m_usd"]), float(nem["output_per_1m_usd"])
    for cfg in (p.get("gemini") or {}).get("tiers", {}).values():
        if cfg.get("model_id") == model_id:
            return float(cfg.get("input_per_1m_usd", 0)), float(cfg.get("output_per_1m_usd", 0))
    return 0.0, 0.0


def rate_cfg(model_id: str) -> dict:
    """給原版各代理用的形狀：{"input_per_1m_usd", "output_per_1m_usd"}；查不到回空 dict（代理照舊用它的備用價）。"""
    i, o = per_1m(model_id)
    return {"input_per_1m_usd": i, "output_per_1m_usd": o} if (i or o) else {}


def estimate_cfg(action: str, google_cfg: dict) -> dict:
    """開拍前估價：這個動作設成 Nemotron 就用 Nemotron 的價，設成 Google 就用代理原本查到的那一檔。"""
    model = model_for_action(action, google_cfg.get("model_id", ""))
    return rate_cfg(model) or google_cfg


def cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """這次呼叫花了多少美元（四捨五入到 0.0001）。"""
    i, o = per_1m(model_id)
    return round(input_tokens / 1_000_000 * i + output_tokens / 1_000_000 * o, 4)


def model_for_action(action: str, google_default: str) -> str:
    """還沒呼叫之前（估價用）：這個動作會由哪個模型跑 —— 依設定頁；設成 Google 就用代理原本的模型名。"""
    from . import settings_store

    _, cfg = settings_store.role_for(action)
    return cfg["model"] if cfg.get("provider") == "nebius" else google_default
