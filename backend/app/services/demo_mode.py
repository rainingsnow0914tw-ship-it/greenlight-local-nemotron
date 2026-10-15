"""Demo 模式 —— 給訪客試玩，但**一毛錢都不會花**。

目的：把真的能跑的產品變成可以公開試玩的展示版 —— 資料都還在，
但不會呼叫任何要花錢的 AI。

## 為什麼是環境變數，不是第二份 code

第一直覺會想「做個陽春版 repo」。但兩份 code 必然不同步：
同一個功能有兩份實作時，其中一份讀錯一個 key 就會靜默顯示錯誤數字，
另一份卻是對的 —— 因為它是另一份 code。
而 demo 版分岔的後果特別糟：訪客看到的會是幾週前的舊產品，
而你以為他們在看最新的。

所以：**同一份 code、同一個 image、同一個 git commit**，
差別只有 `GREENLIGHT_DEMO_MODE`。產品那邊一行都不用改，
以後迭代只要 deploy 兩次（正式一次、demo 一次）。

## 界線：只擋「會花錢的」，不擋「唯讀的」

    擋住   Gemini 呼叫（分鏡／選角／提示詞／看片／預審／翻譯）
           影片生成、TTS 合成
    照跑   ClickHouse 學習迴路查詢（$0，而且是核心賣點，原始列全部看得見）
           成本估算、成功率預測、專案狀態、已生成成果的播放與下載

**ClickHouse 一定要維持真查。** 它是這個產品的主張本身 ——
把它也 mock 掉，demo 就只剩一個殼，訪客看到的統計數字全部失去意義。
而且唯讀查詢不花錢，沒有任何理由擋它。

## 資料隔離

`project_store` 在 demo 模式下改用 `greenlight_demo_projects` collection，
所以訪客怎麼點都碰不到正式資料。GCS 共用同一批媒體（唯讀，不佔額外空間）——
demo 不會生成新檔案，所以沒有寫入風險。

## 誠實原則

被擋下時**明說**這一步在正式版會呼叫哪個模型、花多少錢，
而不是假裝成功或給一個灰掉的死按鈕。這跟產品其他地方的原則一致：
降級可以，靜默不行。那句提示本身就在展示成本透明度 —— 那正是產品的賣點。
"""

from __future__ import annotations

import os


class DemoBlocked(RuntimeError):
    """Demo 模式擋下了一個會花錢的操作。

    帶著「這一步在正式版是什麼、要花多少」，讓 UI 能說得出人話。
    """

    def __init__(self, action: str, model: str = "", est_usd: float = 0.0):
        self.action = action
        self.model = model
        self.est_usd = est_usd
        super().__init__(action)

    def as_detail(self) -> dict[str, object]:
        """給 API 回應用的結構。UI 靠這個組提示。

        `detail` 這個欄位是**故意重複** `message` 的：前端既有的錯誤處理
           只認 `detail`（`errorMessage()`），少了它使用者會看到一串
           `402 /api/...` 的原始文字。多給一個欄位，前端零改動就有人話；
           想做得更漂亮的話再認 `demo_mode` 把它渲染成說明卡而不是紅色錯誤。
        """
        if self.model:
            what = (
                f"在正式版，「{self.action}」會呼叫 {self.model}"
                + (f"、約花費 ${self.est_usd:.2f}" if self.est_usd else "")
            )
        else:
            # 沒有 model 的是「不花 AI 錢但會改壞展示」的操作：
            # 產生新檔案（接片、燒字幕）、刪除專案、複製專案。
            what = f"展示版本停用了「{self.action}」"
        message = f"這是展示版本，不會真的呼叫 AI。{what}。已經生成好的成果都可以直接播放與下載。"
        return {
            "demo_mode": True,
            "blocked_action": self.action,
            "model": self.model,
            "estimated_usd": round(self.est_usd, 3),
            "message": message,
            "detail": message,
        }


def is_demo() -> bool:
    """現在是不是 demo 模式。"""
    return os.getenv("GREENLIGHT_DEMO_MODE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def guard(action: str, model: str = "", est_usd: float = 0.0) -> None:
    """會花錢的操作進來這裡報到。Demo 模式下直接擋掉。

    放在**真正送出請求之前**，不要放在算完成本之後 ——
       成本估算本身是唯讀的、要讓它照跑，那是產品要展示的東西。

    Args:
        action: 人看得懂的動作名稱（會出現在使用者眼前）
        model: 正式版會呼叫哪個模型
        est_usd: 估計花費，0 表示不明說
    """
    if is_demo():
        raise DemoBlocked(action, model, est_usd)


def collection_suffix() -> str:
    """Demo 模式的 Firestore collection 後綴。

    正式與 demo 用不同 collection，訪客怎麼點都碰不到真資料。
    """
    return "_demo" if is_demo() else ""
