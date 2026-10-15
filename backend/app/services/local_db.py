"""學習紀錄的本機資料庫：一個 SQLite 檔，放在使用者自己的資料家目錄。

為什麼要有：原版 Greenlight 的學習紀錄（ledger）寫在雲端的 ClickHouse，那是上一場比賽的合作賽道要求。
本機版不能叫每個使用者去架資料庫 —— Python 內建的 SQLite 就夠：一個人的拍片紀錄是幾千筆，不是幾億筆。

做法：做一個「跟原本的 ClickHouse 連線長得一樣」的物件（insert／query／command 三個動作），
ledger.py 的 13 個查詢函式一行都不用改。差異只有幾個方言，在這裡翻譯：
    countIf(x)   → COALESCE(SUM(CASE WHEN x THEN 1 ELSE 0 END), 0)
    sum(x)       → COALESCE(SUM(x), 0)      ← 沒紀錄時兩者都要是 0，不是 NULL
    count()      → COUNT(*)
    uniqExact(x) → COUNT(DISTINCT x)
    toString(x)  → CAST(x AS TEXT)
    a / b        → CAST(a AS REAL) / b      ← SQLite 整數相除會捨去小數，通過率排序會全部變 0
    %(name)s     → :name
    greenlight.  → （去掉資料庫前綴）
兩張原本由 ClickHouse 自動彙總的表（constraint_stats、calibration_stats）改成檢視表，讀的時候才算 —— 資料量小，免維護。
陣列欄位（content_flags、applied_constraints）存成 JSON 文字，檢視表用 json_each 展開。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime

from .env_boot import data_home

SCHEMA = """
CREATE TABLE IF NOT EXISTS generation_ledger (
    event_id TEXT PRIMARY KEY,
    created_at TEXT,
    plan_id TEXT, shot_id TEXT, attempt INTEGER,
    style_id TEXT, tier TEXT, shot_type TEXT, camera_move TEXT,
    character_count INTEGER, base_duration_seconds INTEGER, extensions INTEGER,
    has_dialogue INTEGER, has_reference_image INTEGER, has_first_frame INTEGER,
    content_flags TEXT, applied_constraints TEXT,
    predicted_success_rate REAL,
    outcome TEXT, rejection_reason TEXT DEFAULT '',
    cost_usd REAL, latency_seconds INTEGER, model_id TEXT,
    prompt_excerpt TEXT DEFAULT '', output_uri TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ledger_style ON generation_ledger(style_id, attempt, outcome);

CREATE VIEW IF NOT EXISTS constraint_stats AS
SELECT j.value AS rule_id, g.style_id, g.shot_type, substr(g.created_at, 1, 10) AS day,
       COUNT(*) AS attempts, SUM(CASE WHEN g.outcome = 'accepted' THEN 1 ELSE 0 END) AS accepted
FROM generation_ledger g, json_each(g.applied_constraints) j
WHERE g.attempt = 1 AND g.outcome != 'error'
GROUP BY rule_id, g.style_id, g.shot_type, day;

CREATE VIEW IF NOT EXISTS calibration_stats AS
SELECT CAST(predicted_success_rate * 10 AS INTEGER) AS predicted_bucket, style_id,
       substr(created_at, 1, 10) AS day,
       CASE WHEN plan_id LIKE '%#backfill' THEN 1 ELSE 0 END AS is_backfill,
       COUNT(*) AS attempts, SUM(CASE WHEN outcome = 'accepted' THEN 1 ELSE 0 END) AS accepted
FROM generation_ledger
WHERE attempt = 1 AND outcome != 'error'
GROUP BY predicted_bucket, style_id, day, is_backfill;
"""


def _replace_call(sql: str, name: str, fn) -> str:
    """把 name(...) 換成 fn(括號裡的內容)；括號可以巢狀。"""
    out, i, pat = [], 0, re.compile(r"\b" + name + r"\(")
    while True:
        m = pat.search(sql, i)
        if not m:
            out.append(sql[i:])
            return "".join(out)
        depth, j = 1, m.end()
        while depth and j < len(sql):
            depth += {"(": 1, ")": -1}.get(sql[j], 0)
            j += 1
        out.append(sql[i:m.start()])
        out.append(fn(sql[m.end():j - 1]))
        i = j


def translate(sql: str) -> str:
    """ClickHouse 方言 → SQLite（只處理 ledger.py 用到的那幾個）。"""
    sql = re.sub(r"\b(greenlight|main)\.", "", sql)
    # 沒有任何紀錄時：ClickHouse 的 countIf／sum 回 0，SQLite 的 SUM 回 NULL ——
    #    新使用者第一次生成就會在 int(None) 炸掉（接線檢查：「抓生成事實失敗…not 'NoneType'」），所以包 COALESCE
    sql = _replace_call(sql, "countIf", lambda x: f"COALESCE(SUM(CASE WHEN {x} THEN 1 ELSE 0 END), 0)")
    sql = _replace_call(sql, "sum", lambda x: f"COALESCE(SUM({x}), 0)")
    sql = _replace_call(sql, "uniqExact", lambda x: f"COUNT(DISTINCT {x})")
    sql = _replace_call(sql, "toString", lambda x: f"CAST({x} AS TEXT)")
    sql = re.sub(r"\bcount\(\)", "COUNT(*)", sql)
    sql = re.sub(r"\b(\w+)\s*/\s*(\w+)\b", r"CAST(\1 AS REAL) / \2", sql)
    sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
    return sql


class _Result:
    def __init__(self, rows, columns):
        self.result_rows = rows
        self.column_names = columns


class LocalClient:
    """模仿原本 ClickHouse 連線的三個動作。"""

    def __init__(self, path=None):
        self.path = path or (data_home() / "learning.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(SCHEMA)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _value(v):
        if isinstance(v, (list, tuple)):
            return json.dumps(list(v), ensure_ascii=False)
        if isinstance(v, uuid.UUID):
            return str(v)
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, bool):
            return int(v)
        return v

    def insert(self, table: str, rows: list, column_names: list) -> None:
        table = table.split(".")[-1]
        cols = ", ".join(column_names)
        marks = ", ".join("?" for _ in column_names)
        with self._lock, self._connect() as con:
            con.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                            [[self._value(v) for v in r] for r in rows])

    def query(self, sql: str, parameters: dict | None = None) -> _Result:
        with self._connect() as con:
            cur = con.execute(translate(sql), parameters or {})
            return _Result(cur.fetchall(), [d[0] for d in cur.description or []])

    def command(self, sql: str, parameters: dict | None = None) -> None:
        with self._lock, self._connect() as con:
            con.execute(translate(sql), parameters or {})
