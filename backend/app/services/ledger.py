"""ClickHouse 生成紀錄層 —— 讓「成功率預估」從猜測變成統計。

設計理由：
    系統的核心承諾是「告訴你這樣拍的成功率是多少」。冷啟動時那個數字來自
    config/priors.yaml 的先驗值 —— 是估的，而且 UI 上誠實標示它是估的。

    每跑一次真實生成就往 ClickHouse 寫一筆，之後同類鏡頭改用實測統計。
    跑得越多越準。這就是 ClickHouse 在本專案的角色：
    不是「拿來裝資料的地方」，是「讓這個功能能夠變準的必要條件」。

優雅降級是硬性要求：
    沒有 ClickHouse 連線時，所有查詢回退到「查不到」，系統照常運作、
    只是全程用先驗值。理由是開發順序前提 ——
    規劃層必須能在本機零成本跑通，不能因為沒起資料庫就整個動不了。

    這也是正式環境的容錯：ClickHouse 掛掉時，使用者看到的是
    「先驗估計」而不是錯誤畫面。

**存取管道走官方 mcp-clickhouse MCP server**：
    ClickHouse 必須在 runtime 透過官方 MCP server（mcp-clickhouse）被查詢，
    這是硬性要求；`clickhouse_connect` 直連功能對、管道錯。

    傳輸細節全部關在 `services/clickhouse_mcp.py`，那一層提供介面相容的
    client，所以這個檔的查詢與統計邏輯不必因管道而改。
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NamedTuple

from ..schemas.plan import QualityTier
from ..schemas.shot import Shot

logger = logging.getLogger(__name__)

# mcp-clickhouse 是選用依賴。
# 沒裝也要能跑整條規劃鏈路（全程先驗值）—— 那是開發順序前提。
try:  # pragma: no cover - 匯入路徑依環境而異
    from . import clickhouse_mcp

    _CLICKHOUSE_AVAILABLE = True
except ImportError:  # pragma: no cover
    clickhouse_mcp = None  # type: ignore
    _CLICKHOUSE_AVAILABLE = False


def _db() -> str:
    """資料庫名稱。

    所有查詢都經過這裡取前綴，不寫死 `greenlight.`：
       如果查詢寫死前綴、連線那邊卻讀 `CLICKHOUSE_DB`，那個環境變數就是假的 ——
       設了會連上另一個資料庫，然後照樣去查 `greenlight.*`。
       不會報錯（同一個 server 上兩個資料庫都看得到），
       只會讓「明明指到測試庫」這件事默默不成立。

       統一走這裡才有辦法把測試資料寫進獨立的資料庫再整個 drop ——
       否則測試列會永久留在 constraint_stats / calibration_stats 裡，
       那兩張是 SummingMergeTree 聚合表，沒有 plan_id 可以事後篩掉。
    """
    return "main" if _local_mode() else os.getenv("CLICKHOUSE_DB", "greenlight")


def _local_mode() -> bool:
    """沒有指定雲端 ClickHouse（CLICKHOUSE_HOST）就是本機模式：學習紀錄存使用者電腦裡的 SQLite。"""
    return not os.getenv("CLICKHOUSE_HOST")


def database_label() -> str:
    """畫面上「紀錄存在哪」的字樣 —— 唯一的家（原本三個 API 各自寫死 "ClickHouse"）。"""
    return "SQLite (on this computer)" if _local_mode() else "ClickHouse"


def endpoint_label() -> str:
    """畫面上「從哪裡查的」：本機模式是使用者自己的電腦；雲端模式是 ClickHouse 主機（帳密去掉）。"""
    return "this computer" if _local_mode() else (os.getenv("CLICKHOUSE_HOST") or "").split("@")[-1]


def _table(name: str = "generation_ledger") -> str:
    return f"{_db()}.{name}"

# 一層要有幾筆才算數。低於這個數字就往下退到更寬鬆的匹配層級。
#
# 為什麼是 5：低於 5 筆的通過率統計波動極大（3 筆裡過 1 筆 = 33%，
# 過 2 筆 = 67%，差一筆就跳 34 個百分點），那種數字不該拿來蓋掉先驗值。
_MIN_SAMPLES_PER_LEVEL = 5


class _Bucket(NamedTuple):
    """一組鏡頭特徵。當 dict 的鍵用，所以要 hashable。

    欄位名刻意跟 _MATCH_LEVELS 裡的欄位名一致 —— 放寬比對是用
    `bucket[欄位名]` 取值的，名字對不上會靜默比對失敗（永遠回先驗值）。
    """

    tier: str
    shot_type: str
    camera_move: str
    character_count: int


@dataclass
class SimilarityResult:
    """相似鏡頭的查詢結果。"""

    observed_rate: float | None = None
    sample_size: int = 0
    match_level: str = "none"
    match_description: str = "尚無實測資料"


@dataclass
class GenerationRecord:
    """一次生成的完整紀錄。寫進 ClickHouse 的形狀。"""

    plan_id: str
    shot_id: str
    attempt: int
    style_id: str
    tier: str
    shot_type: str
    camera_move: str
    character_count: int
    base_duration_seconds: int
    extensions: int
    has_dialogue: bool
    has_reference_image: bool
    has_first_frame: bool
    content_flags: list[str] = field(default_factory=list)
    applied_constraints: list[str] = field(default_factory=list)
    predicted_success_rate: float = 0.0
    outcome: str = "accepted"
    rejection_reason: str = ""
    cost_usd: float = 0.0
    latency_seconds: int = 0
    model_id: str = ""
    prompt_excerpt: str = ""
    output_uri: str = ""


# ---------------------------------------------------------------------------
# 連線
# ---------------------------------------------------------------------------

_client: Any = None
_last_attempt_at: float = 0.0

# 連線失敗後多久才允許再試一次。
#
# 為什麼需要這個冷卻而不是「只試一次」：
#     失敗就永久降級的理由是「不要每個請求都卡在連線逾時上」——
#     那個理由成立，但代價太大：**一次暫時性失敗，這個 instance
#     從此再也拿不到統計資料，而且完全靜默。**
#
#     實際觀察到的情形：ClickHouse Cloud 閒置休眠，冷啟動時第一個 ping
#     超時；四秒後資料庫其實就醒了，但那個 instance 已經永久放棄。
#     健康檢查顯示 `clickhouse: degraded`、成功率全部退回先驗值，
#     而使用者只會覺得「這個系統好像沒在學東西」。
#
#     冷卻期同時擋掉「每個請求都重連」的效能問題與「永遠不再重試」的
#     可用性問題。
_RETRY_COOLDOWN_SECONDS = float(os.getenv("CLICKHOUSE_RETRY_COOLDOWN", "180"))


def _get_client() -> Any:
    """取得 ClickHouse 連線，失敗回 None。

    連上之後就一直用同一個。連不上的話**冷卻一段時間再重試** ——
    不是每次查詢都重連（那會讓每個請求都卡在逾時上），
    也不是永久放棄（那會讓一次暫時性失敗變成永久失憶）。

    要立刻重新嘗試連線的話呼叫 reset_connection()。
    """
    global _client, _last_attempt_at

    if _client is not None:
        return _client

    now = time.monotonic()
    if _last_attempt_at and (now - _last_attempt_at) < _RETRY_COOLDOWN_SECONDS:
        # 冷卻中。安靜回 None —— 這條路徑每個請求都會走到，
        # 每次都 log 會把真正有用的訊息淹掉。
        return None

    _last_attempt_at = now

    # Greenlight Local：沒有特別設定雲端資料庫，就用使用者電腦裡的 SQLite（local_db.py）。
    #    原版預設連 localhost 的 ClickHouse —— 一般使用者的電腦上不會有，結果是學習紀錄一筆都寫不進去、
    #    而且是靜默的（跑接線檢查：「ledger 多一筆 accepted —— 0 → 0 筆」）。
    if _local_mode():
        from .local_db import LocalClient

        try:
            _client = LocalClient()
            logger.info("學習紀錄：本機 SQLite（%s）", _client.path)
        except Exception as exc:  # noqa: BLE001 - 磁碟滿、沒權限…：紀錄寫不進去也不能中斷生成
            logger.warning("本機學習紀錄打不開（%s: %s），先用先驗值，%.0f 秒後重試",
                           type(exc).__name__, str(exc)[:160], _RETRY_COOLDOWN_SECONDS)
            _client = None
        return _client

    if not _CLICKHOUSE_AVAILABLE:
        logger.info("mcp-clickhouse 未安裝，ledger 走降級模式（全程使用先驗值）")
        return None

    host = os.getenv("CLICKHOUSE_HOST", "localhost")
    database = os.getenv("CLICKHOUSE_DB", "greenlight")

    try:
        # 走官方 mcp-clickhouse MCP server（存取管道的硬性要求）。
        #    連線細節、TLS、async 橋接全部關在 clickhouse_mcp 那一層；
        #    回來的 client 介面跟舊的直連驅動一樣，所以底下的查詢邏輯沒動。
        _client = clickhouse_mcp.connect()
        logger.info("ClickHouse 已連線（透過 mcp-clickhouse）：%s/%s", host, database)
    except Exception as exc:  # noqa: BLE001 - 任何連線問題都走降級
        # 用 warning 不是 info。降級是「預期得了但不該安靜發生」的事 ——
        #    info 會混在正常流水裡沒人看見，而這條訊息代表
        #    「從現在起成功率全是猜的」。
        logger.warning(
            "ClickHouse 連不上（%s: %s），ledger 降級使用先驗值，%.0f 秒後重試",
            type(exc).__name__,
            str(exc).split("\n")[0][:160],
            _RETRY_COOLDOWN_SECONDS,
        )
        _client = None

    return _client


def reset_connection() -> None:
    """清掉連線狀態，下次呼叫立刻重新嘗試（不等冷卻）。測試與重連用。"""
    global _client, _last_attempt_at
    _client = None
    _last_attempt_at = 0.0


def is_available() -> bool:
    """ClickHouse 現在能不能用。UI 用這個決定要不要顯示「實測資料」相關區塊。"""
    return _get_client() is not None


# ---------------------------------------------------------------------------
# 寫入
# ---------------------------------------------------------------------------

def record_generation(record: GenerationRecord) -> bool:
    """寫一筆生成紀錄。回傳是否成功寫入。

    寫入失敗絕對不能中斷生成流程。使用者已經花錢生出影片了，
       不能因為統計紀錄寫不進去就把結果丟掉。
       所以這裡吞掉所有例外，只記 log。
    """
    client = _get_client()
    if client is None:
        return False

    try:
        client.insert(
            _table(),
            [
                [
                    uuid.uuid4(),
                    datetime.now(timezone.utc),
                    record.plan_id,
                    record.shot_id,
                    record.attempt,
                    record.style_id,
                    record.tier,
                    record.shot_type,
                    record.camera_move,
                    record.character_count,
                    record.base_duration_seconds,
                    record.extensions,
                    record.has_dialogue,
                    record.has_reference_image,
                    record.has_first_frame,
                    record.content_flags,
                    record.applied_constraints,
                    record.predicted_success_rate,
                    record.outcome,
                    record.rejection_reason,
                    record.cost_usd,
                    record.latency_seconds,
                    record.model_id,
                    record.prompt_excerpt[:500],
                    record.output_uri,
                ]
            ],
            column_names=[
                "event_id",
                "created_at",
                "plan_id",
                "shot_id",
                "attempt",
                "style_id",
                "tier",
                "shot_type",
                "camera_move",
                "character_count",
                "base_duration_seconds",
                "extensions",
                "has_dialogue",
                "has_reference_image",
                "has_first_frame",
                "content_flags",
                "applied_constraints",
                "predicted_success_rate",
                "outcome",
                "rejection_reason",
                "cost_usd",
                "latency_seconds",
                "model_id",
                "prompt_excerpt",
                "output_uri",
            ],
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("寫入 generation_ledger 失敗（不影響生成流程）：%s", exc)
        return False


def record_adjudicated(
    *,
    plan_id: str,
    shot: Shot,
    facts: Any,
    style_id: str,
    tier: str,
    outcome: str,
    rejection_reason: str = "",
    output_uri: str = "",
) -> bool:
    """把一次**已經有結論**的生成寫進 ledger。

    `facts` 是 schemas.segment.PendingLedgerFacts —— 生成當下抓下來、
    扣在 clip 上等人裁決的那份事實。這裡把它跟 Shot 的特徵合起來成一筆紀錄。

    鏡頭特徵一律**從 Shot 現場推導**，不從 facts 複製一份。
       存兩份就會有兩份不同步的風險，而不同步的那一天不會有人發現 ——
       ledger 只會安靜地開始統計錯的東西。

    outcome 三種值的意義見 schema.sql：
        accepted  人採用了
        rejected  生出來了但不能用（人按重拍）
        error     API 失敗，**不算進成功率統計**（那是基礎設施問題不是鏡頭難度）
    """
    return record_generation(
        GenerationRecord(
            plan_id=plan_id,
            shot_id=shot.shot_id,
            attempt=facts.attempt,
            style_id=style_id,
            tier=tier,
            shot_type=shot.shot_type.value,
            camera_move=shot.camera_move.value,
            character_count=len(shot.characters_in_frame),
            base_duration_seconds=shot.base_duration_seconds,
            extensions=shot.extensions,
            has_dialogue=bool(shot.dialogue),
            has_reference_image=bool(shot.reference_image_uri),
            has_first_frame=facts.has_first_frame,
            content_flags=[f.value for f in shot.content_flags],
            applied_constraints=list(facts.applied_constraints),
            predicted_success_rate=facts.predicted_success_rate,
            outcome=outcome,
            rejection_reason=rejection_reason,
            cost_usd=facts.cost_usd,
            latency_seconds=facts.latency_seconds,
            model_id=facts.model_id,
            prompt_excerpt=facts.prompt_excerpt,
            output_uri=output_uri,
        )
    )


# ---------------------------------------------------------------------------
# 相似度查詢 —— 逐層放寬
# ---------------------------------------------------------------------------

# 匹配層級由嚴到寬。每一層是 (層級代號, 人話描述, 額外的 WHERE 條件欄位)。
#
# 設計理由：
#   精確匹配（同風格、同檔位、同鏡別、同運鏡、同人數）的統計最可信，
#   但在系統剛開始跑的時候幾乎一定湊不到 5 筆。
#
#   所以逐層放寬，並且**把用了哪一層告訴使用者** ——
#   「基於 8 筆完全同類鏡頭」跟「基於 40 筆同風格鏡頭」的可信度差很多，
#   混為一談就是誤導。
_MATCH_LEVELS: list[tuple[str, str, list[str]]] = [
    (
        "exact",
        "完全同類鏡頭（同風格、畫質、鏡別、運鏡、人數）",
        ["style_id", "tier", "shot_type", "camera_move", "character_count"],
    ),
    (
        "same_framing",
        "同風格、畫質、鏡別、運鏡",
        ["style_id", "tier", "shot_type", "camera_move"],
    ),
    ("same_shot_type", "同風格、畫質、鏡別", ["style_id", "tier", "shot_type"]),
    ("same_tier", "同風格、同畫質", ["style_id", "tier"]),
    ("same_style", "同風格", ["style_id"]),
]


def query_similar_shots(shot: Shot, tier: QualityTier, style_id: str) -> SimilarityResult:
    """查歷史上特徵相近的鏡頭，算出實測通過率。

    只統計 attempt = 1 且 outcome != 'error' 的紀錄：
      - attempt > 1 是重試，重試時人會微調提示詞，算進去會高估一次過關率
      - outcome = 'error' 是 API 故障（配額、審核、逾時），那是基礎設施問題
        不是鏡頭難度，混進來會讓「這種鏡頭很難拍」的結論被污染

    回傳的 SimilarityResult 帶 match_level 與人話描述，
    UI 必須顯示出來 —— 「基於 8 筆完全同類鏡頭」跟「基於 40 筆同風格鏡頭」
    的可信度差很多。
    """
    client = _get_client()
    if client is None:
        return SimilarityResult()

    values: dict[str, Any] = {
        "style_id": style_id,
        "tier": tier.value,
        "shot_type": shot.shot_type.value,
        "camera_move": shot.camera_move.value,
        "character_count": len(shot.characters_in_frame),
    }

    for level_id, description, columns in _MATCH_LEVELS:
        conditions = " AND ".join(f"{col} = %({col})s" for col in columns)
        params = {col: values[col] for col in columns}

        sql = f"""
            SELECT count() AS n, countIf(outcome = 'accepted') AS ok
            FROM {_table()}
            WHERE attempt = 1 AND outcome != 'error' AND {conditions}
        """

        try:
            rows = client.query(sql, parameters=params).result_rows
        except Exception as exc:  # noqa: BLE001
            logger.error("查詢 generation_ledger 失敗，退回先驗值：%s", exc)
            return SimilarityResult()

        if not rows:
            continue

        total, accepted = int(rows[0][0]), int(rows[0][1])
        if total >= _MIN_SAMPLES_PER_LEVEL:
            return SimilarityResult(
                observed_rate=accepted / total,
                sample_size=total,
                match_level=level_id,
                match_description=f"{total} 筆{description}",
            )

    return SimilarityResult()


def make_history_lookup(style_id: str):
    """做一個給 cost_engine 用的歷史查詢回呼。

    cost_engine 刻意不依賴 ClickHouse（見該檔案的說明），
    它只認一個 (shot, tier) -> (rate, sample_size) 的函式。
    這裡把 ledger 包成那個形狀。

    沒有 ClickHouse 時，這個回呼一律回 (None, 0)，
    整條估價鏈路照常運作、全程用先驗值。

    **一次查完，不要一組一組來回**：

        若每個 (鏡頭, 檔位) 組合各發一次查詢，內部還逐層放寬最多五層，
        本機 docker 每次不到 10 毫秒，感覺不出來；
        一接上雲端，每次跨網 **1–2 秒**，開一個段落頁要等 30 秒以上。

        但這件事本來就不該分那麼多次問。ClickHouse 是 OLAP 引擎，
        「把所有分組的通過率一次算出來」正是它的強項 ——
        一組一組來回等於買了跑車在巷子裡倒車。

        所以：**一次 GROUP BY 把整個風格的統計拉回記憶體**，
        逐層放寬全部在 Python 裡比對。網路往返從 30+ 次變成 1 次。
        聚合結果很小（一個組合一列，通常幾十列），拉回來幾乎不花錢。

    快取只活在這個回呼的生命週期內 —— 也就是一次 build_plan。
    不做跨請求快取是刻意的：ledger 隨時在長，
    使用者按下重拍之後再看到的成功率就該是更新過的。
    """
    started = time.monotonic()
    buckets = _load_style_buckets(style_id)
    elapsed = time.monotonic() - started
    if elapsed > 2.0:
        logger.warning(
            "ledger 統計拉取慢：%.1f 秒（%s 風格、%d 組）—— 規劃頁會跟著卡",
            elapsed,
            style_id,
            len(buckets),
        )

    def lookup(shot: Shot, tier: QualityTier) -> tuple[float | None, int]:
        if not buckets:
            return None, 0

        values = {
            "tier": tier.value,
            "shot_type": shot.shot_type.value,
            "camera_move": shot.camera_move.value,
            "character_count": len(shot.characters_in_frame),
        }

        # 逐層放寬 —— 跟 query_similar_shots 同一組層級，只是在記憶體裡做。
        for _level_id, _desc, columns in _MATCH_LEVELS:
            keys = [c for c in columns if c != "style_id"]
            total = accepted = 0
            for bucket, (n, ok) in buckets.items():
                if all(getattr(bucket, c) == values[c] for c in keys):
                    total += n
                    accepted += ok
            if total >= _MIN_SAMPLES_PER_LEVEL:
                return accepted / total, total

        return None, 0

    return lookup


def _load_style_buckets(style_id: str) -> dict:
    """把一個風格的所有分組統計一次拉回來。

    回傳 {frozen bucket -> (筆數, 通過數)}。查不到或沒有連線就回空 dict，
    呼叫端一律降級成先驗值。

    只統計 attempt = 1 且 outcome != 'error' —— 跟 query_similar_shots
    同一組條件，理由見那支的說明（重試會高估一次過關率、
    API 故障是基礎設施問題不是鏡頭難度）。
    """
    client = _get_client()
    if client is None:
        return {}

    sql = f"""
        SELECT tier, shot_type, camera_move, character_count,
               count() AS n, countIf(outcome = 'accepted') AS ok
        FROM {_table()}
        WHERE attempt = 1 AND outcome != 'error' AND style_id = %(style_id)s
        GROUP BY tier, shot_type, camera_move, character_count
    """
    try:
        rows = client.query(sql, parameters={"style_id": style_id}).result_rows
    except Exception as exc:  # noqa: BLE001
        logger.error("拉取風格統計失敗，全程改用先驗值：%s", exc)
        return {}

    out = {}
    for tier, shot_type, camera_move, char_count, n, ok in rows:
        key = _Bucket(
            tier=str(tier),
            shot_type=str(shot_type),
            camera_move=str(camera_move),
            character_count=int(char_count),
        )
        out[key] = (int(n), int(ok))
    return out


# ---------------------------------------------------------------------------
# 校準查詢
# ---------------------------------------------------------------------------

def constraint_observations(rule_id: str, style_id: str | None = None) -> dict[str, Any]:
    """查一條約束規則被套用時的實際通過率。

    這個數字**不是因果效益**，不能直接拿去更新 constraints.yaml 的
       expected_lift。原因：

       約束是依鏡頭內容自動觸發的。會套用 hand_anatomy 的鏡頭，
       本來就是有手部特寫的鏡頭 —— 那種鏡頭先天就難。
       所以直接比較「有套用 vs 沒套用」的通過率，會得到
       「套了約束反而比較差」的反向結論。這是典型的選擇偏誤。

       要得到真正的因果效益，必須刻意做 A/B：
       在相同條件的鏡頭上隨機停用某條約束，比較兩組的通過率。
       那需要一個 ab_test_mode 的實驗開關，本版尚未實作。

    所以這個函式只提供觀察值，並在回傳裡明確標示 causal=False。
    UI 顯示這個數字時必須寫「觀察值」而不是「效益」。
    """
    client = _get_client()
    if client is None:
        return {"available": False}

    where = ["rule_id = %(rule_id)s"]
    params: dict[str, Any] = {"rule_id": rule_id}
    if style_id:
        where.append("style_id = %(style_id)s")
        params["style_id"] = style_id

    sql = f"""
        SELECT sum(attempts) AS n, sum(accepted) AS ok
        FROM {_db()}.constraint_stats
        WHERE {' AND '.join(where)}
    """

    try:
        rows = client.query(sql, parameters=params).result_rows
    except Exception as exc:  # noqa: BLE001
        logger.error("查詢 constraint_stats 失敗：%s", exc)
        return {"available": False}

    if not rows or not rows[0][0]:
        return {"available": True, "sample_size": 0}

    total, accepted = int(rows[0][0]), int(rows[0][1])
    return {
        "available": True,
        "rule_id": rule_id,
        "sample_size": total,
        "observed_rate": accepted / total,
        "causal": False,
        "caveat": (
            "這是套用該約束時的觀察通過率，不是約束的因果效益 —— "
            "約束是依鏡頭難度自動觸發的，存在選擇偏誤。"
        ),
    }


def overall_stats(style_id: str | None = None, include_backfill: bool = True) -> dict[str, Any]:
    """整體實測概況。提煉層的第一份原料，也是 UI 顯示「學了多少」的來源。

    `include_backfill=False` 會排除 plan_id 帶 `#backfill` 的列 ——
    那些的 predicted_success_rate 是事後重算的（見 scripts/probes/backfill_ledger.py），
    做校準分析時必須排除，否則等於拿答案回去改考卷。
    成功率統計則不受影響，那部分是純事實。
    """
    client = _get_client()
    if client is None:
        return {"available": False}

    where = ["outcome != 'error'"]
    params: dict[str, Any] = {}
    if style_id:
        where.append("style_id = %(style_id)s")
        params["style_id"] = style_id
    if not include_backfill:
        where.append("plan_id NOT LIKE '%#backfill'")

    sql = f"""
        SELECT
            count()                                   AS total,
            countIf(attempt = 1)                      AS first_tries,
            countIf(attempt = 1 AND outcome = 'accepted') AS first_pass,
            countIf(outcome = 'rejected')             AS rejected,
            sum(cost_usd)                             AS spent,
            uniqExact(shot_id)                        AS shots
        FROM {_table()}
        WHERE {' AND '.join(where)}
    """
    try:
        rows = client.query(sql, parameters=params).result_rows
    except Exception as exc:  # noqa: BLE001
        logger.error("查詢 overall_stats 失敗：%s", exc)
        return {"available": False}

    if not rows or not rows[0][1]:
        return {"available": True, "first_tries": 0}

    total, first_tries, first_pass, rejected, spent, shots = rows[0]
    return {
        "available": True,
        "total": int(total),
        "first_tries": int(first_tries),
        "first_pass": int(first_pass),
        "first_pass_rate": round(int(first_pass) / int(first_tries), 3),
        "rejected": int(rejected),
        "spent_usd": round(float(spent or 0), 2),
        "distinct_shots": int(shots),
    }


# 可以用來切分通過率的欄位。刻意只開放這幾個 ——
# 它們都是 LowCardinality 或小整數，分組出來的桶數有限、看得完。
# 開放 shot_id 之類的高基數欄位會得到「每個桶一筆」的無用結果。
_BREAKDOWN_COLUMNS = {
    "shot_type": "鏡別",
    "camera_move": "運鏡",
    "character_count": "入鏡人數",
    "base_duration_seconds": "秒數",
    "has_dialogue": "有無台詞",
    "has_first_frame": "有無接首尾幀",
}


def feature_breakdown(
    dimension: str, style_id: str | None = None, min_samples: int = 2
) -> list[dict[str, Any]]:
    """按某個鏡頭特徵切開，看哪一種比較容易失敗。

    這是提煉層最重要的原料 —— 「夜景 + 多人的一次過關率只有三成」
    這種話，只有分組統計說得出來，看原始列是看不出來的。

    `min_samples` 預設 2 而不是 5：這裡的目的是**給 LLM 當線索**，
       不是拿來直接蓋掉先驗值（那個門檻在 _MIN_SAMPLES_PER_LEVEL）。
       提煉出來的知識會標明樣本數，讓讀的人自己判斷可信度。
    """
    if dimension not in _BREAKDOWN_COLUMNS:
        raise ValueError(
            f"不能按 {dimension} 切分。可用的：{', '.join(_BREAKDOWN_COLUMNS)}"
        )

    client = _get_client()
    if client is None:
        return []

    where = ["attempt = 1", "outcome != 'error'"]
    params: dict[str, Any] = {"min_samples": min_samples}
    if style_id:
        where.append("style_id = %(style_id)s")
        params["style_id"] = style_id

    sql = f"""
        SELECT
            toString({dimension})         AS bucket,
            count()                       AS n,
            countIf(outcome = 'accepted') AS ok
        FROM {_table()}
        WHERE {' AND '.join(where)}
        GROUP BY bucket
        HAVING n >= %(min_samples)s
        ORDER BY ok / n ASC
    """
    try:
        rows = client.query(sql, parameters=params).result_rows
    except Exception as exc:  # noqa: BLE001
        logger.error("查詢 feature_breakdown(%s) 失敗：%s", dimension, exc)
        return []

    return [
        {
            "dimension": dimension,
            "dimension_label": _BREAKDOWN_COLUMNS[dimension],
            "bucket": str(bucket),
            "sample_size": int(n),
            "pass_rate": round(int(ok) / int(n), 3),
        }
        for bucket, n, ok in rows
    ]


def calibration_report(
    style_id: str | None = None, include_backfill: bool = False
) -> list[dict[str, Any]]:
    """預測 vs 實際的校準報告。

    把預測值分成 10 個桶，看每桶的實際通過率。
    理想狀態：預測 30% 的那一桶，實際也該接近 30%。

    這是誠實原則的量化工具。如果預測 30% 的實際只有 15%，
       代表 priors.yaml 整體偏樂觀，base_success_rate 要下修。
       這份報告應該在 demo 影片裡出現 —— 它證明我們不只是報數字，
       還會回頭檢查自己報得準不準。

    `include_backfill` 預設 False，而且**預設就是對的那個值**：
       回填列的預測值是事後重算的（見 scripts/probes/backfill_ledger.py），
       算進來這份報告就會謊報自己很準。要看全部得自己明確打開。
    """
    client = _get_client()
    if client is None:
        return []

    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if style_id:
        where.append("style_id = %(style_id)s")
        params["style_id"] = style_id
    if not include_backfill:
        where.append("is_backfill = 0")

    sql = f"""
        SELECT
            predicted_bucket,
            sum(attempts) AS n,
            sum(accepted) AS ok
        FROM {_db()}.calibration_stats
        WHERE {' AND '.join(where)}
        GROUP BY predicted_bucket
        ORDER BY predicted_bucket
    """

    try:
        rows = client.query(sql, parameters=params).result_rows
    except Exception as exc:  # noqa: BLE001
        logger.error("查詢 calibration_stats 失敗：%s", exc)
        return []

    report = []
    for bucket, total, accepted in rows:
        total, accepted = int(total), int(accepted)
        if total == 0:
            continue
        predicted_mid = (int(bucket) * 10 + 5) / 100
        actual = accepted / total
        report.append(
            {
                "predicted_range": f"{int(bucket) * 10}–{int(bucket) * 10 + 10}%",
                "predicted_midpoint": predicted_mid,
                "actual_rate": round(actual, 3),
                "error": round(actual - predicted_mid, 3),
                "sample_size": total,
            }
        )
    return report


def recent_records(limit: int = 40, project_id: str | None = None) -> dict[str, Any]:
    """最近幾筆**原始**生成紀錄。

    為什麼要有這個函數？
    UI 要有路徑能點進去看 ClickHouse 裡的原始資料，不然沒人會相信統計。

    學習迴路面板本來只顯示**統計**（幾筆、一次過關率、提煉筆記）——
    那是「我們說我們有」。看的人會問的是
    「給我看資料」。統計可以編，一筆一筆帶時間戳、鏡號、模型 id、
    實際花費的原始列編不出來。

    這是**唯讀**查詢，而且刻意不做分頁 —— 要的是「看得到真的有東西」，
       不是資料瀏覽器。要深入分析的人應該直接連 ClickHouse。
    欄位順序照著「一個人會怎麼讀這一列」排：什麼時候、哪一鏡、
       第幾次、結果如何、為什麼被打回、花了多少。

    Args:
        limit: 最多幾筆。上限 200，避免有人手動打大數字把回應撐爆。
        project_id: 只看這個專案（plan_id 是 `{project_id}:{段落}` 的形式）。

    Returns:
        {"available": bool, "rows": [...], "columns": [...], "table": str}
    """
    client = _get_client()
    if client is None:
        return {"available": False, "rows": [], "columns": []}

    limit = max(1, min(int(limit), 200))
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if project_id:
        where.append("plan_id LIKE %(pfx)s")
        params["pfx"] = f"{project_id}%"

    sql = f"""
        SELECT
            created_at, shot_id, attempt, outcome, tier, shot_type,
            character_count, has_dialogue, predicted_success_rate,
            rejection_reason, cost_usd, latency_seconds, model_id
        FROM {_table()}
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC
        LIMIT {limit}
    """
    try:
        result = client.query(sql, parameters=params)
    except Exception as exc:  # noqa: BLE001
        logger.error("查詢 recent_records 失敗：%s", exc)
        return {"available": False, "rows": [], "columns": []}

    cols = [
        "created_at", "shot_id", "attempt", "outcome", "tier", "shot_type",
        "character_count", "has_dialogue", "predicted_success_rate",
        "rejection_reason", "cost_usd", "latency_seconds", "model_id",
    ]
    rows = []
    for r in result.result_rows:
        item = dict(zip(cols, r))
        item["created_at"] = str(item["created_at"])
        item["predicted_success_rate"] = round(float(item["predicted_success_rate"] or 0), 3)
        item["cost_usd"] = round(float(item["cost_usd"] or 0), 4)
        rows.append(item)

    return {
        "available": True,
        "rows": rows,
        "columns": cols,
        # 讓畫面能標出「這是從哪張表撈的」—— 可信度的一部分
        "table": _table(),
        "database": database_label(),
    }
