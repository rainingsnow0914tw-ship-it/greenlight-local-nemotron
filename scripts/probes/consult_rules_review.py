# -*- coding: utf-8 -*-
"""請 Gemini 3.1 Pro 總體檢我們所有的生成邊界規則。

「你把我們現有的邊界跟規則包括生圖跟生影片的都問問他該怎麼做比較好？
  有沒有問題？我們來商量一下」

背景：同日 Pro 看片診斷出 screen_facing_camera 規則的空間矛盾
（要求人臉與螢幕正面同框 = 物理不可能），兩個引擎都因此折衷出爛畫面。
一條規則有這種問題，其他規則值得整批體檢。

輸出是**顧問意見**，不自動改任何規則 —— 採不採納逐條由產品決定。
成本：輸入約 15-20K tokens（Pro $2/1M）＋輸出 8K，約 $0.15。
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]  # scripts/probes/ 往上兩層才是 repo 根
sys.path.insert(0, str(_ROOT / "backend"))

from app.services import env_boot  # noqa: E402

env_boot.load()

constraints = (_ROOT / "config" / "constraints.yaml").read_text(encoding="utf-8")
casting = (_ROOT / "config" / "casting.yaml").read_text(encoding="utf-8")
base_prompts = (_ROOT / "config" / "prompts" / "base.yaml").read_text(encoding="utf-8")

QUESTION = f"""你是資深電影攝影指導、AI 生成模型專家（熟悉 Veo 3.1 與 Gemini Omni Flash 的行為特性）。

我們在做一個「劇本 → 分鏡 → 提示詞 → AI 影片」的產品。下面是我們全部的生成邊界規則，
分三份：

【一、影片物理約束 constraints.yaml】（依鏡頭內容條件觸發、每鏡最多套 6 條、依 expected_lift 排序）
```yaml
{constraints}
```

【二、選角生圖規則 casting.yaml】（角色參考圖的構圖與方向修飾）
```yaml
{casting}
```

【三、分鏡與各 agent 的系統指示 base.yaml】
```yaml
{base_prompts}
```

系統背景（重要）：
- 提示詞組裝順序：[角色外貌標籤][風格前綴][鏡別運鏡][畫面描述][物理約束][台詞（含音色）][音效][畫面人員硬邊界][全域負面]
- 「畫面人員硬邊界」由 code 依分鏡表結構化欄位生成（不是規則），因為通用的「不要加沒列的人」實測擋不住
- 今天剛發現並修掉一個結構性矛盾：舊 screen_facing_camera 要求「人臉與螢幕正面同框」——物理上不可能，兩個引擎都折衷出爛畫面。已改為過肩鏡頭機位語言
- 生成後有「看片檢核」（Gemini 清點人數與文字）與「音色鎖定」（聽片回寫音色）兩條回向迴路

請你逐項體檢，只講**有真實影響**的問題，按嚴重度排序：

A. 【內部矛盾】哪些規則自身要求了物理上/邏輯上互斥的東西？（像 screen 那種）
B. 【規則打架】哪些規則同時觸發時會互相抵消或留下縫隙？
C. 【太抽象】哪些規則的寫法對生成模型來說沒有可執行的畫面意義，該改成攝影機語言/具體幾何？請給改寫。
D. 【缺漏】以你對 Veo/Omni 常見失敗的了解，我們還缺哪些高價值的約束？（只列你有把握常發生的）
E. 【生圖側】選角規則有沒有問題？特別是：參考圖是 3:4 直式（實測會把影片方向帶偏）——該怎麼改？
F. 【每鏡 6 條上限】依你判斷，這個上限合理嗎？約束多了會不會互相稀釋？

每一條給：問題、為什麼、具體改法（提示詞用英文、說明用中文）。不用客套，直接講。"""


def main() -> int:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project="greenlight-2026",
        location="global",
        http_options=types.HttpOptions(timeout=900_000),
    )
    chunks = []
    last = None
    for chunk in client.models.generate_content_stream(
        model="gemini-3.1-pro-preview",
        contents=QUESTION,
        config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=16_384),
    ):
        last = chunk
        if chunk.text:
            chunks.append(chunk.text)

    text = "".join(chunks)
    out = _ROOT / "docs" / "rules_review_by_pro.md"
    out.write_text(
        "# 生成邊界規則總體檢 — Gemini 3.1 Pro 顧問意見\n\n"
        "> **這是顧問意見不是決定**，採納與否逐條由產品決定。\n\n"
        + text,
        encoding="utf-8",
    )
    print(text)
    meta = getattr(last, "usage_metadata", None)
    p = getattr(meta, "prompt_token_count", 0) or 0
    o = getattr(meta, "candidates_token_count", 0) or 0
    print(f"\n---tokens in/out: {p}/{o} · 約 ${p*2/1e6 + o*12/1e6:.3f}")
    print(f"存檔：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
