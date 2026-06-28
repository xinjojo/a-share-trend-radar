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


def score_backtest_sector_signal(row: pd.Series | dict) -> float:
    """历史回测中对单个板块做当日信号评分，不能依赖未来横截面。"""
    score = 0.0
    score += clamp((safe_float(row.get("ret_3d")) + 3) / 10 * 15)
    score += clamp((safe_float(row.get("ret_5d")) + 5) / 16 * 18)
    score += clamp((safe_float(row.get("ret_10d")) + 8) / 25 * 17)
    score += clamp((safe_float(row.get("ret_20d")) + 10) / 40 * 10)
    score += clamp(safe_float(row.get("amount_ratio_20")) / 2.2 * 20)
    score += 6 if row.get("above_ma5") else 0
    score += 6 if row.get("above_ma10") else 0
    score += 8 if row.get("above_ma20") else 0
    score += 6 if row.get("ma_bull") else 0
    score -= clamp((safe_float(row.get("distance_ma20_pct")) - 25) / 15 * 16)
    return round(clamp(score), 1)


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
    sample_count = int(market_df.get("sample_count", pd.Series([total])).iloc[0]) if "sample_count" in market_df else total
    expected_count = (
        int(market_df.get("sample_expected_count", pd.Series([total])).iloc[0])
        if "sample_expected_count" in market_df
        else total
    )
    is_full_market_sample = (
        bool(market_df.get("is_full_market_sample", pd.Series([True])).iloc[0])
        if "is_full_market_sample" in market_df
        else True
    )
    sample_note = (
        str(market_df.get("sample_note", pd.Series(["全市场样本"])).iloc[0])
        if "sample_note" in market_df
        else "全市场样本"
    )
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
        f"本次参与统计 {sample_count} 只股票（{sample_note}）；"
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
            "sample_count": sample_count,
            "sample_expected_count": expected_count,
            "is_full_market_sample": is_full_market_sample,
            "sample_note": sample_note,
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

    ret_rank_3 = _rank_score(out.get("ret_3d", pd.Series(index=out.index)), True)
    ret_rank_5 = _rank_score(out.get("ret_5d", pd.Series(index=out.index)), True)
    ret_rank_10 = _rank_score(out.get("ret_10d", pd.Series(index=out.index)), True)
    amount_rank_3 = _rank_score(out.get("amount_3d", pd.Series(index=out.index)), True)
    amount_rank_5 = _rank_score(out.get("amount_5d", pd.Series(index=out.index)), True)
    amount_rank_10 = _rank_score(out.get("amount_10d", pd.Series(index=out.index)), True)

    rank_matrix = pd.concat([ret_rank_3, ret_rank_5, ret_rank_10, amount_rank_3, amount_rank_5, amount_rank_10], axis=1)
    out["rank_stability_score"] = (
        rank_matrix.mean(axis=1) - rank_matrix.std(axis=1).fillna(0) * 0.35
    ).map(lambda x: clamp(safe_float(x)))

    out["flow_score"] = (
        _rank_score(out.get("activity_3d", pd.Series(index=out.index)), True) * 0.34
        + _rank_score(out.get("activity_5d", pd.Series(index=out.index)), True) * 0.33
        + _rank_score(out.get("activity_10d", pd.Series(index=out.index)), True) * 0.33
    )
    if "flow_score_label" not in out.columns:
        out["flow_score_label"] = "成交活跃度代理评分"
    if "flow_score_type" not in out.columns:
        out["flow_score_type"] = "proxy"

    out["amount_expansion_score"] = out.get("amount_ratio_20", pd.Series(0, index=out.index)).map(
        lambda x: clamp(safe_float(x) / 2.5 * 100)
    )
    out["turnover_activity_score"] = (
        _rank_score(out.get("amount_3d", pd.Series(index=out.index)), True) * 0.25
        + _rank_score(out.get("amount_5d", pd.Series(index=out.index)), True) * 0.25
        + _rank_score(out.get("amount_10d", pd.Series(index=out.index)), True) * 0.25
        + out["amount_expansion_score"] * 0.25
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
        out["rank_stability_score"] * 0.22
        + out["flow_score"] * 0.18
        + out["trend_strength_score"] * 0.20
        + out["money_effect_score"] * 0.15
        + out["leader_concentration_score"] * 0.10
        + out["amount_expansion_score"] * 0.10
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
    status_bonus = out.get("observe_status", pd.Series("", index=out.index)).map(
        {
            "缩量回踩 5 日线": 16,
            "缩量回踩 10 日线": 15,
            "缩量回踩 20 日线": 14,
            "放量反包": 10,
            "等待回调": -12,
            "不适合追": -20,
            "高位过热": -35,
            "趋势破坏": -45,
        }
    ).fillna(0)
    distance = out.get("distance_ma20_pct", pd.Series(0, index=out.index)).map(safe_float)
    trend_status = out.get("trend_status", pd.Series("", index=out.index)).astype(str)
    pool_group = out.get("pool_group", pd.Series("", index=out.index)).astype(str)
    distance_bonus = ((distance >= 0) & (distance <= 15) & (trend_status == "多头趋势")).astype(int) * 14
    distance_penalty = (distance > 25).astype(int) * 18 + (distance > 35).astype(int) * 22
    group_penalty = (pool_group == "高位观察/不适合追").astype(int) * 8
    out["research_priority_score"] = (
        out["leader_score"] + status_bonus + distance_bonus - distance_penalty - group_penalty
    ).map(lambda x: round(clamp(safe_float(x)), 1))
    return out.sort_values("leader_score", ascending=False).reset_index(drop=True)
