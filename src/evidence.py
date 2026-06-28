"""V4 证据摘要。

证据只来自已经保存的真实快照和用户显式运行过的回测结果。
样本不足时必须提示，不用模拟数据填充胜率。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pandas as pd

from config import HISTORY_DB_PATH
from src.history_db import init_history_db
from src.utils import safe_float


TECHNICAL_RULES = ["放量反包", "缩量回踩MA5", "缩量回踩MA10", "缩量回踩MA20", "MA多头排列", "高位过热", "跌破MA20"]
LIFECYCLE_RULES = ["启动期", "主升期", "高潮期", "分歧期", "退潮期", "修复期"]


def build_evidence_summary(report_date: str | None = None) -> dict[str, Any]:
    """读取真实快照和技术信号回测，生成 Evidence 摘要。"""
    init_history_db()
    snapshot_stats = _snapshot_stats()
    technical = _technical_evidence()
    lifecycle = _lifecycle_evidence()
    total_events = sum(int(row.get("sample_count", 0) or 0) for row in technical)
    enough = snapshot_stats.get("snapshot_days", 0) >= 20 or total_events >= 200
    summary = (
        f"当前已有真实快照 {snapshot_stats.get('snapshot_days', 0)} 天、技术信号样本 {total_events} 次。"
        if enough
        else f"当前真实快照 {snapshot_stats.get('snapshot_days', 0)} 天、技术信号样本 {total_events} 次，证据仍不足。"
    )
    return {
        "report_date": report_date or "",
        "summary": summary,
        "snapshot_stats": snapshot_stats,
        "technical_rules": technical,
        "lifecycle_rules": lifecycle,
        "evidence_level": "可参考" if enough else "样本不足",
    }


def _snapshot_stats() -> dict[str, Any]:
    """统计真实快照覆盖天数和记录数。"""
    if not HISTORY_DB_PATH.exists():
        return {"snapshot_days": 0, "sector_samples": 0, "stock_samples": 0, "action_samples": 0}
    with sqlite3.connect(HISTORY_DB_PATH) as conn:
        return {
            "snapshot_days": _single_int(conn, "SELECT COUNT(DISTINCT date) FROM market_snapshot"),
            "sector_samples": _single_int(conn, "SELECT COUNT(*) FROM sector_snapshot"),
            "stock_samples": _single_int(conn, "SELECT COUNT(*) FROM stock_snapshot"),
            "action_samples": _single_int(conn, "SELECT COUNT(*) FROM action_snapshot"),
        }


def _technical_evidence() -> list[dict[str, Any]]:
    """读取最近一次技术信号回测摘要。"""
    if not HISTORY_DB_PATH.exists():
        return _empty_technical_rules()
    with sqlite3.connect(HISTORY_DB_PATH) as conn:
        if not _table_exists(conn, "technical_backtest_runs"):
            return _empty_technical_rules()
        row = conn.execute(
            """
            SELECT run_id, created_at, start_date, end_date, event_count, summary_json
            FROM technical_backtest_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return _empty_technical_rules()
    run_id, created_at, start_date, end_date, event_count, summary_json = row
    try:
        summary = pd.DataFrame(json.loads(summary_json or "[]"))
    except Exception:
        summary = pd.DataFrame()
    if summary.empty:
        return _empty_technical_rules(run_id=run_id, created_at=created_at, event_count=event_count)
    rows = []
    for rule in TECHNICAL_RULES:
        item = summary[(summary["signal"].astype(str) == rule) & (pd.to_numeric(summary["horizon"], errors="coerce") == 10)]
        if item.empty:
            item = summary[summary["signal"].astype(str) == rule].sort_values("horizon").tail(1)
        if item.empty:
            rows.append(_technical_rule_row(rule, run_id, created_at, start_date, end_date, 0))
            continue
        record = item.iloc[0]
        rows.append(
            {
                "rule": rule,
                "evidence_type": "个股技术信号回测",
                "run_id": run_id,
                "created_at": created_at,
                "period": f"{start_date} 至 {end_date}",
                "horizon": int(safe_float(record.get("horizon"))),
                "sample_count": int(safe_float(record.get("occurrences"))),
                "win_rate_pct": round(safe_float(record.get("win_rate_pct")), 2),
                "avg_return_pct": round(safe_float(record.get("avg_return_pct")), 2),
                "max_drawdown_pct": round(safe_float(record.get("worst_max_drawdown_pct")), 2),
                "status": "可参考" if safe_float(record.get("occurrences")) >= 30 else "样本不足",
            }
        )
    return rows


