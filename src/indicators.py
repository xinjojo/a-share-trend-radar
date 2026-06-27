"""技术指标与状态识别。"""

from __future__ import annotations

import pandas as pd

from src.utils import clamp, safe_float


def add_moving_averages(
    df: pd.DataFrame,
    price_col: str = "close",
    volume_col: str = "volume",
    windows: tuple[int, ...] = (5, 10, 20, 60),
) -> pd.DataFrame:
    """添加价格均线、成交额/成交量均线和阶段涨幅。"""
    if df is None or df.empty or price_col not in df.columns:
        return pd.DataFrame() if df is None else df.copy()
    out = df.copy()
    if "date" in out.columns:
        out = out.sort_values("date")
    out[price_col] = pd.to_numeric(out[price_col], errors="coerce").fillna(0)
    for window in windows:
        out[f"ma{window}"] = out[price_col].rolling(window, min_periods=max(2, window // 2)).mean()
        out[f"ret_{window}d"] = out[price_col].pct_change(window) * 100
    if volume_col in out.columns:
        out[volume_col] = pd.to_numeric(out[volume_col], errors="coerce").fillna(0)
        out["volume_ma20"] = out[volume_col].rolling(20, min_periods=5).mean()
        out["volume_ratio_20"] = out[volume_col] / out["volume_ma20"].replace(0, pd.NA)
    if "amount" in out.columns:
        out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0)
        out["amount_ma20"] = out["amount"].rolling(20, min_periods=5).mean()
        out["amount_ratio_20"] = out["amount"] / out["amount_ma20"].replace(0, pd.NA)
    elif "amount_yi" in out.columns:
        out["amount_yi"] = pd.to_numeric(out["amount_yi"], errors="coerce").fillna(0)
        out["amount_ma20_yi"] = out["amount_yi"].rolling(20, min_periods=5).mean()
        out["amount_ratio_20"] = out["amount_yi"] / out["amount_ma20_yi"].replace(0, pd.NA)
    return out.fillna(0)


def latest_trend_flags(df: pd.DataFrame) -> dict:
    """提取最新一根 K线的趋势布尔状态。"""
    if df is None or df.empty:
        return {
            "above_ma5": False,
            "above_ma10": False,
            "above_ma20": False,
            "above_ma60": False,
            "ma_bull": False,
            "amount_ratio_20": 0.0,
            "ret_20d": 0.0,
            "ret_60d": 0.0,
            "date": "",
            "price_basis": "",
            "ma_basis": "",
            "adjustment": "",
        }
    enriched = add_moving_averages(df)
    last = enriched.iloc[-1]
    close = safe_float(last.get("close"))
    ma5 = safe_float(last.get("ma5"))
    ma10 = safe_float(last.get("ma10"))
    ma20 = safe_float(last.get("ma20"))
    ma60 = safe_float(last.get("ma60"))
    return {
        "above_ma5": close > ma5 > 0,
        "above_ma10": close > ma10 > 0,
        "above_ma20": close > ma20 > 0,
        "above_ma60": close > ma60 > 0,
        "ma_bull": ma5 > ma10 > ma20 > ma60 > 0,
        "amount_ratio_20": safe_float(last.get("amount_ratio_20") or last.get("volume_ratio_20")),
        "ret_20d": safe_float(last.get("ret_20d")),
        "ret_60d": safe_float(last.get("ret_60d")),
        "close": close,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "date": str(last.get("date", "")),
        "price_basis": str(last.get("price_basis", "")),
        "ma_basis": str(last.get("ma_basis", last.get("price_basis", ""))),
        "adjustment": str(last.get("adjustment", "")),
    }


def classify_trend(df: pd.DataFrame) -> str:
    """判断个股/板块趋势状态。"""
    flags = latest_trend_flags(df)
    if not flags["above_ma20"]:
        return "趋势破坏"
    if flags["ma_bull"] and flags["above_ma5"]:
        return "多头趋势"
    if flags["above_ma10"] and flags["above_ma20"]:
        return "上升趋势"
    return "震荡偏强"


def observe_buy_point(df: pd.DataFrame) -> str:
    """输出观察状态，不输出买入建议。"""
    if df is None or df.empty or len(df) < 10:
        return "数据不足"
    enriched = add_moving_averages(df)
    last = enriched.iloc[-1]
    prev = enriched.iloc[-2] if len(enriched) >= 2 else last
    close = safe_float(last.get("close"))
    low = safe_float(last.get("low"))
    ma5 = safe_float(last.get("ma5"))
    ma10 = safe_float(last.get("ma10"))
    ma20 = safe_float(last.get("ma20"))
    amount_ratio = safe_float(last.get("amount_ratio_20") or last.get("volume_ratio_20"), 1.0)
    ret_20d = safe_float(last.get("ret_20d"))
    distance_ma20_pct = (close / ma20 - 1) * 100 if ma20 > 0 and close > 0 else 0.0

    if ma20 > 0 and close < ma20:
        return "趋势破坏"
    if distance_ma20_pct > 25:
        return "高位过热"
    if ret_20d >= 35 and close > ma5 * 1.08:
        return "高位过热"
    if ma5 > 0 and low <= ma5 <= close and amount_ratio <= 1.2:
        return "缩量回踩 5 日线"
    if ma10 > 0 and low <= ma10 <= close and amount_ratio <= 1.1:
        return "缩量回踩 10 日线"
    if ma20 > 0 and low <= ma20 <= close and amount_ratio <= 1.0:
        return "缩量回踩 20 日线"
    if close > safe_float(prev.get("high")) and amount_ratio >= 1.5:
        return "放量反包"
    if ma5 > 0 and close > ma5 * 1.06:
        return "等待回调"
    if ret_20d >= 20 and amount_ratio >= 1.8:
        return "不适合追"
    return "等待回调"


def invalid_condition(df: pd.DataFrame) -> str:
    """生成研究观察的失效条件。"""
    flags = latest_trend_flags(df)
    close = safe_float(flags.get("close"))
    ma20 = safe_float(flags.get("ma20"))
    ma60 = safe_float(flags.get("ma60"))
    if ma20 > 0:
        return f"收盘有效跌破 20 日线（约 {ma20:.2f}）且无法快速收回"
    if ma60 > 0:
        return f"收盘有效跌破 60 日线（约 {ma60:.2f}）"
    if close > 0:
        return f"收盘跌破当前价下方 8%-10% 区间（约 {close * 0.92:.2f}）"
    return "数据不足，暂无法定义"


def trend_score_from_flags(flags: dict) -> float:
    """把均线状态映射成 0-100 趋势分。"""
    score = 0.0
    score += 15 if flags.get("above_ma5") else 0
    score += 20 if flags.get("above_ma10") else 0
    score += 25 if flags.get("above_ma20") else 0
    score += 20 if flags.get("above_ma60") else 0
    score += 20 if flags.get("ma_bull") else 0
    return clamp(score)
