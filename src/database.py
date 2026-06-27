"""SQLite 持久化，用于保存快照和日报。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """创建 SQLite 连接。"""
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    """初始化基础表。"""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                report_date TEXT PRIMARY KEY,
                markdown TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                rows_count INTEGER NOT NULL,
                PRIMARY KEY (name, created_at)
            )
            """
        )


def save_dataframe_snapshot(name: str, df: pd.DataFrame) -> None:
    """把 DataFrame 保存到 SQLite 同名表，并记录快照元信息。"""
    if df is None:
        return
    init_db()
    created_at = datetime.now().isoformat(timespec="seconds")
    table_name = f"snapshot_{name}"
    with get_connection() as conn:
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        conn.execute(
            "INSERT OR REPLACE INTO snapshots(name, created_at, rows_count) VALUES (?, ?, ?)",
            (name, created_at, len(df)),
        )


def save_report(report_date: str, markdown: str) -> None:
    """保存 Markdown 日报。"""
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO reports(report_date, markdown, created_at)
            VALUES (?, ?, ?)
            """,
            (report_date, markdown, datetime.now().isoformat(timespec="seconds")),
        )


def load_report(report_date: str) -> str:
    """读取指定日期日报。"""
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT markdown FROM reports WHERE report_date = ?",
            (report_date,),
        ).fetchone()
    return row[0] if row else ""

