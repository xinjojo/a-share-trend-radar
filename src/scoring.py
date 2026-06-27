"""市场、板块和个股评分公式。"""

from __future__ import annotations

import pandas as pd

from src.utils import clamp, safe_float


def _rank_score(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """把指标按横截面排名映射到 0-100。"""
    if series.empty:
        return pd.Series(dtype=float)
    numeric = pd.to_numeric(series, errors="coerce").fillna(0)
    if numeric.nunique() <= 1:
        return pd.Series([50.0] * len(numeric), index=series.index)
    pct = numeric.rank(pct=True, ascending=higher_is_better)
    return pct * 100


def score_market_temperature(market_df: pd.DataFrame, index_df: pd.DataFrame) -> dict:
    """计算市场温度 0-100 分。"""
    if market_df is None or market_df.empty:
        return {
            "score": 0.0,
            "risk_preference": "冷",
            "explanation": "全市场行情暂不可用，无法计算市场温度。",
            "metrics": {},
        }

    total = len(market_df)
    up_count = int((market_df["change_pct"] > 0).sum())
    down_count = int((market_df["change_pct"] < 0).sum())
    flat_count = total - up_count - down_count
    limit_up = int(market_df.get("is_limit_up", pd.Series(False, index=market_df.index)).sum())
    limit_down = int(market_df.get("is_limit_down", pd.Series(False, index=market_df.index)).sum())
    total_amount_yi = float(pd.to_numeric(market_df.get("amount_yi", 0), errors="coerce").fillna(0).sum())
    strong_count = int((market_df["change_pct"] >= 5).sum())
    big_amount_count = int((market_df.get("amount_yi", 0) >= 20).sum())

    breadth_score = up_count / max(up_count + down_count, 1) * 100
    limit_score = clamp(50 + (limit_up - limit_down) * 2.0)
    amount_score = clamp((total_amount_yi - 6000) / 6000 * 100)
    strong_score = clamp(strong_count / max(total * 0.06, 1) * 100)
    big_amount_score = clamp(big_amount_count / max(total * 0.04, 1) * 100)

    index_change = 0.0
    if index_df is not None and not index_df.empty and "change_pct" in index_df.columns:
        index_change = float(pd.to_numeric(index_df["change_pct"], errors="coerce").fillna(0).mean())
    index_score = clamp((index_change + 3) / 6 * 100)

    score = (
        breadth_score * 0.25
        + limit_score * 0.15
        + amount_score * 0.20
        + index_score * 0.20
        + strong_score * 0.12
        + big_amount_score * 0.08
    )
    score = round(clamp(score), 1)

    if score < 25:
        risk = "冷"
    elif score < 45:
        risk = "弱"
    elif score < 60:
        risk = "中性"
    elif score < 78:
        risk = "热"
    else:
        risk = "过热"

    explanation = (
        f"上涨 {up_count} 家、下跌 {down_count} 家、平盘 {flat_count} 家；"
        f"涨停约 {limit_up} 家、跌停约 {limit_down} 家；"
        f"全市场成交额约 {total_amount_yi:,.0f} 亿元，主要指数平均涨跌幅 {index_change:.2f}%。"
    )
    return {
        "score": score,
        "risk_preference": risk,
        "explanation": explanation,
        "metrics": {
            "total": total,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total_amount_yi": total_amount_yi,
            "strong_count": strong_count,
            "big_amount_count": big_amount_count,
            "index_change": index_change,
        },
    }


def score_sector_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """按统一公式计算板块综合分和分类。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()

    # 资金持续性：优先使用真实资金流；若没有，sector_radar 会提供 signed_amount 代理。
    out["fund_continuity_score"] = (
        _rank_score(out.get("fund_3d", pd.Series(index=out.index)), True) * 0.34
        + _rank_score(out.get("fund_5d", pd.Series(index=out.index)), True) * 0.33
        + _rank_score(out.get("fund_10d", pd.Series(index=out.index)), True) * 0.33
    )
    out["turnover_activity_score"] = (
        _rank_score(out.get("amount_3d", pd.Series(index=out.index)), True) * 0.25
        + _rank_score(out.get("amount_5d", pd.Series(index=out.index)), True) * 0.25
        + _rank_score(out.get("amount_10d", pd.Series(index=out.index)), True) * 0.25
        + out.get("amount_ratio_20", pd.Series(0, index=out.index)).map(lambda x: clamp(safe_float(x) / 2.5 * 100)) * 0.25
    )

    trend_base = (
        out.get("above_ma5", pd.Series(False, index=out.index)).astype(int) * 15
        + out.get("above_ma10", pd.Series(False, index=out.index)).astype(int) * 15
        + out.get("above_ma20", pd.Series(False, index=out.index)).astype(int) * 20
        + out.get("above_ma60", pd.Series(False, index=out.index)).astype(int) * 20
        + out.get("ma_bull", pd.Series(False, index=out.index)).astype(int) * 20
        + out.get("ret_10d", pd.Series(0, index=out.index)).map(lambda x: clamp((safe_float(x) + 5) / 20 * 10, 0, 10))
    )
    out["trend_strength_score"] = trend_base.map(lambda x: clamp(safe_float(x)))

    out["money_effect_score"] = (
        out.get("up_ratio", pd.Series(0, index=out.index)).map(lambda x: clamp(safe_float(x) * 100)) * 0.7
        + out.get("limit_up_count", pd.Series(0, index=out.index)).map(lambda x: clamp(safe_float(x) / 5 * 100)) * 0.3
    )
    out["leader_concentration_score"] = out.get(
        "leader_amount_share", pd.Series(0, index=out.index)
    ).map(lambda x: clamp(safe_float(x) * 220))
    out["overheat_risk_score"] = (
        out.get("ret_10d", pd.Series(0, index=out.index)).map(lambda x: clamp((safe_float(x) - 12) / 20 * 100))
        * 0.6
        + out.get("amount_ratio_20", pd.Series(0, index=out.index)).map(lambda x: clamp((safe_float(x) - 2.2) / 2.5 * 100))
        * 0.4
    )

    out["score"] = (
        out["fund_continuity_score"] * 0.30
        + out["turnover_activity_score"] * 0.20
        + out["trend_strength_score"] * 0.20
        + out["money_effect_score"] * 0.15
        + out["leader_concentration_score"] * 0.10
        - out["overheat_risk_score"] * 0.05
    ).map(lambda x: round(clamp(safe_float(x)), 1))

    out["category"] = out.apply(_classify_sector, axis=1)
    out = out.sort_values(["score", "change_pct"], ascending=[False, False]).reset_index(drop=True)
    out["rank"] = range(1, len(out) + 1)
    return out


def _classify_sector(row: pd.Series) -> str:
    """输出持续主线、短线热点、退潮板块。"""
    score = safe_float(row.get("score"))
    ret_5d = safe_float(row.get("ret_5d"))
    ret_10d = safe_float(row.get("ret_10d"))
    above_ma20 = bool(row.get("above_ma20"))
    amount_ratio = safe_float(row.get("amount_ratio_20"))
    up_ratio = safe_float(row.get("up_ratio"))

    if score >= 68 and above_ma20 and ret_5d > 0 and ret_10d > 0 and up_ratio >= 0.5:
        return "持续主线"
    if score >= 58 and safe_float(row.get("change_pct")) > 1.5 and amount_ratio >= 1.3:
        return "短线热点"
    if (not above_ma20) or ret_5d < -3 or up_ratio < 0.35:
        return "退潮板块"
    return "观察中"


def score_leader_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """龙头股票池打分。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["liquidity_score"] = _rank_score(out.get("amount_yi", pd.Series(index=out.index)), True)
    out["trend_score"] = out.get("trend_score", pd.Series(0, index=out.index)).map(lambda x: clamp(safe_float(x)))
    out["strength_score"] = (
        _rank_score(out.get("change_pct", pd.Series(index=out.index)), True) * 0.35
        + _rank_score(out.get("ret_20d", pd.Series(index=out.index)), True) * 0.35
        + _rank_score(out.get("ret_60d", pd.Series(index=out.index)), True) * 0.30
    )
    out["mcap_score"] = out.get("mcap_yi", pd.Series(0, index=out.index)).map(
        lambda x: clamp((safe_float(x) - 30) / 170 * 100)
    )
    out["leader_score"] = (
        out["liquidity_score"] * 0.30
        + out["strength_score"] * 0.30
        + out["trend_score"] * 0.25
        + out["mcap_score"] * 0.15
    ).map(lambda x: round(clamp(safe_float(x)), 1))
    return out.sort_values("leader_score", ascending=False).reset_index(drop=True)
