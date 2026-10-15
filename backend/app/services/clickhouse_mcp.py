"""透過 **官方 mcp-clickhouse MCP server** 存取 ClickHouse。

═══════════════════════════════════════════════════════════════════════════
為什麼有這一層
═══════════════════════════════════════════════════════════════════════════

存取管道是硬性要求：ClickHouse 必須在 runtime 透過官方
MCP server（mcp-clickhouse）被真的查詢，而不是走 `clickhouse_connect` 直連。
`ledger.py` 原本整層直連 —— **功能是對的，管道是錯的**，
所以加這一層把管道換掉。

═══════════════════════════════════════════════════════════════════════════
設計：長得像舊 client，內部走 MCP
═══════════════════════════════════════════════════════════════════════════

`ledger.py` 有十幾處 `client.query(...) .result_rows` 與一處 `client.insert(...)`。
與其改十幾個呼叫點，這一層提供一個**介面相容**的替身：

    client.query(sql)   → .result_rows / .column_names   （同 clickhouse_connect）
    client.command(sql) → 不取結果的 DDL/DML
    client.insert(table, rows, column_names=[...])       → 組成 INSERT SQL
    client.ping()       → 連通性檢查

呼叫端一行都不用改。**管道換掉、功能不動。**

═══════════════════════════════════════════════════════════════════════════
async / sync 橋接
═══════════════════════════════════════════════════════════════════════════

MCP Python SDK 全是 async，而 `ledger.py` 是同步的、被 FastAPI 的同步
route 呼叫（那些 route 跑在 threadpool 裡）。

不能用 `asyncio.run()` 逐次呼叫 —— 那會每次查詢都重開一次 stdio 子進程與
MCP 握手（實測光是連線握手就要一秒多）。

作法：**開一條背景執行緒跑自己的 event loop**，session 在裡面長期存活；
同步端用 `run_coroutine_threadsafe` 把工作丟進去、等結果。

═══════════════════════════════════════════════════════════════════════════
連不上一律降級，不能擋住使用者
═══════════════════════════════════════════════════════════════════════════

跟舊的直連層同一條紀律：ClickHouse 不通時整個系統退回先驗值繼續運作，
只 log 一行。統計寫不進去是系統內部的事，不是使用者的事。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# 這個數字必須大於下面的 _QUERY_TIMEOUT。
#
# start() 等的「就緒」裡面**包含 connect() 的那個 ping**，而 ping 最久
# 可以跑到 _QUERY_TIMEOUT 秒。外層等待比它要等的內層短，
# 對休眠中的 ClickHouse 就是必然失敗（例如 60 < 120）。
#
# 實測代價（冷啟動）：外層 60 秒到就放棄、降級，但那個 ping 已經把
# ClickHouse 叫醒了；不到一分鐘後 MCP 其實就緒，只是 client 已經被丟掉，
# 要等 ledger 的冷卻結束、重開一個子進程才成功。
# 醒來只要約 100 秒，使用者卻吃了約 240 秒的降級（學習迴路整區消失）。
#
# 150 秒 = 120（ping 上限）+ 啟動開銷與餘裕。
# 代價是冷啟動後第一個請求要等最多 150 秒，但那是喚醒休眠資料庫的
# 固有成本 —— 拿它換掉「四分鐘的錯誤資料」是划算的。
_STARTUP_TIMEOUT = float(os.getenv("CLICKHOUSE_MCP_STARTUP_TIMEOUT", "150"))
_CALL_TIMEOUT = float(os.getenv("CLICKHOUSE_MCP_CALL_TIMEOUT", "180"))

# mcp-clickhouse 自己的查詢逾時，預設只有 30 秒：
#
# ClickHouse Cloud 閒置會自動休眠，**喚醒本身就要超過 30 秒**
# （實測 ping 在 30 秒逾時、再幾秒之後其實就連上了）。
# 用預設值的話第一個查詢注定超時，而那個查詢正好是 connect() 的 ping。
_QUERY_TIMEOUT = os.getenv("CLICKHOUSE_MCP_QUERY_TIMEOUT", "120")

# 把「外層等待必須大於內層」這條規則交給 code 檢查，不是靠註釋。
#
#    這三個數字都可以用環境變數覆寫，而它們之間的大小關係是有意義的：
#    start() 等的就緒包含一個最久跑 _QUERY_TIMEOUT 的 ping。
#    調錯順序的後果不是報錯，是**靜默降級** —— 系統照跑，
#    只是成功率全部改用先驗值猜，而且沒有人會發現。
#    寧可在啟動時就炸掉。
if _STARTUP_TIMEOUT <= float(_QUERY_TIMEOUT):
    raise RuntimeError(
        f"設定衝突：CLICKHOUSE_MCP_STARTUP_TIMEOUT（{_STARTUP_TIMEOUT:.0f}s）"
        f"必須大於 CLICKHOUSE_MCP_QUERY_TIMEOUT（{_QUERY_TIMEOUT}s）——"
        f"啟動等待包含那個 ping，比它短的話對休眠中的 ClickHouse 必然失敗。"
    )


class MCPUnavailable(RuntimeError):
    """MCP server 起不來或連不上。呼叫端應該降級，不是往上拋。"""


# ---------------------------------------------------------------------------
# SQL literal 序列化
#
# mcp-clickhouse 只吃 SQL 字串（`run_query`），沒有參數化介面 ——
# INSERT 的值必須自己序列化進 SQL。
# ---------------------------------------------------------------------------

def _quote(value: Any) -> str:
    """把 Python 值轉成 ClickHouse SQL literal。

    為什麼要有這個函式？
        `client.insert(table, rows, column_names=...)` 原本由驅動負責
        型別轉換與跳脫。改走 MCP 之後只剩「送一段 SQL 字串」，
        這件事變成這一層的責任。

    設計來源：改走 mcp-clickhouse。

    這裡處理的全是**系統自己產生**的值（提示詞、URI、旗標），
       不是使用者直接輸入的表單。但提示詞裡本來就會有單引號與反斜線，
       跳脫錯了就是一筆寫不進去的紀錄 + 一個看不懂的 SQL 錯誤。

    布林要在 int 之前判斷 —— Python 的 `bool` 是 `int` 的子類別，
       順序顛倒的話 True 會被寫成 1 而不是 ClickHouse 的 true/false，
       欄位型別是 Bool 時會被拒絕。

    Args:
        value: 要序列化的 Python 值。

    Returns:
        可以直接嵌進 SQL 的字串片段。
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):  # 必須在 int 之前
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        # ClickHouse DateTime 不吃時區後綴，統一轉成 UTC 的無時區字串
        if value.tzinfo is not None:
            value = value.astimezone(tz=None).replace(tzinfo=None)
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    if isinstance(value, uuid.UUID):
        return f"'{value}'"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_quote(v) for v in value) + "]"
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


