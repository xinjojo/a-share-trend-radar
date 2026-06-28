"""V3 真实快照数据库。

本模块保存系统每天实际生成出来的市场、主线、股票池和 Action 结果。
这些快照是未来前向验证的真实样本，不回填历史、不伪造字段。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import pandas as pd

from config import HISTORY_DB_PATH
from src.utils import safe_float, safe_int


SNAPSHOT_TABLES = ("market_snapshot", "sector_snapshot", "stock_snapshot", "action_snapshot")
HISTORY_DB_LABEL = "data/radar_history.db"


def get_history_connection() -> sqlite3.Connection:
    """创建 V3 历史快照库连接。"""
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(HISTORY_DB_PATH)


def init_history_db() -> None:
    """初始化真实快照数据库表。"""
    with get_history_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_snapshot (
                date TEXT PRIMARY KEY,
                generated_at TEXT NOT NULL,
                market_score REAL,
                risk_preference TEXT,
                sample_count INTEGER,
                is_full_market_sample INTEGER,
                sample_note TEXT,
                up_count INTEGER,
                down_count INTEGER,
                limit_up INTEGER,
                limit_down INTEGER,
                total_amount_yi REAL,
                explanation TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sector_snapshot (
                date TEXT NOT NULL,
                sector_code TEXT NOT NULL,
                sector_name TEXT NOT NULL,
                sector_type TEXT,
                rank INTEGER,
                category TEXT,
                score REAL,
                opportunity_score REAL,
                risk_score REAL,
                confidence_score REAL,
                lifecycle_stage TEXT,
                action TEXT,
                action_reason TEXT,
                top_stock TEXT,
                market_temperature REAL,
                amount_ratio_20 REAL,
                up_ratio REAL,
                distance_ma20_pct REAL,
                payload_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (date, sector_code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_snapshot (
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                stock_research_group TEXT,
                board_name TEXT,
                matched_action TEXT,
                matched_lifecycle TEXT,
                leader_score REAL,
                research_priority_score REAL,
                observe_status TEXT,
                trend_status TEXT,
                close REAL,
                ma20 REAL,
                distance_ma20_pct REAL,
                price_basis TEXT,
                stock_group_reason TEXT,
                payload_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (date, code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_snapshot (
                date TEXT NOT NULL,
                action TEXT NOT NULL,
                sector_name TEXT NOT NULL,
                position INTEGER NOT NULL,
                reason TEXT,
                score REAL,
                opportunity_score REAL,
                risk_score REAL,
                confidence_score REAL,
                candidate_count INTEGER,
                signal_note TEXT,
                payload_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (date, action, sector_name, position)
            )
            """
        )


