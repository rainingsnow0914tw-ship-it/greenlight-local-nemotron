"""Gemini（Vertex）client 的唯一出口。

為什麼要抽出來：

   同一段四行的 client 建構若複製在多個檔案裡，只有 timeout 數字不同，
   那些數字很容易全都是錯的 —— 例如寫 900_000 或 600_000 毫秒，
   也就是 **900 秒 / 600 秒**，而 Cloud Run 的請求上限**就是 900 秒**。

   兩個數字一樣的後果不是「等久一點」，是**逾時保護等於不存在**：
   請求會一路撐到平台把整個請求砍掉（504），
   例外處理沒機會跑、狀態存不下、ledger 一筆都沒有。
   （實測：連續多次 899.999 秒的 504，log 裡沒有任何一行是應用程式印的。）

   複製多份的東西不可能同步 —— 抽成一個出口之後，
   「所有對外呼叫都必須在平台砍掉之前自己收手」這條規則才有地方住。

## timeout 怎麼定（用真實 log 定的，不是猜的）

    endpoint                     n     max      p50
    casting/rounds              28   883.2s   110.7s   ← 差 17 秒就 504
    plan-shots                  10    93.8s    58.9s
    parse                        1    67.8s    67.8s
    screening                    1    62.8s    62.8s
    prompts/rewrite              2    15.2s    15.2s

`casting/rounds` 那個 883 秒是離群值（p50 只有 110 秒），成因未查清
   —— log 裡沒有重試或 429 的警告，所以是圖片生成本身慢。
   **這裡刻意不為了遷就它把上限拉到 900** ：那等於回到「保護等於不存在」。
   420 秒把「等十五分鐘然後 504、什麼都沒有」換成
   「等七分鐘拿到明確錯誤、可以重試」，而重試很可能 110 秒就過了。
"""

from __future__ import annotations

import json
import os
from typing import Any

# 這些數字的共同上限：Cloud Run 請求上限 900 秒。
#    任何超過它的值都是假的保護 —— 平台會先動手，而且是最糟的那種動手法。
#    這裡最大值取 420，剩下的餘裕留給例外處理、存狀態、寫 ledger、回應。
LONG_MS = 420_000
"""劇本解析 / 分鏡 / 選角 / 內容預審 —— 實測 60–110 秒的重活。"""

QUICK_MS = 180_000
"""提示詞 / 字幕翻譯 / 看片檢核 / 音色分析 / 提煉 —— 實測 20 秒內。"""


def client(timeout_ms: int = QUICK_MS, action: str = "這一步") -> Any:
    """依設定頁，回傳這個動作該用的 AI client。

    Greenlight Local：不再用作者的 Google 雲端帳號。每個動作名稱（解析劇本、分鏡、產生提示詞…）
    在設定頁對到一個角色，角色選 Nemotron（使用者的 Nebius 金鑰）或 Google（使用者的 Google 金鑰）。
    各代理照舊呼叫 `client.models.generate_content(...)`，不用改一行 ——
    選 Nemotron 時回傳的是 `_NemotronClient`，長得跟 Google 的 client 一樣（見下面）。

    **Demo 模式仍在這裡擋下**：所有 AI 呼叫都經過這個出口。

    Args:
        timeout_ms: 單次呼叫上限（**毫秒**）。用 LONG_MS / QUICK_MS。
        action: 動作名稱 —— 決定用哪個 AI（settings_store.ROLES），也用在被擋時的提示。
    """
    from . import demo_mode, settings_store

    role, cfg = settings_store.role_for(action)
    demo_mode.guard(action, model=cfg.get("provider", "AI"))

    if cfg.get("provider") == "nebius":
        return _NemotronClient(cfg["model"], settings_store.key("nebius"), timeout_ms / 1000, action)

    if cfg.get("provider") == "google":
        from google import genai
        from google.genai import types

        key = settings_store.key("google")
        if not key:
            raise PermissionError(f"「{action}」is set to Google, but no Google API key is saved — add yours in Settings")
        return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))

    raise NotImplementedError(f"「{action}」uses provider {cfg.get('provider')!r}, which is not available yet")


# ── Nemotron 轉接：讓各代理以為自己在跟 Google 的 client 說話 ─────────────────────
#
# 為什麼用轉接而不是改每個代理：九個代理、十幾個呼叫點都寫成 Google SDK 的樣子
# （generate_content／generate_content_stream、config 帶 system_instruction／response_schema…）。
# 一個一個改等於複製十幾份同樣的轉換 —— 收在這一個出口，規則只有一個家。
#
# 只轉文字：Nemotron 看不到圖、聽不到聲音。內容裡有圖或聲音 → 明講「這個角色要換成 Google」，不默默丟掉。

