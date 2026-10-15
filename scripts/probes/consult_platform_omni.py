# -*- coding: utf-8 -*-
"""問「平台裡的 AI」Omni 的完整能力與注意事項。

「你搜外面的資訊，人家用的經驗應該是很少的，最新的資訊你一定要問平台裡面的 AI。
  因為外面還是停留在用戶自己寫提示詞的年代。」

做法：Vertex 上的 Gemini + Google Search grounding —— 模型即時檢索
（會抓到官方文件與最新公告）再回答，等於平台自己的 AI 帶著最新資料說話。
成本：一次約 $0.05。回答存 docs/omni_platform_intel.md。
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

QUESTION = """我們正在用 Vertex AI / Agent Platform 上的 `gemini-omni-flash-preview`
（Interactions API，`POST .../locations/global/interactions`）做一個
「劇本 → 分鏡 → 提示詞 → 影片 → 剪接成片」的產品。已實測知道：
固定約 10 秒、16:9/9:16 靠提示詞文字控制、參考圖會影響輸出方向、
generation_config 裡 task/duration_seconds/aspect_ratio 都是 Unknown parameter、
Vertex 這條路不支援 previous_interaction_id（編輯要把影片本體餵回去）。

請搜尋最新的官方文件與公告，回答：
1. Interactions API 的 **generation_config 到底支援哪些欄位**？有沒有控制
   秒數、解析度、比例、負面提示的正式方法？
2. input 陣列還支援哪些型別與組合？（多張參考圖上限？影片參考的長度限制？
   音訊什麼時候開放？）
3. 對話式編輯的最佳實踐：怎麼下編輯指令保留度最高？有沒有官方建議的措辭模式？
4. 已知的限制、配額、地區限制、計費細節（輸入影片按什麼算錢？）
5. 提示詞寫法上，Omni 跟 Veo 的差異 —— 官方有沒有 prompting guide？
   （例如：它是對話式模型，是不是該用自然語言敘事而不是逗號堆疊標籤？）
6. Video Extension / Upscaling「coming soon」有沒有時間表或 preview 申請管道？
7. preview 階段的穩定性承諾：模型行為會不會變？有沒有版本 pin 的方法？

請引用來源。沒把握的請明說「不確定」，不要編。用中文回答。"""


def main() -> int:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project="greenlight-2026",
        location="global",
        http_options=types.HttpOptions(timeout=900_000),
    )

    resp = client.models.generate_content(
        model="gemini-3.1-pro-preview",
        contents=QUESTION,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2,
            max_output_tokens=8_192,
        ),
    )
    text = resp.text or ""
    out = _ROOT / "docs" / "omni_platform_intel.md"
    out.write_text(
        "# Gemini Omni Flash — 平台 AI（Gemini + Google Search grounding）情報\n\n"
        "> 最新的資訊要問平台裡面的 AI。\n\n" + text,
        encoding="utf-8",
    )
    print(text)
    meta = getattr(resp, "usage_metadata", None)
    print(
        "\n---tokens:",
        getattr(meta, "prompt_token_count", 0),
        "/",
        getattr(meta, "candidates_token_count", 0),
    )
    print("存檔：", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