def save_radar_history_snapshot(
    report_date: str,
    market_temperature: dict[str, Any],
    sector_df: pd.DataFrame | None,
    stock_groups: dict[str, pd.DataFrame] | None,
    actions: dict[str, list[dict[str, Any]]] | None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """覆盖保存某一天真实生成的四类快照。"""
    init_history_db()
    generated_at = generated_at or datetime.now().isoformat(timespec="seconds")
    market_rows = [_market_row(report_date, generated_at, market_temperature)]
    sector_rows = _sector_rows(report_date, generated_at, sector_df, market_temperature)
    stock_rows = _stock_rows(report_date, generated_at, stock_groups)
    action_rows = _action_rows(report_date, generated_at, actions)
    with get_history_connection() as conn:
        for table in SNAPSHOT_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE date = ?", (report_date,))
        conn.executemany(
            """
            INSERT INTO market_snapshot(
                date, generated_at, market_score, risk_preference, sample_count,
                is_full_market_sample, sample_note, up_count, down_count, limit_up,
                limit_down, total_amount_yi, explanation, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            market_rows,
        )
        conn.executemany(
            """
            INSERT INTO sector_snapshot(
                date, sector_code, sector_name, sector_type, rank, category, score,
                opportunity_score, risk_score, confidence_score, lifecycle_stage, action,
                action_reason, top_stock, market_temperature, amount_ratio_20, up_ratio,
                distance_ma20_pct, payload_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sector_rows,
        )
        conn.executemany(
            """
            INSERT INTO stock_snapshot(
                date, code, name, stock_research_group, board_name, matched_action,
                matched_lifecycle, leader_score, research_priority_score, observe_status,
                trend_status, close, ma20, distance_ma20_pct, price_basis,
                stock_group_reason, payload_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            stock_rows,
        )
        conn.executemany(
            """
            INSERT INTO action_snapshot(
                date, action, sector_name, position, reason, score, opportunity_score,
                risk_score, confidence_score, candidate_count, signal_note,
                payload_json, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            action_rows,
        )
    return {
        "saved": True,
        "database": HISTORY_DB_LABEL,
        "generated_at": generated_at,
        "market_rows": len(market_rows),
        "sector_rows": len(sector_rows),
        "stock_rows": len(stock_rows),
        "action_rows": len(action_rows),
        "message": "历史快照已保存",
    }


def load_snapshot_status(report_date: str) -> dict[str, Any]:
    """读取某日快照保存状态，用于页面展示和自检。"""
    init_history_db()
    with get_history_connection() as conn:
        row = conn.execute(
            "SELECT generated_at FROM market_snapshot WHERE date = ?",
            (report_date,),
        ).fetchone()
        counts = {}
        for table in SNAPSHOT_TABLES:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE date = ?", (report_date,)).fetchone()[0]
    return {
        "saved": bool(row),
        "database": HISTORY_DB_LABEL,
        "generated_at": row[0] if row else "",
        "market_rows": counts.get("market_snapshot", 0),
        "sector_rows": counts.get("sector_snapshot", 0),
        "stock_rows": counts.get("stock_snapshot", 0),
        "action_rows": counts.get("action_snapshot", 0),
        "message": "历史快照已保存" if row else "历史快照尚未保存",
    }


def _market_row(report_date: str, generated_at: str, market_temperature: dict[str, Any]) -> tuple[Any, ...]:
    """把市场温度字典转为入库行。"""
    metrics = market_temperature.get("metrics", {}) if isinstance(market_temperature, dict) else {}
    return (
        report_date,
        generated_at,
        safe_float(market_temperature.get("score") if isinstance(market_temperature, dict) else 0),
        str(market_temperature.get("risk_preference", "") if isinstance(market_temperature, dict) else ""),
        safe_int(metrics.get("sample_count") or metrics.get("total")),
        1 if bool(metrics.get("is_full_market_sample", True)) else 0,
        str(metrics.get("sample_note", "")),
        safe_int(metrics.get("up_count")),
        safe_int(metrics.get("down_count")),
        safe_int(metrics.get("limit_up")),
        safe_int(metrics.get("limit_down")),
        safe_float(metrics.get("total_amount_yi")),
        str(market_temperature.get("explanation", "") if isinstance(market_temperature, dict) else ""),
        _json(market_temperature),
    )


def _sector_rows(
    report_date: str,
    generated_at: str,
    sector_df: pd.DataFrame | None,
    market_temperature: dict[str, Any],
) -> list[tuple[Any, ...]]:
    """把主线表转为入库行。"""
    if sector_df is None or sector_df.empty:
        return []
    rows = []
    market_score = safe_float(market_temperature.get("score") if isinstance(market_temperature, dict) else 0)
    for _, row in sector_df.iterrows():
        payload = row.to_dict()
        rows.append(
            (
                report_date,
                str(row.get("board_code", "")),
                str(row.get("board_name", "")),
                str(row.get("board_layer", row.get("board_type", ""))),
                safe_int(row.get("rank")),
                str(row.get("category", "")),
                safe_float(row.get("score")),
                safe_float(row.get("opportunity_score")),
                safe_float(row.get("risk_score")),
                safe_float(row.get("confidence_score")),
                str(row.get("lifecycle_state", "")),
                str(row.get("action", "")),
                str(row.get("action_reason", "")),
                _top_stock(row),
                market_score,
                safe_float(row.get("amount_ratio_20")),
                safe_float(row.get("up_ratio")),
                safe_float(row.get("distance_ma20_pct")),
                _json(payload),
                generated_at,
            )
        )
    return rows


def _stock_rows(
    report_date: str,
    generated_at: str,
    stock_groups: dict[str, pd.DataFrame] | None,
) -> list[tuple[Any, ...]]:
    """把五栏股票池转为入库行。"""
    rows = []
    seen: set[str] = set()
    for group_name, df in (stock_groups or {}).items():
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("code", ""))
            if not code or code in seen:
                continue
            seen.add(code)
            payload = row.to_dict()
            payload["stock_research_group"] = str(row.get("stock_research_group", group_name))
            rows.append(
                (
                    report_date,
                    code,
                    str(row.get("name", "")),
                    str(row.get("stock_research_group", group_name)),
                    str(row.get("board_name", "")),
                    str(row.get("matched_action", "")),
                    str(row.get("matched_lifecycle", "")),
                    safe_float(row.get("leader_score")),
                    safe_float(row.get("research_priority_score")),
                    str(row.get("observe_status", "")),
                    str(row.get("trend_status", "")),
                    safe_float(row.get("close")),
                    safe_float(row.get("ma20")),
                    safe_float(row.get("distance_ma20_pct")),
                    str(row.get("price_basis", "")),
                    str(row.get("stock_group_reason", "")),
                    _json(payload),
                    generated_at,
                )
            )
    return rows


def _action_rows(
    report_date: str,
    generated_at: str,
    actions: dict[str, list[dict[str, Any]]] | None,
) -> list[tuple[Any, ...]]:
    """把今日 Action 四组转为入库行。"""
    rows = []
    for action, items in (actions or {}).items():
        for position, item in enumerate(items or [], start=1):
            rows.append(
                (
                    report_date,
                    str(action),
                    str(item.get("board_name", "")),
                    position,
                    str(item.get("reason", "")),
                    safe_float(item.get("score")),
                    safe_float(item.get("opportunity_score")),
                    safe_float(item.get("risk_score")),
                    safe_float(item.get("confidence_score")),
                    safe_int(item.get("candidate_count")),
                    str(item.get("signal_note", "")),
                    _json(item),
                    generated_at,
                )
            )
    return rows


def _top_stock(row: pd.Series) -> str:
    """提取主线快照的核心股票。"""
    top_stocks = str(row.get("top_stocks", ""))
    if top_stocks:
        return top_stocks.split("、")[0]
    return str(row.get("leader", ""))


def _json(payload: Any) -> str:
    """安全序列化，避免 NaN/NA 写入 JSON。"""
    return json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False, default=str)


def _json_safe(value: Any) -> Any:
    """递归清理 pandas/numpy 类型。"""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value
