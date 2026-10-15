"""把 schema 套到 `.env` 指到的那個 ClickHouse。本機、Cloud、GCE 都同一支。

為什麼不用 docker exec 灌 schema：
    那只在「ClickHouse 跑在本機 docker」時有效。接上 ClickHouse Cloud 之後
    根本沒有容器可以 exec。而 schema 只要有兩份施加方式，
    遲早會有一邊漏掉某次改動 —— 然後在最不方便的時候才發現。

    這支走跟應用程式**完全相同**的連線路徑（services/ledger 的環境變數），
    所以「schema 灌得進去」本身就順便證明了「應用程式連得上」。

用法：
    ./.venv/Scripts/python.exe scripts/setup_clickhouse.py            # 建表
    ./.venv/Scripts/python.exe scripts/setup_clickhouse.py --show     # 只看現況
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import env_boot, ledger  # noqa: E402

env_boot.load()

SCHEMA = ROOT / "deployment" / "clickhouse" / "schema.sql"


def split_statements(sql: str) -> list[str]:
    """把 schema.sql 拆成一句一句。

    先剝掉 `--` 註解再切分號 —— 註解裡有分號的話（我們的 schema 註解寫得很長，
    很可能有）直接切會把一句話劈成兩半，而錯誤訊息會指向莫名其妙的地方。
    """
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def target_label() -> str:
    host = os.getenv("CLICKHOUSE_HOST", "localhost")
    port = os.getenv("CLICKHOUSE_PORT", "8123")
    return f"{host}:{port}/{ledger._db()}"  # noqa: SLF001


def show(client) -> None:
    print(f"目標：{target_label()}")
    print(f"版本：{client.query('SELECT version()').result_rows[0][0]}")
    dbs = [r[0] for r in client.query("SHOW DATABASES").result_rows]
    print(f"資料庫：{', '.join(dbs)}")

    db = ledger._db()  # noqa: SLF001
    if db not in dbs:
        print(f"⚠️ 還沒有 {db} 資料庫")
        return

    rows = client.query(
        "SELECT name, engine, total_rows FROM system.tables "
        f"WHERE database = '{db}' ORDER BY name"
    ).result_rows
    if not rows:
        print(f"⚠️ {db} 是空的，還沒建表")
        return
    print()
    print(f"{'表':24s} {'引擎':26s} 筆數")
    for name, engine, total in rows:
        print(f"{name:24s} {engine:26s} {total if total is not None else '-'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="只看現況，不動任何東西")
    args = ap.parse_args()

    # 先天的雞生蛋（接 Cloud 時撞到）：
    #    連線時要指定資料庫，但我們正要建的就是那個資料庫。
    #    clickhouse-connect 會在連線階段就驗證資料庫存不存在，於是
    #    「第一次設定一台全新的 ClickHouse」永遠連不上 ——
    #    而錯誤訊息長得跟「帳密打錯」一模一樣。
    #
    #    解法是先連公用的 `default` 把資料庫建起來，再切回去。
    #    這條在 GCE 自架時會再撞一次，所以寫進腳本不是寫成一次性指令。
    target_db = ledger._db()  # noqa: SLF001
    client = ledger._get_client()  # noqa: SLF001

    if client is None and not args.show:
        print(f"連不上 {target_db}，改連 default 先把資料庫建起來…")
        os.environ["CLICKHOUSE_DB"] = "default"
        ledger.reset_connection()
        boot = ledger._get_client()  # noqa: SLF001
        if boot is not None:
            boot.command(f"CREATE DATABASE IF NOT EXISTS {target_db}")
            print(f"  OK  建立資料庫 {target_db}")
        os.environ["CLICKHOUSE_DB"] = target_db
        ledger.reset_connection()
        client = ledger._get_client()  # noqa: SLF001

    if client is None:
        print("X 連不上。檢查 .env 的 CLICKHOUSE_* 設定。")
        print("  🔴 連不上在本專案是「預期行為」（降級模式），所以只會 log 一行 info，")
        print("     不會有紅字。設錯了跟沒設看起來一模一樣。")
        return 1

    if args.show:
        show(client)
        return 0

    print(f"目標：{target_label()}")
    statements = split_statements(SCHEMA.read_text(encoding="utf-8"))

    # schema.sql 寫死 `greenlight.` 前綴。實際資料庫名稱由 CLICKHOUSE_DB 決定，
    # 兩者不同時要換掉 —— 否則會在正式庫旁邊默默建出第二套表。
    db = ledger._db()  # noqa: SLF001
    if db != "greenlight":
        statements = [re.sub(r"\bgreenlight\.", f"{db}.", s) for s in statements]
        statements = [
            s.replace("CREATE DATABASE IF NOT EXISTS greenlight", f"CREATE DATABASE IF NOT EXISTS {db}")
            for s in statements
        ]

    print(f"要執行 {len(statements)} 個語句")
    print()

    for i, stmt in enumerate(statements, 1):
        head = " ".join(stmt.split())[:64]
        try:
            client.command(stmt)
            print(f"  OK  {i}. {head}")
        except Exception as exc:  # noqa: BLE001
            print(f"  X   {i}. {head}")
            print(f"      {str(exc).splitlines()[0][:160]}")
            return 1

    print()
    show(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
