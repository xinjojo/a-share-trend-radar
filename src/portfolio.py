"""组合净值和交易统计。"""

from __future__ import annotations

import pandas as pd

from src.utils import safe_float


def build_drawdown_curve(equity_curve: pd.DataFrame) -> pd.DataFrame:
    """根据净值曲线计算回撤曲线。"""
    if equity_curve is None or equity_curve.empty or "equity" not in equity_curve.columns:
        return pd.DataFrame()
    out = equity_curve[["date", "equity"]].copy()
    out["peak"] = pd.to_numeric(out["equity"], errors="coerce").cummax()
    out["drawdown_pct"] = out["equity"] / out["peak"].replace(0, pd.NA) * 100 - 100
    return out.fillna(0)


def calculate_metrics(equity_curve: pd.DataFrame, trades: pd.DataFrame, initial_cash: float) -> dict:
    """计算回测核心指标。"""
    if equity_curve is None or equity_curve.empty:
        return {
            "总收益率": 0.0,
            "年化收益率": 0.0,
            "最大回撤": 0.0,
            "胜率": 0.0,
            "盈亏比": 0.0,
            "平均持仓天数": 0.0,
            "交易次数": 0,
            "最大连续亏损次数": 0,
        }
    curve = equity_curve.copy()
    curve["date"] = pd.to_datetime(curve["date"], errors="coerce")
    start_value = initial_cash
    end_value = safe_float(curve["equity"].iloc[-1])
    total_return = end_value / start_value * 100 - 100 if start_value > 0 else 0.0
    days = max((curve["date"].iloc[-1] - curve["date"].iloc[0]).days, 1)
    annual_return = ((1 + total_return / 100) ** (365 / days) - 1) * 100 if days > 0 else 0.0
    drawdown = build_drawdown_curve(curve)
    max_drawdown = safe_float(drawdown["drawdown_pct"].min()) if not drawdown.empty else 0.0

    trades = trades if trades is not None else pd.DataFrame()
    closed = trades[trades.get("status", pd.Series(dtype=str)) == "closed"].copy() if not trades.empty else pd.DataFrame()
    trade_count = len(closed)
    if closed.empty:
        win_rate = 0.0
        profit_factor = 0.0
        avg_holding_days = 0.0
        max_loss_streak = 0
    else:
        pnl_pct = pd.to_numeric(closed["pnl_pct"], errors="coerce").fillna(0)
        wins = pnl_pct[pnl_pct > 0]
        losses = pnl_pct[pnl_pct < 0]
        win_rate = len(wins) / max(trade_count, 1) * 100
        profit_factor = wins.sum() / abs(losses.sum()) if abs(losses.sum()) > 0 else 0.0
        avg_holding_days = safe_float(pd.to_numeric(closed["holding_days"], errors="coerce").fillna(0).mean())
        max_loss_streak = _max_consecutive_losses(pnl_pct.tolist())

    return {
        "总收益率": float(round(total_return, 2)),
        "年化收益率": float(round(annual_return, 2)),
        "最大回撤": float(round(max_drawdown, 2)),
        "胜率": float(round(win_rate, 2)),
        "盈亏比": float(round(profit_factor, 2)),
        "平均持仓天数": float(round(avg_holding_days, 2)),
        "交易次数": int(trade_count),
        "最大连续亏损次数": int(max_loss_streak),
    }


def annual_returns(equity_curve: pd.DataFrame) -> pd.DataFrame:
    """计算年度收益表。"""
    if equity_curve is None or equity_curve.empty:
        return pd.DataFrame()
    out = equity_curve.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["year"] = out["date"].dt.year
    rows = []
    for year, group in out.groupby("year"):
        start = safe_float(group["equity"].iloc[0])
        end = safe_float(group["equity"].iloc[-1])
        rows.append({"年份": int(year), "收益率%": round(end / start * 100 - 100, 2) if start > 0 else 0.0})
    return pd.DataFrame(rows)


def _max_consecutive_losses(pnl_pct: list[float]) -> int:
    """最大连续亏损次数。"""
    best = 0
    current = 0
    for pnl in pnl_pct:
        if pnl < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best