_PARAM_RE = re.compile(r"%\((\w+)\)s")


def _interpolate(sql: str, parameters: dict[str, Any] | None) -> str:
    """把 `%(name)s` 佔位符換成序列化後的值。

    為什麼要有這個函式？
        `ledger.py` 的統計查詢用 DB-API 的 `%(name)s` 參數化寫法，
        由驅動負責帶入。mcp-clickhouse 只有 `run_query(query)` ——
        沒有參數通道，內插變成這一層的責任。

    設計來源：改走 MCP 之後 ledger 的參數化查詢會丟出
        `query() got an unexpected keyword argument 'parameters'`，
        所以參數內插要在這裡補上。

    缺參數要**當場拋錯**，不能靜靜留下沒替換的 `%(name)s` ——
       那會變成一個語法怪異但可能仍然執行的 SQL，
       回一組看起來正常、實際上條件錯掉的統計數字。

    用 function 形式的 `re.sub`：替換字串裡有反斜線（跳脫過的引號），
       字串形式會被當成 backreference 解釋。

    Args:
        sql: 含 `%(name)s` 佔位符的 SQL。
        parameters: 佔位符對應的值。

    Returns:
        佔位符都已替換成 SQL literal 的完整語句。
    """
    if not parameters:
        return sql

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in parameters:
            raise MCPUnavailable(f"SQL 參數「{key}」沒有給值")
        return _quote(parameters[key])

    return _PARAM_RE.sub(replace, sql)


