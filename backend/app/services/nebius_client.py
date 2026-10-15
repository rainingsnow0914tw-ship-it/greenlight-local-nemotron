"""Nebius Token Factory（Nemotron）的唯一出口：OpenAI 相容、一律串流。

從 nemotron-director 的 director.py 搬過來、已經在雲端實測過的做法（–）：
- 一定要串流：寫一整場分鏡要先想很久再吐一大段 JSON，不串流的連線一分多鐘沒資料會被中間的代理切斷。
- 思考吃 max_tokens：平台實際輸出上限 32768（給 48000 也一樣），reasoning_budget 在長回合不一定被遵守。
  撞到上限（finish_reason=length）就同一回合不思考再問一次，用量兩次加總回報 —— 不浪費一整回合，數字也是真的。
- 回覆是空的就明講（思考吃光額度），不讓下游拿空字串去解析、在別處莫名壞掉。

金鑰來自使用者自己的設定（settings_store），不讀環境變數 —— 本機版不用作者的金鑰。
"""

from __future__ import annotations

import json

import httpx

BASE = "https://api.tokenfactory.nebius.com/v1"
MAX_OUTPUT = 32768                 # 平台實際上限（見檔頭）
REASONING_BUDGET = 12000           # 正常情況的思考上限；撞頂才關掉思考重問


class EmptyReply(ValueError):
    """思考把額度用光、沒有任何回覆內容。"""


def _stream(body: dict, key: str, timeout_s: float):
    """送一次串流請求，回傳 (文字, usage, finish_reason)。連線層的錯重試一次。"""
    for attempt in range(2):
        content, usage, finish = [], {}, None
        try:
            with httpx.stream("POST", f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=httpx.Timeout(60, read=timeout_s)) as r:
                if r.status_code != 200:
                    r.read()
                    r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    ev = json.loads(line[6:])
                    usage = ev.get("usage") or usage
                    for ch in ev.get("choices") or []:
                        content.append((ch.get("delta") or {}).get("content") or "")
                        finish = ch.get("finish_reason") or finish
            return "".join(content), usage, finish
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError):
            if attempt:
                raise
    raise RuntimeError("unreachable")


def chat(messages: list, model: str, key: str, temperature: float = 0.4, max_tokens: int = MAX_OUTPUT,
         timeout_s: float = 300) -> tuple[str, dict]:
    """問一次 Nemotron，回傳 (文字, usage)。usage 是 OpenAI 格式（prompt_tokens／completion_tokens／total_tokens）。"""
    if not key:
        raise PermissionError("No Nebius API key — add yours in Settings")
    body = {"model": model, "messages": messages, "temperature": temperature,
            "max_tokens": min(max_tokens, MAX_OUTPUT), "stream": True, "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_budget": REASONING_BUDGET}}
    text, usage, finish = _stream(body, key, timeout_s)
    if finish == "length":
        retry = dict(body, chat_template_kwargs={"enable_thinking": False})
        text, usage2, finish = _stream(retry, key, timeout_s)
        usage = {k: (usage.get(k) or 0) + (usage2.get(k) or 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        usage["retried_without_thinking"] = True
    if not text.strip():
        raise EmptyReply(f"empty reply (finish_reason={finish}): the reasoning used up the token budget")
    return text, usage
