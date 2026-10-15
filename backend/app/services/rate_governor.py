"""自適應速率調節：讓系統自己找出配額邊界。

為什麼不寫死一個間隔：

    Vertex AI 生成模型的每分鐘配額查不到：
      · Cloud Monitoring 沒有影片秒數／圖片張數的 metric
      · Service Usage 的配額清單裡只有訓練用的儲存與 TPU，沒有生成模型
      · API 不公開 per-model 的動態配額

    所以任何寫死的數字都是猜的：猜大了每張圖白等，猜小了照樣撞 429。
    而且配額會因模型、檔位、專案、時段而不同，換個檔位就不對。

    讓它自己撞、自己學：被擋就拉長間隔、連續順利就縮短，
    逼近但不越過那條看不見的線。

## 策略：AIMD（加性增、乘性減）

    跟 TCP 壅塞控制同一個道理，而且理由一樣：
    超載的代價遠大於保守的代價，所以退讓要快、進逼要慢。

      撞到 429  → 間隔 ×2       立刻退到安全區
      連續成功  → 間隔 −10%     慢慢試探回去

    非對稱是刻意的。如果兩邊都用同樣的步幅，系統會在邊界上來回震盪，
    每震一次就浪費一個失敗的請求（而失敗的請求仍然算進配額）。

## 為什麼是 process 內記憶

    後端是常駐的，所以同一個 process 裡學到的邊界會一直累積：
    跑完一輪選角學到的間隔，批次生成時直接接著用。
    不做跨 process 持久化：配額本來就會隨時間變動，
    存下來的舊值不見得比重新學更準，反而會帶著過期的假設開跑。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _State:
    interval: float
    consecutive_ok: int = 0
    throttle_count: int = 0
    request_count: int = 0
    last_request: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class RateGovernor:
    """一個資源（通常是一顆模型）的速率調節器。

    用法：
        gov = get("gemini-3.1-flash-image")
        gov.before_request()          # 等到可以送
        try:
            result = call_api()
            gov.on_success()
        except Exception as exc:
            if is_quota_error(exc):
                gov.on_throttled()    # 學到「太快了」
            raise
    """

    def __init__(
        self,
        name: str,
        *,
        start_interval: float = 6.0,
        min_interval: float = 1.5,
        max_interval: float = 90.0,
        decay: float = 0.9,
        ok_before_decay: int = 3,
    ):
        self.name = name
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.decay = decay
        # 連續成功幾次才敢縮短。設 1 的話每次成功都往下探，
        # 會在邊界上震盪；設 3 讓它確認「真的順了」再進逼。
        self.ok_before_decay = ok_before_decay
        self._s = _State(interval=start_interval)

    # ---- 對外 ----

    def before_request(self) -> float:
        """必要時睡到可以送下一個請求。回傳實際等待秒數。"""
        with self._s.lock:
            interval = self._s.interval
            last = self._s.last_request
            now = time.monotonic()
            wait = 0.0
            if last is not None:
                elapsed = now - last
                if elapsed < interval:
                    wait = interval - elapsed
            # 先把時間戳往前推，這樣多執行緒同時進來也不會擠在一起送
            self._s.last_request = now + wait
            self._s.request_count += 1

        if wait > 0:
            time.sleep(wait)
        return wait

    def on_success(self) -> None:
        """這次過了。連續順利夠多次就試著縮短間隔。"""
        with self._s.lock:
            self._s.consecutive_ok += 1
            if self._s.consecutive_ok >= self.ok_before_decay:
                before = self._s.interval
                self._s.interval = max(self.min_interval, self._s.interval * self.decay)
                self._s.consecutive_ok = 0
                if self._s.interval < before:
                    logger.debug(
                        "%s 連續順利，間隔 %.1f → %.1f 秒",
                        self.name, before, self._s.interval,
                    )

    def on_throttled(self) -> None:
        """撞到 429。立刻退讓：這是唯一能觀測到的邊界訊號。"""
        with self._s.lock:
            before = self._s.interval
            self._s.interval = min(self.max_interval, self._s.interval * 2)
            self._s.consecutive_ok = 0
            self._s.throttle_count += 1
        logger.warning(
            "%s 撞到配額，間隔 %.1f → %.1f 秒（累計第 %d 次）",
            self.name, before, self._s.interval, self._s.throttle_count,
        )

    def snapshot(self) -> dict[str, float | int]:
        """目前學到的狀態。給 API 回傳用，使用者等待時看得到系統在做什麼。"""
        with self._s.lock:
            return {
                "name": self.name,
                "interval_seconds": round(self._s.interval, 1),
                "requests": self._s.request_count,
                "throttled": self._s.throttle_count,
            }


# ---------------------------------------------------------------------------
# 全域註冊表
# ---------------------------------------------------------------------------
#
# 依模型分開：Veo 跟 Gemini image 的配額是獨立的，
# 混在一起會讓「圖片被擋」拖慢「影片生成」，反之亦然。

_registry: dict[str, RateGovernor] = {}
_registry_lock = threading.Lock()


def get(name: str, **kwargs) -> RateGovernor:
    """取得（必要時建立）某個資源的調節器。同名共用同一個實例。"""
    with _registry_lock:
        gov = _registry.get(name)
        if gov is None:
            gov = RateGovernor(name, **kwargs)
            _registry[name] = gov
        return gov


def snapshot_all() -> list[dict]:
    with _registry_lock:
        return [g.snapshot() for g in _registry.values()]


def reset() -> None:
    """清空所有學到的狀態。測試用。"""
    with _registry_lock:
        _registry.clear()