def _lifecycle_evidence() -> list[dict[str, Any]]:
    """统计生命周期真实快照样本。收益验证后续由前向数据累积。"""
    if not HISTORY_DB_PATH.exists():
        return _empty_lifecycle_rules()
    with sqlite3.connect(HISTORY_DB_PATH) as conn:
        if not _table_exists(conn, "sector_snapshot"):
            return _empty_lifecycle_rules()
        try:
            df = pd.read_sql_query(
                """
                SELECT lifecycle_stage, COUNT(*) AS sample_count,
                       AVG(score) AS avg_score,
                       AVG(opportunity_score) AS avg_opportunity,
                       AVG(risk_score) AS avg_risk
                FROM sector_snapshot
                GROUP BY lifecycle_stage
                """,
                conn,
            )
        except Exception:
            df = pd.DataFrame()
    rows = []
    for rule in LIFECYCLE_RULES:
        item = df[df["lifecycle_stage"].astype(str) == rule] if not df.empty and "lifecycle_stage" in df.columns else pd.DataFrame()
        if item.empty:
            rows.append(_lifecycle_rule_row(rule, 0))
            continue
        record = item.iloc[0]
        rows.append(
            {
                "rule": rule,
                "evidence_type": "真实快照前向样本",
                "sample_count": int(safe_float(record.get("sample_count"))),
                "avg_score": round(safe_float(record.get("avg_score")), 2),
                "avg_opportunity": round(safe_float(record.get("avg_opportunity")), 2),
                "avg_risk": round(safe_float(record.get("avg_risk")), 2),
                "avg_return_pct": None,
                "win_rate_pct": None,
                "max_drawdown_pct": None,
                "status": "待收益归因" if safe_float(record.get("sample_count")) >= 30 else "样本不足",
                "note": "生命周期收益需要连续真实快照后做前向归因，目前不伪造历史 Action。",
            }
        )
    return rows


def _empty_technical_rules(run_id: str = "", created_at: str = "", event_count: int = 0) -> list[dict[str, Any]]:
    """技术信号没有回测样本时的占位。"""
    return [_technical_rule_row(rule, run_id, created_at, "", "", event_count) for rule in TECHNICAL_RULES]


def _technical_rule_row(
    rule: str,
    run_id: str,
    created_at: str,
    start_date: str,
    end_date: str,
    event_count: int,
) -> dict[str, Any]:
    """技术规则占位行。"""
    return {
        "rule": rule,
        "evidence_type": "个股技术信号回测",
        "run_id": run_id,
        "created_at": created_at,
        "period": f"{start_date} 至 {end_date}" if start_date or end_date else "",
        "horizon": "",
        "sample_count": 0,
        "win_rate_pct": None,
        "avg_return_pct": None,
        "max_drawdown_pct": None,
        "status": "样本不足",
        "note": f"最近回测事件数 {event_count}，尚未形成该规则可用样本。",
    }


def _empty_lifecycle_rules() -> list[dict[str, Any]]:
    """生命周期没有真实快照样本时的占位。"""
    return [_lifecycle_rule_row(rule, 0) for rule in LIFECYCLE_RULES]


def _lifecycle_rule_row(rule: str, sample_count: int) -> dict[str, Any]:
    """生命周期规则占位行。"""
    return {
        "rule": rule,
        "evidence_type": "真实快照前向样本",
        "sample_count": sample_count,
        "avg_score": None,
        "avg_opportunity": None,
        "avg_risk": None,
        "avg_return_pct": None,
        "win_rate_pct": None,
        "max_drawdown_pct": None,
        "status": "样本不足",
        "note": "需要连续运行保存快照后再统计。",
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """检查 SQLite 表是否存在。"""
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return bool(row)


def _single_int(conn: sqlite3.Connection, sql: str) -> int:
    """执行单值 COUNT 查询。"""
    try:
        return int(conn.execute(sql).fetchone()[0] or 0)
    except Exception:
        return 0
