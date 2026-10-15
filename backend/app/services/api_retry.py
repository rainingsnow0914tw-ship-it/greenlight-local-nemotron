"""Google API 呼叫的重試與節流。

為什麼需要：
    一輪選角連續打多次圖片生成時會撞到 429 RESOURCE_EXHAUSTED。
    不是額度用完，是每分鐘請求數的配額 —— 打太密集就會被擋。

    這件事在選角階段只是少一張候選圖；到了批次生成階段
    （多個鏡頭連續呼叫 Veo）就是整批垮掉，而且每一次失敗的重試
    都可能是真金白銀。所以重試機制要在便宜的階段就做好。

設計原則：
    1. **只重試值得重試的錯誤**。429（太密集）、503（暫時不可用）、
       連線中斷 —— 這些等一下會好。400（參數錯）、403（沒權限）、
       404（模型不存在）重試一百次還是一樣，只是浪費時間。
    2. **指數退避**。429 的意思是「你太快了」，馬上再打一次只會再被擋。
    3. **請求之間留間隔**。與其被擋了再退避，不如一開始就別打那麼快。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 這些字串出現在例外訊息裡就代表「等一下會好」。
# 用字串比對而不是例外型別：google-genai 把 HTTP 錯誤包成自己的例外類別，
# 型別在不同版本間會變，訊息裡的狀態碼與狀態名反而穩定。
_RETRYABLE_MARKERS = (
    "429",
    "RESOURCE_EXHAUSTED",
    "503",
    "UNAVAILABLE",
    "500",
    "INTERNAL",
    "DEADLINE_EXCEEDED",
    "Server disconnected",
    "Connection reset",
)

# 配額類錯誤要用完全不同的等待策略，見 _quota_delays。
_QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED")

# 429 專用的等待秒數（依實測訂）：
#    配額是**滾動時間視窗**（大約一分鐘），不是「等一下就好」的暫時性壅塞。
#    用 6 → 12 → 24 秒的指數退避，四次重試全部落在同一個視窗裡，
#    等於白等 42 秒還是失敗。
#    直接跳到 25 秒起、第二次就跨過整個視窗，成功率高得多，
#    總等待時間反而更短。
_QUOTA_DELAYS = (25.0, 50.0, 70.0)

# 這些出現就**不要重試** —— 是設定或請求本身的問題，重試只是延後看到錯誤。
_FATAL_MARKERS = (
    "400",
    "INVALID_ARGUMENT",
    "403",
    "PERMISSION_DENIED",
    "404",
    "NOT_FOUND",
    "Reauthentication is needed",
)


def is_retryable(exc: BaseException) -> bool:
    """這個錯誤值不值得再試一次。

    先看致命標記再看可重試標記 —— 因為某些錯誤訊息會同時提到兩者
    （例如帶著 404 的訊息裡剛好有 "500" 這個數字），
    致命判斷優先比較安全：寧可少重試一次，也不要對著設定錯誤空轉。
    """
    text = str(exc)
    if any(marker in text for marker in _FATAL_MARKERS):
        return False
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def is_quota(exc: BaseException) -> bool:
    """這是不是配額（429）錯誤。

    分開判斷是因為它的恢復時間跟其他暫時性錯誤完全不同 ——
    503 是「伺服器現在忙」，幾秒就好；429 是「你這一分鐘打太多了」，
    要等整個視窗過去。
    """
    return any(marker in str(exc) for marker in _QUOTA_MARKERS)


def call(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 6.0,
    max_delay: float = 60.0,
    label: str = "",
    deadline: float | None = None,
) -> T:
    """執行 fn，遇到暫時性錯誤就退避重試。

    Args:
        fn: 不帶參數的呼叫（用 lambda 或 functools.partial 包）
        attempts: 總共試幾次（含第一次）
        base_delay: 第一次重試前等幾秒，之後每次加倍
        max_delay: 單次等待上限，避免最後一次要等好幾分鐘
        label: 記 log 用的名稱
        deadline: `time.monotonic()` 座標的截止點。剩餘時間不夠再試一輪
            就不試了，直接把上一次的例外拋出去。

            為什麼需要：重試次數 × 單次上限才是真正的
            總時間，而呼叫端（Cloud Run 請求）有自己的死線。少了這個檢查，
            「單次逾時設短一點」根本擋不住總和爆掉 —— 而爆掉的後果不是
            慢，是**整個請求被平台砍掉、例外處理沒機會跑、失敗紀錄存不下**。

    Raises:
        最後一次的例外原樣拋出 —— 不包一層自訂例外，
        因為呼叫端要看到的是「Google 到底回了什麼」。
    """
    last: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt >= attempts or not is_retryable(exc):
                raise

            if is_quota(exc):
                # 配額類走固定的長等待表，跨過整個配額視窗
                index = min(attempt - 1, len(_QUOTA_DELAYS) - 1)
                delay = _QUOTA_DELAYS[index]
                kind = "配額"
            else:
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                kind = "暫時性"

            # 等下去會超過死線的話，現在就放棄 ——
            #    「等到一半被平台砍掉」比「誠實地早點失敗」糟得多：
            #    前者連失敗紀錄都留不下來。
            if deadline is not None and time.monotonic() + delay >= deadline:
                logger.warning(
                    "%s 第 %d 次失敗（%s），但剩餘預算不足 %.0f 秒，不再重試",
                    label or getattr(fn, "__name__", "call"),
                    attempt,
                    kind,
                    delay,
                )
                raise

            logger.warning(
                "%s 第 %d 次失敗（%s：%s），%.0f 秒後重試",
                label or getattr(fn, "__name__", "call"),
                attempt,
                kind,
                str(exc)[:110],
                delay,
            )
            time.sleep(delay)

    # 理論上到不了這裡（迴圈裡不是 return 就是 raise），
    # 但留著讓型別檢查與極端情況都有明確行為。
    raise last if last else RuntimeError("重試迴圈異常結束")


class Pacer:
    """請求節流器 —— 確保連續呼叫之間至少隔一段時間。

    比「被擋了再退避」好的地方：退避是**事後補救**，已經浪費一次往返；
    節流是**事前避免**，而且對配額計數器友善。

    一輪三張圖之間各隔幾秒，比第三張撞 429 之後等 6 秒再重試划算 ——
    後者的總時間更長，而且中間那次失敗仍然算進配額。
    """

    def __init__(self, min_interval: float = 4.0):
        self.min_interval = min_interval
        self._last: float | None = None

    def wait(self) -> None:
        """必要時睡到可以發下一個請求。第一次呼叫不等。"""
        now = time.monotonic()
        if self._last is not None:
            elapsed = now - self._last
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


def wrap(pacer: Pacer | None, fn: Callable[[], T], **kwargs: Any) -> T:
    """節流 + 重試一次搞定。

    重試的那一次也會先經過節流 —— 否則退避完馬上又打，
    等於把剛才等的時間浪費掉。
    """

    def _guarded() -> T:
        if pacer is not None:
            pacer.wait()
        return fn()

    return call(_guarded, **kwargs)


def governed(resource: str, fn: Callable[[], T], **kwargs: Any) -> T:
    """自適應節流 + 重試。**新的程式碼用這個，不要用 wrap()。**

    跟 `wrap()` 的差別：`Pacer` 是固定間隔（要先猜一個數字），
    `RateGovernor` 會從 429 學出間隔 —— 而**真正的配額查不到**
    （見 rate_governor 的說明），所以任何固定值都是猜的。

    429 要同時餵給兩個機制：
       governor 學「以後要慢一點」，retry 決定「這一次等多久再試」。
       少了前者，每次批次生成都會重新撞一次牆。

    kwargs 直接轉給 `call()` —— 包含 `deadline`，長生成一定要帶，
    否則重試次數 × 單次上限會撐爆呼叫端的死線。
    """
    from . import rate_governor

    gov = rate_governor.get(resource)

    def _guarded() -> T:
        gov.before_request()
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            if is_quota(exc):
                gov.on_throttled()
            raise
        gov.on_success()
        return result

    return call(_guarded, **kwargs)
