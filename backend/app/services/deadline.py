"""硬性逾時 —— 對「連線活著但對方不回」唯一管用的手段。

為什麼不是 `requests(timeout=…)` 就好：

    requests 的 timeout 是 connect ＋ **read timeout（兩次 socket 讀取之間
    的最大間隔）**，不是總時間。對方連線活著、間歇有動靜，計時器就一直被
    重置，永遠不觸發。而「連線活著但對方不回」正是掛住最常見的樣子。

為什麼要包**整個請求**而不只是包最可疑的那一段：

    只框住影片生成那一段（設 420 秒硬預算）時，實際上仍發生過
    整個請求掛到 **899.9 秒的 504**，而那 15 分鐘裡
    **連影片生成前面那行 log 都沒印出來** —— 它卡在被框住的範圍之外。

    結論：**框住的是想得到的地方，掛住的是想不到的地方。**
    一個同步 handler 裡有十幾個可能阻塞的呼叫（Firestore、ClickHouse、
    GCS 下載、抽幀、TTS、影片生成、看片檢核），逐個框永遠會漏。
    要框就框最外層 —— 那是唯一能保證「一定在平台砍掉之前收手」的位置。

這是「放棄等待」，不是「中止對方」。Python 沒辦法從外面砍掉一個卡在
   socket 上的執行緒。被放棄的執行緒設成 daemon、自生自滅，
   **它可能還在跑、甚至可能跑完並寫入狀態**。所以：
   - 逾時訊息不能承諾「沒有做任何事」「沒有扣到錢」
   - 使用者看到失敗但稍後重整發現成功，是預期內的結果，
     那仍然遠好過一個什麼都沒留下的 504
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DeadlineExceeded(RuntimeError):
    """超過硬性預算，已放棄等待。

    跟其他錯誤有一個關鍵差異：**不知道對方那邊發生了什麼事。**
       其他錯誤都是對方明確回了什麼（400、內容審查、配額），
       這個是對方什麼都沒回 —— 工作可能沒做，也可能做完了只是沒收到
       （而那通常是要計費的）。訊息必須誠實反映這個不確定。
    """


def run_with_deadline(fn: Callable[[], T], seconds: float, label: str) -> T:
    """跑 fn，超過 seconds 就放棄等待並拋 `DeadlineExceeded`。

    每次開一條新執行緒，不用固定大小的 pool：掛住的那條會一直佔著位子，
       用 pool 的話連續幾次逾時就會把池佔滿 ——
       症狀會從「這一次失敗」惡化成「整個服務不動了」。

    Args:
        fn: 不帶參數的呼叫（用 lambda 或 functools.partial 包）
        seconds: 硬性上限
        label: 記 log 與錯誤訊息用

    Raises:
        DeadlineExceeded: 超時。
        任何 fn 自己拋出的例外：原樣往上拋，不包裝 ——
            呼叫端要看到的是「原本到底發生什麼事」。
    """
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True, name=f"dl-{label}")
    thread.start()
    thread.join(seconds)

    if thread.is_alive():
        logger.error(
            "%s：超過硬性上限 %.0f 秒仍未完成，放棄等待（執行緒被放生，可能仍在跑）",
            label,
            seconds,
        )
        raise DeadlineExceeded(
            f"{label} 超過 {int(seconds // 60)} 分鐘還沒完成，已經停止等待。"
        )

    if "error" in box:
        raise box["error"]
    return box["result"]  # type: ignore[return-value]
