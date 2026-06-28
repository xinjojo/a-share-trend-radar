"""回测基准指数处理。"""

from __future__ import annotations

import pandas as pd

from src.data_provider import AStockDataProvider
from src.utils import safe_float


BENCHMARK_SYMBOLS = {
    "沪深300": "sh000300",
    "创业板指": "sz399006",
    "中证全指": "sh000985",
}


def load_benchmark_curves(
    provider: AStockDataProvider,
    start_date: str,
    end_date: str,
    initial_value: float = 1.0,
) -> pd.DataFrame:
    """加载并归一化多个基准指数曲线。"""
    frames = []
    for name, symbol in BENCHMARK_SYMBOLS.items():
        hist = provider.get_index_history(symbol, limit=900)
        if hist is None or hist.empty:
            continue
        curve = normalize_benchmark(hist, name, start_date, end_date, initial_value=initial_value)
        if not curve.empty:
            frames.append(curve)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def normalize_benchmark(
    hist: pd.DataFrame,
    name: str,
    start_date: str,
    end_date: str,
    initial_value: float = 1.0,
) -> pd.DataFrame:
    """把指数历史 K 线归一成净值曲线。"""
    if hist is None or hist.empty or "close" not in hist.columns:
        return pd.DataFrame()
    out = hist.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce").fillna(0)
    out = out[(out["date"] >= pd.to_datetime(start_date)) & (out["date"] <= pd.to_datetime(end_date))]
    out = out[out["close"] > 0].sort_values("date")
    if out.empty:
        return pd.DataFrame()
    first_close = safe_float(out["close"].iloc[0])
    out["benchmark"] = name
    out["value"] = out["close"] / first_close * initial_value if first_close > 0 else initial_value
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out[["date", "benchmark", "value", "close"]].reset_index(drop=True)


def market_temperature_proxy(index_hist: pd.DataFrame, date: str) -> float:
    """用沪深300是否站上 MA20 近似历史市场温度，避免使用未来行情。"""
    if index_hist is None or index_hist.empty or "close" not in index_hist.columns:
        return 100.0
    out = index_hist.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce").fillna(0)
    out = out[out["date"] <= pd.to_datetime(date)].sort_values("date")
    if len(out) < 20:
        return 100.0
    close = safe_float(out["close"].iloc[-1])
    ma20 = safe_float(out["close"].tail(20).mean())
    if close >= ma20:
        return 60.0
    return 40.0