# ---------------------------------------------------------------------------
# 結果物件 —— 介面對齊 clickhouse_connect
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    """跟 `clickhouse_connect` 的查詢結果同形狀（只保留有用到的兩個屬性）。"""

    result_rows: list[list[Any]] = field(default_factory=list)
    column_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 背景 event loop + MCP session
# ---------------------------------------------------------------------------

class _SessionThread:
    """一條背景執行緒，裡面有 event loop 與長期存活的 MCP session。

    為什麼不用 `asyncio.run()` 逐次呼叫？
        每一次都會重新 spawn stdio 子進程、重跑 MCP 握手、重連 ClickHouse。
        實測光握手就一秒多，而規劃一個段落會查好幾次 —— 那個成本會直接
        變成使用者盯著轉圈的秒數。

    這條執行緒是 daemon：主程式結束時不等它。MCP 子進程由
       stdio_client 的 context manager 負責收，正常關閉會一起帶走。
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._stop = threading.Event()

    # -- 啟動 --------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="clickhouse-mcp", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=_STARTUP_TIMEOUT):
            raise MCPUnavailable(
                f"mcp-clickhouse 在 {_STARTUP_TIMEOUT:.0f} 秒內沒有就緒"
            )
        if self._error is not None:
            raise MCPUnavailable(str(self._error))

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - 起不來就降級
            self._error = exc
            self._ready.set()
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    async def _serve(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_clickhouse.main"],
            env=_server_env(),
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                self._ready.set()
                # 撐住這個 context 直到被要求關閉 —— 一離開 async with，
                # 子進程就會被收掉、session 也失效。
                while not self._stop.is_set():
                    await asyncio.sleep(0.2)

    # -- 呼叫 --------------------------------------------------------------

    def call_tool(self, name: str, args: dict) -> Any:
        """同步呼叫一個 MCP 工具。逾時或失敗一律拋 MCPUnavailable。"""
        if self._loop is None or self._session is None:
            raise MCPUnavailable("MCP session 尚未就緒")

        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, args), self._loop
        )
        try:
            return future.result(timeout=_CALL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            raise MCPUnavailable(f"MCP 呼叫 {name} 失敗：{exc}") from exc

    def stop(self) -> None:
        self._stop.set()
        self._session = None


def _server_env() -> dict[str, str]:
    """組出 mcp-clickhouse 子進程要的環境變數。

    專案用 `CLICKHOUSE_DB`，mcp-clickhouse 讀的是 `CLICKHOUSE_DATABASE`
       —— 名字不一樣。這裡做轉換，不要求使用者多設一個變數。

    Windows 的子進程需要 PATH / SystemRoot / COMSPEC 才起得來；
       Linux 容器只需要 PATH。缺了會是「子進程立刻死掉」這種很難查的症狀。

    寫入預設是關的。ledger 要 INSERT，所以明確打開 ——
       但 **DROP/TRUNCATE 不開**（那要另一個旗標），
       產品 runtime 沒有任何理由刪表。
    """
    env: dict[str, str] = {}

    for key in (
        "CLICKHOUSE_HOST",
        "CLICKHOUSE_PORT",
        "CLICKHOUSE_USER",
        "CLICKHOUSE_PASSWORD",
    ):
        value = os.getenv(key)
        if value:
            env[key] = value

    database = os.getenv("CLICKHOUSE_DATABASE") or os.getenv("CLICKHOUSE_DB")
    if database:
        env["CLICKHOUSE_DATABASE"] = database

    port = os.getenv("CLICKHOUSE_PORT", "")
    secure_env = os.getenv("CLICKHOUSE_SECURE")
    secure = (
        secure_env.strip().lower() in ("1", "true", "yes", "on")
        if secure_env
        else port == "8443"
    )
    env["CLICKHOUSE_SECURE"] = "true" if secure else "false"
    env["CLICKHOUSE_VERIFY"] = os.getenv("CLICKHOUSE_VERIFY", "true")

    # 見檔頭 _QUERY_TIMEOUT 的說明：預設 30 秒接不住 Cloud 的喚醒時間。
    env["CLICKHOUSE_MCP_QUERY_TIMEOUT"] = _QUERY_TIMEOUT

    env["CLICKHOUSE_ALLOW_WRITE_ACCESS"] = "true"
    # 測試腳本要建/刪自己的資料庫時才打開，產品 runtime 不需要。
    if os.getenv("CLICKHOUSE_ALLOW_DROP", "").strip().lower() in ("1", "true", "yes"):
        env["CLICKHOUSE_ALLOW_DROP"] = "true"

    for key in ("PATH", "SystemRoot", "TEMP", "TMP", "COMSPEC", "LANG", "LC_ALL"):
        value = os.getenv(key)
        if value:
            env[key] = value

    return env


# ---------------------------------------------------------------------------
# 對外的 client
# ---------------------------------------------------------------------------

class MCPClickHouseClient:
    """介面對齊 `clickhouse_connect`，內部走官方 mcp-clickhouse MCP server。

    為什麼長成 clickhouse_connect 的樣子？
        `ledger.py` 有十幾處呼叫點。換管道是為了符合存取要求，不是為了重寫
        統計邏輯 —— 讓那些呼叫點一行都不用改，改動的風險就只剩這一個檔。

    設計來源：官方 MCP server 存取管道是硬性要求。

    不能刪：刪了就退回 `clickhouse_connect` 直連，不符合存取要求。
    """

    def __init__(self, thread: _SessionThread) -> None:
        self._thread = thread

    # -- 內部 --------------------------------------------------------------

    def _run_query(self, sql: str) -> QueryResult:
        result = self._thread.call_tool("run_query", {"query": sql})

        if getattr(result, "isError", False):
            message = _first_text(result) or "未知錯誤"
            raise MCPUnavailable(f"查詢失敗：{message[:300]}")

        payload = _first_text(result)
        if not payload:
            return QueryResult()

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            # DDL 之類沒有結果集的語句，server 可能回非 JSON 的訊息
            return QueryResult()

        return QueryResult(
            result_rows=[list(row) for row in data.get("rows", [])],
            column_names=list(data.get("columns", [])),
        )

    # -- 公開介面（對齊 clickhouse_connect）--------------------------------

    def query(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> QueryResult:
        """跑一段 SELECT，回傳有 `.result_rows` / `.column_names` 的結果。

        `parameters` 用 `%(name)s` 佔位符，跟舊驅動的寫法一致 ——
        ledger 那六處統計查詢一行都不用改。
        """
        return self._run_query(_interpolate(sql, parameters))

    def command(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> None:
        """跑一段不取結果的語句（DDL / DML）。"""
        self._run_query(_interpolate(sql, parameters))

    def insert(
        self,
        table: str,
        data: list[list[Any]],
        column_names: list[str] | None = None,
    ) -> None:
        """插入資料列。內部組成 `INSERT INTO ... VALUES ...` 走 MCP。

        值的跳脫由 `_quote()` 負責。提示詞裡的單引號與反斜線是常態，
           不是邊緣案例。
        """
        if not data:
            return

        columns = f" ({', '.join(column_names)})" if column_names else ""
        values = ", ".join(
            "(" + ", ".join(_quote(v) for v in row) + ")" for row in data
        )
        self._run_query(f"INSERT INTO {table}{columns} VALUES {values}")

    def ping(self) -> bool:
        """連通性檢查。跟舊介面一樣在連不上時拋例外。"""
        self._run_query("SELECT 1")
        return True


def _first_text(result: Any) -> str:
    """從 MCP 的 CallToolResult 裡取出第一段文字內容。"""
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            return text
    return ""


# ---------------------------------------------------------------------------
# 建立
# ---------------------------------------------------------------------------

def connect() -> MCPClickHouseClient:
    """啟動 mcp-clickhouse 並回傳可用的 client。

    失敗一律拋 `MCPUnavailable` —— 呼叫端（ledger）接住之後降級成先驗值。

    每次呼叫都會 spawn 一個新的 MCP 子進程。呼叫端要自己快取
       （`ledger._get_client()` 有做），不要在每個請求裡呼叫這支。
    """
    thread = _SessionThread()
    thread.start()
    client = MCPClickHouseClient(thread)
    client.ping()
    return client