class _Usage:
    """模仿 Google 的 usage_metadata（各代理用它算用量）。"""

    def __init__(self, u: dict, model_version: str = ""):
        self.model_version = model_version     # 讓只拿到 usage_metadata 的代理也查得到真的模型
        self.prompt_token_count = u.get("prompt_tokens", 0) or 0
        self.candidates_token_count = u.get("completion_tokens", 0) or 0
        self.total_token_count = u.get("total_tokens", 0) or (self.prompt_token_count + self.candidates_token_count)
        self.retried_without_thinking = bool(u.get("retried_without_thinking"))


class _Response:
    def __init__(self, text: str, usage: dict, parsed=None, model_version: str = ""):
        self.text = text
        self.parsed = parsed
        self.usage_metadata = _Usage(usage, model_version)
        self.model_version = model_version     # 真的回答的模型（rates.model_used 讀它算費用）


def _to_text(contents, action: str) -> str:
    """Google SDK 的 contents（字串／字串清單／Content／Part）→ 純文字；有圖或聲音就停下來講清楚。"""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, (list, tuple)):
        return "\n\n".join(_to_text(c, action) for c in contents)
    parts = getattr(contents, "parts", None)
    if parts is not None:
        return _to_text(list(parts), action)
    if getattr(contents, "text", None):
        return contents.text
    if getattr(contents, "inline_data", None) or getattr(contents, "file_data", None):
        raise NotImplementedError(f"「{action}」sends images or audio, which Nemotron cannot read — "
                                  "set this step to Google in Settings")
    return str(contents)


def _extract_json(text: str) -> str:
    """推理模型可能先吐 <think>…</think> 或包一層 ```json —— 只取第一個 { 到最後一個 }。"""
    import re

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    return text[a:b + 1] if a >= 0 and b > a else text


class _NemotronModels:
    def __init__(self, model: str, key: str, timeout_s: float, action: str):
        self.model, self.key, self.timeout_s, self.action = model, key, timeout_s, action

    def _messages(self, contents, config):
        system = getattr(config, "system_instruction", None) if config else None
        system = _to_text(system, self.action) if system else ""
        schema = getattr(config, "response_schema", None) if config else None
        if schema is not None:
            js = schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema
            system += ("\n\nReturn ONLY one JSON object (no prose, no code fence) that matches this JSON schema:\n"
                       + json.dumps(js, ensure_ascii=False))
        elif config and getattr(config, "response_mime_type", None) == "application/json":
            system += "\n\nReturn ONLY one JSON object, no prose."
        msgs = [{"role": "system", "content": system.strip()}] if system.strip() else []
        return msgs + [{"role": "user", "content": _to_text(contents, self.action)}], schema

    def generate_content(self, model=None, contents=None, config=None, **_):
        """`model` 參數被忽略：用哪個 Nemotron 由設定頁決定（各代理傳的是 Google 的模型名）。"""
        from . import nebius_client

        msgs, schema = self._messages(contents, config)
        temperature = getattr(config, "temperature", None) if config else None
        max_tokens = getattr(config, "max_output_tokens", None) if config else None
        text, usage = nebius_client.chat(msgs, self.model, self.key,
                                         temperature=0.4 if temperature is None else temperature,
                                         max_tokens=max_tokens or nebius_client.MAX_OUTPUT, timeout_s=self.timeout_s)
        parsed = None
        if schema is not None or (config and getattr(config, "response_mime_type", None) == "application/json"):
            text = _extract_json(text)
            if schema is not None and hasattr(schema, "model_validate_json"):
                try:
                    parsed = schema.model_validate_json(text)
                except Exception:      # 驗不過就讓代理自己用 .text 解析、走它原本的錯誤處理
                    parsed = None
        return _Response(text, usage, parsed, model_version=self.model)

    def generate_content_stream(self, model=None, contents=None, config=None, **_):
        """各代理用串流是為了不被逾時切斷；這裡底層已經在串流，整段收完一次交出（代理只讀 .text 與最後的用量）。"""
        yield self.generate_content(model=model, contents=contents, config=config)


class _NemotronClient:
    def __init__(self, model: str, key: str, timeout_s: float, action: str):
        self.models = _NemotronModels(model, key, timeout_s, action)
