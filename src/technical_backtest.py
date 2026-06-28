"""个股技术信号历史回测。

V3 先验证个股层规则，不把系统生成的主线 Action 当成历史真值。
信号只使用当日及以前 K 线，收益统计从信号日之后的交易日计算。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from config import HISTORY_DB_PATH
from src.data_provider import AStockDataProvider
from src.history_db import init_history_db
from src.indicators import add_moving_averages
from src.utils import normalize_code, safe_float


DEFAULT_FORWARD_DAYS = (1, 3, 5, 10, 20)


@dataclass
class TechnicalBacktestParams:
    """个股技术信号回测参数。"""

    start_date: str
    end_date: str
    forward_days: tuple[int, ...] = DEFAULT_FORWARD_DAYS
    history_limit: int = 900
    max_codes: int = 30
    volume_pullback_ma5: float = 1.2
    volume_pullback_ma10: float = 1.1
    volume_pullback_ma20: float = 1.0
    reversal_volume_ratio: float = 1.5
    hot_distance_ma20: float = 25.0
    hot_ret20: float = 35.0


def run_technical_signal_backtest(
    provider: AStockDataProvider,
    codes: list[str],
    params: TechnicalBacktestParams,
    name_map: dict[str, str] | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """运行个股技术信号回测。"""
    code_list = _normalize_codes(codes)[: max(int(params.max_codes), 1)]
    warnings = [
        "本回测只验证个股技术信号，不验证主线 Action 或生命周期。",
        "信号按当日收盘后可见数据计算，收益从之后 1/3/5/10/20 个交易日统计。",
        "个股历史 K 线优先 mootdx；失败后才使用备用数据源，价格口径以数据源返回为准。",
    ]
    if not code_list:
        return _empty_result(params, warnings + ["未输入有效股票代码。"])

    events: list[dict[str, Any]] = []
    history_status: list[dict[str, Any]] = []
    name_map = name_map or {}
    for code in code_list:
        hist = provider.get_stock_history(code, limit=params.history_limit)
        if hist is None or hist.empty:
            history_status.append({"code": code, "name": name_map.get(code, ""), "status": "无历史K线"})
            continue
        enriched = _prepare_history(hist, params.start_date, params.end_date)
        if len(enriched) < 10:
            history_status.append({"code": code, "name": name_map.get(code, ""), "status": f"K线不足：{len(enriched)}"})
            continue
        history_status.append(
            {
                "code": code,
                "name": name_map.get(code, ""),
                "status": "可用",
                "rows": len(enriched),
                "start": str(enriched["date"].iloc[0]),
                "end": str(enriched["date"].iloc[-1]),
                "data_source": str(enriched.get("data_source", pd.Series([""])).iloc[-1]),
            }
        )
        events.extend(_events_for_stock(code, name_map.get(code, ""), enriched, params))

    events_df = pd.DataFrame(events)
    summary_df = summarize_signal_events(events_df)
    run_id = str(uuid.uuid4())
    result = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "params": asdict(params),
        "codes": code_list,
        "events": events_df,
        "summary": summary_df,
        "history_status": pd.DataFrame(history_status),
        "warnings": warnings,
    }
    if save:
        save_technical_backtest_result(result)
    return result


def summarize_signal_events(events_df: pd.DataFrame) -> pd.DataFrame:
    """汇总不同信号、不同持有天数后的收益分布。"""
    if events_df is None or events_df.empty:
        return pd.DataFrame(
            columns=[
                "signal",
                "horizon",
                "occurrences",
                "win_rate_pct",
                "avg_return_pct",
                "median_return_pct",
                "p25_return_pct",
                "p75_return_pct",
                "best_return_pct",
                "worst_return_pct",
                "avg_max_drawdown_pct",
                "worst_max_drawdown_pct",
            ]
        )
    rows = []
    for (signal, horizon), group in events_df.groupby(["signal", "horizon"], sort=True):
        returns = pd.to_numeric(group["forward_return_pct"], errors="coerce").dropna()
        drawdowns = pd.to_numeric(group["max_drawdown_pct"], errors="coerce").dropna()
        if returns.empty:
            continue
        rows.append(
            {
                "signal": signal,
                "horizon": int(horizon),
                "occurrences": int(len(returns)),
                "win_rate_pct": round(float((returns > 0).mean() * 100), 2),
                "avg_return_pct": round(float(returns.mean()), 2),
                "median_return_pct": round(float(returns.median()), 2),
                "p25_return_pct": round(float(returns.quantile(0.25)), 2),
                "p75_return_pct": round(float(returns.quantile(0.75)), 2),
                "best_return_pct": round(float(returns.max()), 2),
                "worst_return_pct": round(float(returns.min()), 2),
                "avg_max_drawdown_pct": round(float(drawdowns.mean()), 2) if not drawdowns.empty else 0.0,
                "worst_max_drawdown_pct": round(float(drawdowns.min()), 2) if not drawdowns.empty else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values(["signal", "horizon"]).reset_index(drop=True)


def generate_technical_backtest_report(result: dict[str, Any]) -> str:
    """生成可导出的 Markdown 回测报告。"""
    params = result.get("params", {})
    summary = result.get("summary")
    events = result.get("events")
    history_status = result.get("history_status")
    lines = [
        "# 个股技术信号历史回测报告",
        "",
        f"生成时间：{result.get('created_at', '')}",
        f"回测区间：{params.get('start_date', '')} 至 {params.get('end_date', '')}",
        f"股票数量：{len(result.get('codes', []))}",
        "",
        "> 本报告只用于验证技术信号后的历史收益分布，不构成投资建议。",
        "",
        "## 数据与限制",
        "",
    ]
    lines.extend(f"- {item}" for item in result.get("warnings", []))
    lines.extend(
        [
            "",
            "## 样本概况",
            "",
            f"- 有效事件数：{len(events) if isinstance(events, pd.DataFrame) else 0}",
            f"- 信号汇总行数：{len(summary) if isinstance(summary, pd.DataFrame) else 0}",
        ]
    )
    if isinstance(history_status, pd.DataFrame) and not history_status.empty:
        bad = history_status[history_status["status"].astype(str) != "可用"]
        if not bad.empty:
            lines.append("- 数据不足代码：" + "、".join(bad["code"].astype(str).head(20).tolist()))
    lines.extend(["", "## 信号收益摘要", ""])
    if not isinstance(summary, pd.DataFrame) or summary.empty:
        lines.append("- 暂无有效信号事件。")
    else:
        top = summary.sort_values(["horizon", "avg_return_pct"], ascending=[True, False]).head(20)
        for _, row in top.iterrows():
            lines.append(
                f"- {row.get('signal')} / {int(row.get('horizon'))}日：样本 {int(row.get('occurrences'))}，"
                f"胜率 {safe_float(row.get('win_rate_pct')):.1f}%，平均收益 {safe_float(row.get('avg_return_pct')):.2f}%，"
                f"中位数 {safe_float(row.get('median_return_pct')):.2f}%，最差回撤 {safe_float(row.get('worst_max_drawdown_pct')):.2f}%。"
            )
    return "\n".join(lines)


def save_technical_backtest_result(result: dict[str, Any]) -> None:
    """把个股技术信号回测结果保存到 V3 历史库。"""
    init_history_db()
    _init_technical_tables()
    run_id = str(result.get("run_id", ""))
    events = result.get("events")
    events_df = events if isinstance(events, pd.DataFrame) else pd.DataFrame()
    summary = result.get("summary")
    summary_df = summary if isinstance(summary, pd.DataFrame) else pd.DataFrame()
    with sqlite3.connect(HISTORY_DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO technical_backtest_runs(
                run_id, created_at, start_date, end_date, codes_json,
                params_json, warnings_json, event_count, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                result.get("created_at", datetime.now().isoformat(timespec="seconds")),
                result.get("params", {}).get("start_date", ""),
                result.get("params", {}).get("end_date", ""),
                json.dumps(result.get("codes", []), ensure_ascii=False),
                json.dumps(result.get("params", {}), ensure_ascii=False, default=str),
                json.dumps(result.get("warnings", []), ensure_ascii=False),
                len(events_df),
                summary_df.to_json(orient="records", force_ascii=False) if not summary_df.empty else "[]",
            ),
        )
        conn.execute("DELETE FROM technical_signal_events WHERE run_id = ?", (run_id,))
        if not events_df.empty:
            to_save = events_df.copy()
            to_save["run_id"] = run_id
            to_save.to_sql("technical_signal_events", conn, if_exists="append", index=False)


def _events_for_stock(
    code: str,
    name: str,
    hist: pd.DataFrame,
    params: TechnicalBacktestParams,
) -> list[dict[str, Any]]:
    """生成单只股票的信号事件明细。"""
    events: list[dict[str, Any]] = []
    for index in range(1, len(hist)):
        row = hist.iloc[index]
        prev = hist.iloc[index - 1] if index > 0 else row
        signals = _signals_for_row(row, prev, params)
        if not signals:
            continue
        entry_close = safe_float(row.get("close"))
        if entry_close <= 0:
            continue
        for signal in signals:
            for horizon in params.forward_days:
                target_index = index + int(horizon)
                if target_index >= len(hist):
                    continue
                future = hist.iloc[index + 1 : target_index + 1]
                if future.empty:
                    continue
                target = hist.iloc[target_index]
                target_close = safe_float(target.get("close"))
                future_low = pd.to_numeric(future["low"], errors="coerce").min() if "low" in future.columns else entry_close
                max_drawdown = min(0.0, safe_float(future_low) / entry_close * 100 - 100) if entry_close > 0 else 0.0
                events.append(
                    {
                        "code": code,
                        "name": name,
                        "signal": signal,
                        "signal_date": str(row.get("date", "")),
                        "horizon": int(horizon),
                        "target_date": str(target.get("date", "")),
                        "entry_close": round(entry_close, 4),
                        "target_close": round(target_close, 4),
                        "forward_return_pct": round(target_close / entry_close * 100 - 100, 4) if target_close > 0 else 0.0,
                        "max_drawdown_pct": round(max_drawdown, 4),
                        "close": round(entry_close, 4),
                        "ma5": round(safe_float(row.get("ma5")), 4),
                        "ma10": round(safe_float(row.get("ma10")), 4),
                        "ma20": round(safe_float(row.get("ma20")), 4),
                        "ma60": round(safe_float(row.get("ma60")), 4),
                        "distance_ma20_pct": round(_distance_ma20(row), 4),
                        "volume_ratio_20": round(safe_float(row.get("volume_ratio_20") or row.get("amount_ratio_20")), 4),
                        "ret_20d": round(safe_float(row.get("ret_20d")), 4),
                        "data_source": str(row.get("data_source", "")),
                        "price_basis": str(row.get("price_basis", "不复权")),
                    }
                )
    return events


def _signals_for_row(row: pd.Series, prev: pd.Series, params: TechnicalBacktestParams) -> list[str]:
    """用当日及以前数据判断技术信号。"""
    close = safe_float(row.get("close"))
    low = safe_float(row.get("low"))
    ma5 = safe_float(row.get("ma5"))
    ma10 = safe_float(row.get("ma10"))
    ma20 = safe_float(row.get("ma20"))
    ma60 = safe_float(row.get("ma60"))
    ret20 = safe_float(row.get("ret_20d"))
    volume_ratio = safe_float(row.get("volume_ratio_20") or row.get("amount_ratio_20"), 1.0)
    distance = _distance_ma20(row)
    signals = []
    ma_bull = ma5 > ma10 > ma20 > ma60 > 0 and close > ma20
    if ma_bull:
        signals.append("MA多头排列")
    if ma_bull and 0 <= distance <= 15:
        signals.append("距MA20 0-15%多头")
    elif ma_bull and 15 < distance <= params.hot_distance_ma20:
        signals.append("距MA20 15-25%偏热")
    elif distance > params.hot_distance_ma20:
        signals.append("距MA20 >25%")
    if ma5 > 0 and low <= ma5 <= close and volume_ratio <= params.volume_pullback_ma5 and close >= ma20:
        signals.append("缩量回踩MA5")
    if ma10 > 0 and low <= ma10 <= close and volume_ratio <= params.volume_pullback_ma10 and close >= ma20:
        signals.append("缩量回踩MA10")
    if ma20 > 0 and low <= ma20 <= close and volume_ratio <= params.volume_pullback_ma20:
        signals.append("缩量回踩MA20")
    if close > safe_float(prev.get("high")) and volume_ratio >= params.reversal_volume_ratio:
        signals.append("放量反包")
    if safe_float(prev.get("close")) >= safe_float(prev.get("ma20")) > 0 and ma20 > 0 and close < ma20:
        signals.append("跌破MA20")
    if distance > params.hot_distance_ma20 or (ret20 >= params.hot_ret20 and ma5 > 0 and close > ma5 * 1.08):
        signals.append("高位过热")
    return signals


def _prepare_history(hist: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """标准化并截取回测区间需要的历史 K 线。"""
    out = add_moving_averages(hist)
    if out.empty or "date" not in out.columns:
        return pd.DataFrame()
    out = out.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    # 均线计算需要回看窗口，因此先计算后截取正式统计区间。
    mask = (out["date"] >= pd.to_datetime(start_date)) & (out["date"] <= pd.to_datetime(end_date))
    out = out[mask].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def _normalize_codes(codes: list[str]) -> list[str]:
    """去重并标准化股票代码。"""
    out = []
    seen = set()
    for item in codes:
        code = normalize_code(str(item))
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _distance_ma20(row: pd.Series) -> float:
    """计算距 MA20 百分比。"""
    close = safe_float(row.get("close"))
    ma20 = safe_float(row.get("ma20"))
    return close / ma20 * 100 - 100 if close > 0 and ma20 > 0 else 0.0


def _empty_result(params: TechnicalBacktestParams, warnings: list[str]) -> dict[str, Any]:
    """返回空回测结果。"""
    return {
        "run_id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "params": asdict(params),
        "codes": [],
        "events": pd.DataFrame(),
        "summary": summarize_signal_events(pd.DataFrame()),
        "history_status": pd.DataFrame(),
        "warnings": warnings,
    }


def _init_technical_tables() -> None:
    """初始化个股信号回测结果表。"""
    with sqlite3.connect(HISTORY_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS technical_backtest_runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                codes_json TEXT NOT NULL,
                params_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                event_count INTEGER NOT NULL,
                summary_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS technical_signal_events (
                run_id TEXT NOT NULL,
                code TEXT,
                name TEXT,
                signal TEXT,
                signal_date TEXT,
                horizon INTEGER,
                target_date TEXT,
                entry_close REAL,
                target_close REAL,
                forward_return_pct REAL,
                max_drawdown_pct REAL,
                close REAL,
                ma5 REAL,
                ma10 REAL,
                ma20 REAL,
                ma60 REAL,
                distance_ma20_pct REAL,
                volume_ratio_20 REAL,
                ret_20d REAL,
                data_source TEXT,
                price_basis TEXT
            )
            """
        )
