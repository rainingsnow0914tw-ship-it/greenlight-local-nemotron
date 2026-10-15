# -*- coding: utf-8 -*-
"""問平台 AI：紀錄片式旁白要怎麼跟角色台詞區分。

實際症狀：
    紀錄片式劇本、第三人稱旁白講述，結果模型搞不清楚誰在講話，
    旁白台詞變成主角開口在講。

本機診斷已經找到一半根因：`Shot.speaker` 只存名字，分鏡 agent 的說明還
明講「旁白、畫外音、電話那頭」都填這一欄 —— 三種不同的東西擠在同一格，
下游拿它去查選角表就把旁白當成角色，套上外貌標籤與音色。

還缺的另一半是**提示詞怎麼寫**才能讓模型不要讓畫面裡的人開口。那要問官方。

為什麼用 REST + `gcloud auth print-access-token` 而不是 genai SDK：
   SDK 走 ADC，而本機不設定 ADC（測試環境直接用雲端）。gcloud 的 user 憑證還在，
   REST 這條路就不需要 ADC。
   REST + user 憑證**必須手動帶 x-goog-user-project**，否則錯誤訊息
      極度誤導（有些 endpoint 回 404「Publisher model was not found」，
      會讓人以為 model ID 打錯，實際上 ID 完全正確）。
"""
from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
PROJECT = "greenlight-2026"
MODEL = "gemini-3.1-pro-preview"
ENDPOINT = (
    f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}"
    f"/locations/global/publishers/google/models/{MODEL}:generateContent"
)

QUESTION = """我在用 Google 的影片生成模型做一個「劇本轉影片」的工具，
主力是 gemini-omni-flash-preview（Interactions API），備援是 Veo 3.1。

現在遇到的具體問題：使用者的劇本是**紀錄片形式**，大量使用第三人稱旁白
（narrator / voice-over）講述事件，而不是畫面中的角色在對話。
但生成出來的影片會讓**畫面中的主角開口說出那段旁白**（對嘴、嘴型在動），
而那段話根本不是他說的。

請針對以下幾點回答，並盡量引用官方文件或 prompting guide：

1. 對 Veo 3.1 與 Gemini Omni Flash，要產生「畫面外的第三人稱旁白配音，
   畫面中的人不說話」，提示詞應該怎麼寫？有沒有官方建議的措辭或語法
   （例如某種標記、某個關鍵字、把台詞放在哪個位置）？

2. 有沒有實證有效的措辭可以**明確禁止畫面中的人物做出說話的嘴型**？
   （例如 "no lip movement" / "no one speaks on camera" 這類句子有效嗎？
   或者有更可靠的講法？）

3. 官方對 narration / voice-over 與 character dialogue 的區分，
   在 prompting 上有沒有專門的指引？兩者的寫法差在哪裡？

4. 紀錄片風格（documentary style）在這兩個模型上有沒有已知的最佳實踐？
   例如鏡頭語言、B-roll、旁白與畫面的搭配方式。

5. 旁白的**音色如何跨鏡頭保持一致**？（每一鏡是獨立生成的，
   我們已知角色音色要在每一鏡重複描述，旁白是否適用同一套做法？）

6. 如果模型本身無法可靠區分，有沒有替代架構？
   （例如：影片生成時完全不要音軌、旁白改用 TTS 另外合成後再混音 ——
   這樣做在 Google 的模型生態裡可行嗎？用哪個 TTS？）

請用中文回答。沒把握的地方請明說「不確定」，不要編造官方沒說過的東西。
"""


def _token() -> str:
    """拿 gcloud 的 user access token。

    用 gcloud.cmd 不是 gcloud —— Windows 上 subprocess 找不到後者
       （踩過，錯誤是 FileNotFoundError [WinError 2]）。
    """
    for exe in ("gcloud.cmd", "gcloud"):
        try:
            out = subprocess.run(
                [exe, "auth", "print-access-token", *(["--account", os.environ["GCLOUD_ACCOUNT"]] if os.environ.get("GCLOUD_ACCOUNT") else [])],
                capture_output=True, text=True, check=True, shell=False,
            )
            return out.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("拿不到 access token —— 先跑 gcloud auth login")


def main() -> int:
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json; charset=utf-8",
        # 見檔頭：user 憑證打 REST 一定要帶這個
        "x-goog-user-project": PROJECT,
    }
    payload = {
        "contents": [{"role": "user", "parts": [{"text": QUESTION}]}],
        # Google Search grounding —— 外界資訊落後半年，這一步是為了拿官方最新說法
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }

    r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=900)
    print("HTTP", r.status_code)
    if r.status_code != 200:
        print(json.dumps(r.json(), ensure_ascii=False)[:600])
        return 1

    body = r.json()
    parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)

    usage = body.get("usageMetadata", {})
    cost = (
        usage.get("promptTokenCount", 0) * 1.25
        + usage.get("candidatesTokenCount", 0) * 10.0
    ) / 1_000_000
    print(f"用量：in {usage.get('promptTokenCount')} / out {usage.get('candidatesTokenCount')} ≈ ${cost:.4f}")

    out = _ROOT / "docs" / "narration_intel.md"
    out.write_text(
        "# 紀錄片旁白 vs 角色台詞 —— 平台 AI（Gemini + Google Search grounding）情報\n\n"
        "> 提問：劇本是紀錄片形式、第三人稱旁白，"
        "但生成出來變成畫面中的主角開口說那段旁白。\n\n"
        + text,
        encoding="utf-8",
    )
    print(f"\n已存：{out}")
    print("=" * 72)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
