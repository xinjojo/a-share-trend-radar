"""主线轮动策略回测引擎。

MVP 版本使用当前雷达候选池的历史 K 线做无未来函数重放。它能验证
入场/出场规则本身，但仍存在当前候选池幸存者偏差；页面和 README 会明确提示。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from config import BACKTEST_DEFAULTS
from src.benchmark import load_benchmark_curves, market_temperature_proxy
from src.data_provider import AStockDataProvider
from src.database import get_connection, init_db
from src.indicators import add_moving_averages
from src.lifecycle import classify_lifecycle
from src.portfolio import annual_returns, build_drawdown_curve, calculate_metrics
from src.scoring import score_backtest_sector_signal
from src.utils import safe_float, safe_int


@dataclass
class BacktestParams:
    """回测参数。"""

    initial_cash: float = BACKTEST_DEFAULTS["initial_cash"]
    market_temperature_threshold: float = BACKTEST_DEFAULTS["market_temperature_threshold"]
    top_sectors: int = BACKTEST_DEFAULTS["top_sectors"]
    stocks_per_sector: int = BACKTEST_DEFAULTS["stocks_per_sector"]
    max_positions: int = BACKTEST_DEFAULTS["max_positions"]
    ma20_distance_limit: float = BACKTEST_DEFAULTS["ma20_distance_limit"]
    stop_loss_pct: float = BACKTEST_DEFAULTS["stop_loss_pct"]
    take_profit_pct: float = BACKTEST_DEFAULTS["take_profit_pct"]
    trailing_stop_pct: float = BACKTEST_DEFAULTS["trailing_stop_pct"]
    enable_trailing_stop: bool = BACKTEST_DEFAULTS["enable_trailing_stop"]
    max_holding_days: int = BACKTEST_DEFAULTS["max_holding_days"]
    exit_if_not_profitable_after_days: int = BACKTEST_DEFAULTS["exit_if_not_profitable_after_days"]
    execution_price: str = BACKTEST_DEFAULTS["execution_price"]


def run_rotation_backtest(
    provider: AStockDataProvider,
    sector_df: pd.DataFrame,
    leader_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    params: BacktestParams | dict | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """运行主线轮动策略回测。"""
    p = _normalize_params(params)
    warnings = [
        "MVP 回测使用当前雷达候选板块和股票池向历史回放，存在候选池幸存者偏差。",
        "历史市场温度暂用沪深300是否站上 MA20 代理，不等同于真实历史市场温度。",
    ]
    if sector_df is None or sector_df.empty or leader_df is None or leader_df.empty:
        return _empty_result(p, start_date, end_date, warnings + ["板块或股票池数据不足，无法回测。"])

    sector_universe = sector_df.sort_values("score", ascending=False).head(max(10, p.top_sectors * 4)).copy()
    board_histories = _load_board_histories(provider, sector_universe)
    stock_histories = _load_stock_histories(provider, leader_df)
    benchmark_hist = add_moving_averages(provider.get_index_history("sh000300", limit=900))
    dates = _backtest_dates(stock_histories, benchmark_hist, start_date, end_date)
    if len(dates) < 5:
        return _empty_result(p, start_date, end_date, warnings + ["可用交易日不足，无法形成有效回测。"])

    cash = float(p.initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    pending_buys: list[dict[str, Any]] = []
    pending_sells: list[dict[str, Any]] = []
    closed_trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []

    for index, date in enumerate(dates):
        is_last_date = index == len(dates) - 1
        cash, positions, closed_today = _execute_sells(pending_sells, positions, stock_histories, date, cash, p)
        closed_trades.extend(closed_today)
        pending_sells = []

        cash, positions = _execute_buys(pending_buys, positions, stock_histories, date, cash, p)
        pending_buys = []

        equity = _portfolio_value(cash, positions, stock_histories, date)
        equity_rows.append({"date": date, "equity": equity, "cash": cash, "positions": len(positions)})

        sector_signals = _sector_signals_for_date(sector_universe, board_histories, date)
        market_score = market_temperature_proxy(benchmark_hist, date)
        candidate_orders = (
            _candidate_orders_for_date(leader_df, stock_histories, sector_signals, date, p)
            if market_score >= p.market_temperature_threshold
            else []
        )
        candidate_codes = {order["code"] for order in candidate_orders}

        if not is_last_date:
            pending_sells = _sell_orders_for_date(positions, stock_histories, sector_signals, candidate_codes, date, p)
            pending_buys = _buy_orders_for_date(candidate_orders, positions, pending_sells, p)

    open_trades = _open_trade_records(positions, stock_histories, dates[-1])
    trades_df = pd.DataFrame(closed_trades + open_trades)
    equity_curve = pd.DataFrame(equity_rows)
    drawdown_curve = build_drawdown_curve(equity_curve)
    metrics = calculate_metrics(equity_curve, trades_df, p.initial_cash)
    annual_df = annual_returns(equity_curve)
    benchmark_df = load_benchmark_curves(provider, start_date, end_date, initial_value=p.initial_cash)
    result = {
        "run_id": str(uuid.uuid4()),
        "params": asdict(p),
        "start_date": start_date,
        "end_date": end_date,
        "metrics": metrics,
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "trades": trades_df,
        "annual_returns": annual_df,
        "benchmarks": benchmark_df,
        "warnings": warnings,
    }
    if save:
        save_backtest_result(result)
    return result


def save_backtest_result(result: dict[str, Any]) -> None:
    """保存回测运行和交易明细。"""
    _init_backtest_tables()
    run_id = result.get("run_id") or str(uuid.uuid4())
    trades = result.get("trades")
    trades_df = trades if isinstance(trades, pd.DataFrame) else pd.DataFrame()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO backtest_runs(
                run_id, created_at, start_date, end_date, params_json, metrics_json, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now().isoformat(timespec="seconds"),
                result.get("start_date", ""),
                result.get("end_date", ""),
                json.dumps(result.get("params", {}), ensure_ascii=False),
                json.dumps(result.get("metrics", {}), ensure_ascii=False),
                json.dumps(result.get("warnings", []), ensure_ascii=False),
            ),
        )
        conn.execute("DELETE FROM backtest_trades WHERE run_id = ?", (run_id,))
        if not trades_df.empty:
            to_save = trades_df.copy()
            to_save["run_id"] = run_id
            to_save.to_sql("backtest_trades", conn, if_exists="append", index=False)


def _normalize_params(params: BacktestParams | dict | None) -> BacktestParams:
    """合并默认参数和页面传入参数。"""
    if isinstance(params, BacktestParams):
        return params
    payload = dict(BACKTEST_DEFAULTS)
    if params:
        payload.update(params)
    return BacktestParams(**payload)


def _empty_result(params: BacktestParams, start_date: str, end_date: str, warnings: list[str]) -> dict[str, Any]:
    """空回测结果。"""
    return {
        "run_id": str(uuid.uuid4()),
        "params": asdict(params),
        "start_date": start_date,
        "end_date": end_date,
        "metrics": calculate_metrics(pd.DataFrame(), pd.DataFrame(), params.initial_cash),
        "equity_curve": pd.DataFrame(),
        "drawdown_curve": pd.DataFrame(),
        "trades": pd.DataFrame(),
        "annual_returns": pd.DataFrame(),
        "benchmarks": pd.DataFrame(),
        "warnings": warnings,
    }


def _load_board_histories(provider: AStockDataProvider, sector_universe: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """加载板块历史 K 线。"""
    histories = {}
    for _, row in sector_universe.iterrows():
        code = str(row.get("board_code", ""))
        if not code:
            continue
        hist = provider.get_board_history(code, board_name=str(row.get("board_name", "")), limit=900)
        enriched = add_moving_averages(hist) if hist is not None and not hist.empty else pd.DataFrame()
        if not enriched.empty:
            histories[code] = enriched
    return histories


def _load_stock_histories(provider: AStockDataProvider, leader_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """加载股票历史 K 线。"""
    histories = {}
    for code in leader_df["code"].dropna().astype(str).unique().tolist():
        hist = provider.get_stock_history(code, limit=900)
        enriched = add_moving_averages(hist) if hist is not None and not hist.empty else pd.DataFrame()
        if not enriched.empty:
            histories[code] = enriched
    return histories


def _backtest_dates(
    stock_histories: dict[str, pd.DataFrame],
    benchmark_hist: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> list[str]:
    """生成回测交易日序列。"""
    date_set = set()
    if benchmark_hist is not None and not benchmark_hist.empty and "date" in benchmark_hist.columns:
        date_set.update(benchmark_hist["date"].astype(str).tolist())
    for hist in stock_histories.values():
        if "date" in hist.columns:
            date_set.update(hist["date"].astype(str).tolist())
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    return [
        date
        for date in sorted(date_set)
        if start <= pd.to_datetime(date, errors="coerce") <= end
    ]


def _sector_signals_for_date(
    sector_universe: pd.DataFrame,
    board_histories: dict[str, pd.DataFrame],
    date: str,
) -> pd.DataFrame:
    """计算某日可见的板块历史信号。"""
    rows = []
    for _, meta in sector_universe.iterrows():
        code = str(meta.get("board_code", ""))
        hist = _history_until(board_histories.get(code), date)
        if hist.empty:
            continue
        signal = _sector_signal_row(meta, hist)
        rows.append(signal)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out["rank"] = range(1, len(out) + 1)
    return out


def _sector_signal_row(meta: pd.Series, hist: pd.DataFrame) -> dict[str, Any]:
    """根据截至当日的板块历史计算单日信号。"""
    last = hist.iloc[-1]
    close = safe_float(last.get("close"))
    ma20 = safe_float(last.get("ma20"))
    row = {
        "board_code": str(meta.get("board_code", "")),
        "board_name": str(meta.get("board_name", "")),
        "board_layer": str(meta.get("board_layer", "")),
        "ret_3d": _period_return(hist, 3),
        "ret_5d": _period_return(hist, 5),
        "ret_10d": _period_return(hist, 10),
        "ret_20d": _period_return(hist, 20),
        "amount_ratio_20": safe_float(last.get("amount_ratio_20")),
        "above_ma5": close > safe_float(last.get("ma5")) > 0,
        "above_ma10": close > safe_float(last.get("ma10")) > 0,
        "above_ma20": close > ma20 > 0,
        "above_ma60": close > safe_float(last.get("ma60")) > 0,
        "ma_bull": safe_float(last.get("ma5")) > safe_float(last.get("ma10")) > ma20 > 0,
        "distance_ma20_pct": (close / ma20 - 1) * 100 if close > 0 and ma20 > 0 else 0,
        "up_ratio": safe_float(meta.get("up_ratio"), 0.5),
        "limit_up_count": safe_float(meta.get("limit_up_count")),
        "leader_change": safe_float(meta.get("leader_change")),
        "high_open_low_close_count": _recent_high_open_low_close(hist),
        "volume_stall_count": _recent_volume_stall(hist),
    }
    row["score"] = score_backtest_sector_signal(row)
    lifecycle = classify_lifecycle(row)
    row.update(lifecycle)
    return row


def _candidate_orders_for_date(
    leader_df: pd.DataFrame,
    stock_histories: dict[str, pd.DataFrame],
    sector_signals: pd.DataFrame,
    date: str,
    params: BacktestParams,
) -> list[dict[str, Any]]:
    """根据当日收盘信号生成下一交易日候选买单。"""
    if sector_signals is None or sector_signals.empty:
        return []
    top_sectors = sector_signals[
        ~sector_signals["lifecycle_state"].isin(["退潮期"])
    ].head(params.top_sectors)
    orders = []
    for _, sector in top_sectors.iterrows():
        candidates = leader_df[leader_df.apply(lambda r: _leader_matches_board(r, sector), axis=1)].copy()
        if "pool_group" in candidates.columns:
            candidates = candidates[candidates["pool_group"] == "可研究候选"]
        if candidates.empty:
            continue
        valid_rows = []
        for _, stock in candidates.iterrows():
            code = str(stock.get("code", ""))
            hist = _history_until(stock_histories.get(code), date)
            ok, reason = _stock_signal_ok(hist, params)
            if ok:
                payload = stock.to_dict()
                payload.update(
                    {
                        "signal_date": date,
                        "board_code": sector.get("board_code", ""),
                        "board_name": sector.get("board_name", ""),
                        "sector_score_at_signal": safe_float(sector.get("score")),
                        "sector_lifecycle_at_signal": sector.get("lifecycle_state", ""),
                        "signal_reason": reason,
                    }
                )
                valid_rows.append(payload)
        if valid_rows:
            valid = pd.DataFrame(valid_rows).sort_values(["leader_score", "amount_yi"], ascending=False)
            orders.extend(valid.head(params.stocks_per_sector).to_dict("records"))
    return sorted(orders, key=lambda row: (safe_float(row.get("sector_score_at_signal")), safe_float(row.get("leader_score"))), reverse=True)


def _sell_orders_for_date(
    positions: dict[str, dict[str, Any]],
    stock_histories: dict[str, pd.DataFrame],
    sector_signals: pd.DataFrame,
    candidate_codes: set[str],
    date: str,
    params: BacktestParams,
) -> list[dict[str, Any]]:
    """根据当日收盘信号生成下一交易日卖单。"""
    orders = []
    sector_state_by_code = {
        str(row.get("board_code", "")): str(row.get("lifecycle_state", ""))
        for _, row in sector_signals.iterrows()
    } if sector_signals is not None and not sector_signals.empty else {}
    for code, position in list(positions.items()):
        hist = _history_until(stock_histories.get(code), date)
        if hist.empty:
            continue
        last = hist.iloc[-1]
        close = safe_float(last.get("close"))
        ma20 = safe_float(last.get("ma20"))
        entry_price = safe_float(position.get("entry_price"))
        pnl_pct = close / entry_price * 100 - 100 if entry_price > 0 else 0.0
        position["peak_price"] = max(safe_float(position.get("peak_price")), close)
        trailing_drawdown = close / safe_float(position.get("peak_price")) * 100 - 100 if safe_float(position.get("peak_price")) > 0 else 0.0
        position["holding_days"] = safe_int(position.get("holding_days")) + 1
        board_code = str(position.get("board_code", ""))
        reason = ""
        if ma20 > 0 and close < ma20:
            reason = "跌破 MA20"
        elif sector_state_by_code.get(board_code) == "退潮期":
            reason = "板块生命周期进入退潮期"
        elif code not in candidate_codes:
            reason = "个股从龙头池信号跌出"
        elif position["holding_days"] >= params.exit_if_not_profitable_after_days and pnl_pct <= 0:
            reason = "持仓超时仍未盈利"
        elif position["holding_days"] >= params.max_holding_days:
            reason = "达到最大持仓天数"
        elif pnl_pct <= params.stop_loss_pct:
            reason = "触发止损"
        elif params.enable_trailing_stop and trailing_drawdown <= -abs(params.trailing_stop_pct):
            reason = "触发移动止盈"
        elif pnl_pct >= params.take_profit_pct:
            reason = "触发止盈"
        if reason:
            orders.append({"code": code, "signal_date": date, "reason": reason})
    return orders


def _buy_orders_for_date(
    candidate_orders: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    pending_sells: list[dict[str, Any]],
    params: BacktestParams,
) -> list[dict[str, Any]]:
    """扣除已持仓和待卖出后生成买单。"""
    pending_sell_codes = {order["code"] for order in pending_sells}
    orders = []
    for order in candidate_orders:
        code = str(order.get("code", ""))
        if not code or code in positions or code in pending_sell_codes:
            continue
        if len(positions) + len(orders) >= params.max_positions:
            break
        orders.append(order)
    return orders


def _execute_sells(
    orders: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    stock_histories: dict[str, pd.DataFrame],
    date: str,
    cash: float,
    params: BacktestParams,
) -> tuple[float, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """在当日收盘执行昨日卖出信号。"""
    closed = []
    for order in orders:
        code = str(order.get("code", ""))
        if code not in positions:
            continue
        price = _execution_price(stock_histories.get(code), date, params.execution_price)
        if price <= 0:
            continue
        position = positions.pop(code)
        shares = safe_float(position.get("shares"))
        proceeds = shares * price
        cash += proceeds
        entry_price = safe_float(position.get("entry_price"))
        pnl = (price - entry_price) * shares
        pnl_pct = price / entry_price * 100 - 100 if entry_price > 0 else 0.0
        closed.append(
            {
                **position,
                "exit_signal_date": order.get("signal_date", ""),
                "exit_date": date,
                "exit_price": price,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": order.get("reason", ""),
                "status": "closed",
            }
        )
    return cash, positions, closed


def _execute_buys(
    orders: list[dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    stock_histories: dict[str, pd.DataFrame],
    date: str,
    cash: float,
    params: BacktestParams,
) -> tuple[float, dict[str, dict[str, Any]]]:
    """在当日收盘执行昨日买入信号。"""
    available_slots = max(params.max_positions - len(positions), 0)
    executable = orders[:available_slots]
    remaining = len(executable)
    for order in executable:
        code = str(order.get("code", ""))
        price = _execution_price(stock_histories.get(code), date, params.execution_price)
        if price <= 0 or cash <= 0 or code in positions:
            continue
        allocation = cash / max(remaining, 1)
        shares = allocation / price
        cash -= allocation
        positions[code] = {
            "trade_id": str(uuid.uuid4()),
            "code": code,
            "name": order.get("name", ""),
            "board_code": order.get("board_code", ""),
            "board_name": order.get("board_name", ""),
            "entry_signal_date": order.get("signal_date", ""),
            "entry_date": date,
            "entry_price": price,
            "shares": shares,
            "entry_value": allocation,
            "peak_price": price,
            "holding_days": 0,
            "sector_score_at_signal": order.get("sector_score_at_signal", 0),
            "sector_lifecycle_at_signal": order.get("sector_lifecycle_at_signal", ""),
            "leader_score": order.get("leader_score", 0),
            "signal_reason": order.get("signal_reason", ""),
        }
        remaining -= 1
    return cash, positions


def _portfolio_value(
    cash: float,
    positions: dict[str, dict[str, Any]],
    stock_histories: dict[str, pd.DataFrame],
    date: str,
) -> float:
    """计算当日组合权益。"""
    total = cash
    for code, position in positions.items():
        price = _execution_price(stock_histories.get(code), date, "close")
        total += safe_float(position.get("shares")) * price
    return round(total, 2)


def _open_trade_records(
    positions: dict[str, dict[str, Any]],
    stock_histories: dict[str, pd.DataFrame],
    final_date: str,
) -> list[dict[str, Any]]:
    """把期末未平仓持仓记录为 open。"""
    records = []
    for code, position in positions.items():
        price = _execution_price(stock_histories.get(code), final_date, "close")
        entry_price = safe_float(position.get("entry_price"))
        pnl_pct = price / entry_price * 100 - 100 if entry_price > 0 else 0.0
        records.append(
            {
                **position,
                "exit_signal_date": "",
                "exit_date": "",
                "exit_price": price,
                "pnl": round((price - entry_price) * safe_float(position.get("shares")), 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": "期末未平仓",
                "status": "open",
            }
        )
    return records


def _stock_signal_ok(hist: pd.DataFrame, params: BacktestParams) -> tuple[bool, str]:
    """判断股票是否满足回测入场过滤。"""
    if hist is None or hist.empty or len(hist) < 60:
        return False, "历史数据不足"
    last = hist.iloc[-1]
    close = safe_float(last.get("close"))
    high = safe_float(last.get("high"))
    low = safe_float(last.get("low"))
    ma5 = safe_float(last.get("ma5"))
    ma10 = safe_float(last.get("ma10"))
    ma20 = safe_float(last.get("ma20"))
    ma60 = safe_float(last.get("ma60"))
    ret20 = safe_float(last.get("ret_20d"))
    distance = close / ma20 * 100 - 100 if close > 0 and ma20 > 0 else 999
    amplitude = (high - low) / close * 100 if close > 0 else 0
    daily_ret = _period_return(hist, 1)
    if not (ma5 > ma10 > ma20 > ma60 > 0):
        return False, "均线未多头排列"
    if not (close > ma20 > 0):
        return False, "未站上 MA20"
    if distance > params.ma20_distance_limit:
        return False, "距 MA20 过远"
    if distance > 25 or ret20 >= 35:
        return False, "高位过热"
    if daily_ret >= 9.5 and amplitude < 1.2:
        return False, "疑似连续一字板"
    return True, f"多头排列，距MA20 {distance:.1f}%"


def _leader_matches_board(stock: pd.Series, sector: pd.Series) -> bool:
    """判断去重后的股票行是否属于某个板块。"""
    board_code = str(sector.get("board_code", ""))
    board_name = str(sector.get("board_name", ""))
    stock_board_code = str(stock.get("board_code", ""))
    stock_board_name = str(stock.get("board_name", ""))
    return board_code in stock_board_code.split(" / ") or board_name in stock_board_name.split(" / ")


def _history_until(hist: pd.DataFrame | None, date: str) -> pd.DataFrame:
    """截取截至指定日期的历史数据。"""
    if hist is None or hist.empty or "date" not in hist.columns:
        return pd.DataFrame()
    out = hist.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out[out["date"] <= pd.to_datetime(date)].sort_values("date")
    if out.empty:
        return out
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def _execution_price(hist: pd.DataFrame | None, date: str, price_col: str) -> float:
    """取某日成交价格。"""
    if hist is None or hist.empty or "date" not in hist.columns:
        return 0.0
    rows = hist[hist["date"].astype(str) == str(date)]
    if rows.empty:
        return 0.0
    row = rows.iloc[-1]
    col = price_col if price_col in row.index else "close"
    return safe_float(row.get(col))


def _period_return(hist: pd.DataFrame, window: int) -> float:
    """计算截至当前日的阶段涨幅。"""
    if hist is None or hist.empty or "close" not in hist.columns or len(hist) <= window:
        return 0.0
    close = pd.to_numeric(hist["close"], errors="coerce").fillna(0)
    base = safe_float(close.iloc[-window - 1])
    latest = safe_float(close.iloc[-1])
    return latest / base * 100 - 100 if base > 0 else 0.0


def _recent_high_open_low_close(hist: pd.DataFrame, window: int = 5) -> int:
    """近期高开低走次数。"""
    if hist is None or hist.empty or not {"open", "close"}.issubset(hist.columns):
        return 0
    out = hist.copy()
    prev_close = pd.to_numeric(out["close"], errors="coerce").shift(1)
    mask = (pd.to_numeric(out["open"], errors="coerce") > prev_close * 1.005) & (
        pd.to_numeric(out["close"], errors="coerce") < pd.to_numeric(out["open"], errors="coerce")
    )
    return int(mask.tail(window).sum())


def _recent_volume_stall(hist: pd.DataFrame, window: int = 5) -> int:
    """近期放量滞涨次数。"""
    if hist is None or hist.empty or "amount_ratio_20" not in hist.columns:
        return 0
    amount_ratio = pd.to_numeric(hist["amount_ratio_20"], errors="coerce").fillna(0)
    ret = pd.to_numeric(hist["close"], errors="coerce").pct_change() * 100
    return int(((amount_ratio >= 1.5) & (ret <= 1.0)).tail(window).sum())


def _init_backtest_tables() -> None:
    """初始化回测结果表。"""
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                params_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_trades (
                trade_id TEXT,
                run_id TEXT,
                code TEXT,
                name TEXT,
                board_code TEXT,
                board_name TEXT,
                entry_signal_date TEXT,
                entry_date TEXT,
                entry_price REAL,
                shares REAL,
                entry_value REAL,
                peak_price REAL,
                holding_days INTEGER,
                sector_score_at_signal REAL,
                sector_lifecycle_at_signal TEXT,
                leader_score REAL,
                signal_reason TEXT,
                exit_signal_date TEXT,
                exit_date TEXT,
                exit_price REAL,
                pnl REAL,
                pnl_pct REAL,
                exit_reason TEXT,
                status TEXT
            )
            """
        )
