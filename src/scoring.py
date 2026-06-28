"""市场、板块和个股评分公式。"""

from __future__ import annotations

import pandas as pd

from config import OPERATING_SYSTEM_RULES
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


def enrich_operating_scores(
    df: pd.DataFrame,
    history_stats: dict[str, dict] | None = None,
    rules: dict | None = None,
) -> pd.DataFrame:
    """为主线增加机会分、风险分、信心分、Action 和评分解释。"""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    r = {**OPERATING_SYSTEM_RULES, **(rules or {})}
    stats = history_stats or {}
    out = df.copy()
    rows = []
    for _, row in out.iterrows():
        board_code = str(row.get("board_code", ""))
        item_stats = stats.get(board_code, {})
        opportunity = score_opportunity(row)
        risk = score_risk(row)
        confidence = score_confidence(row, item_stats=item_stats, rules=r)
        action, action_reason = determine_action(row, opportunity, risk, rules=r)
        explanation = explain_sector_score(row, item_stats=item_stats)
        rows.append(
            {
                "opportunity_score": opportunity,
                "risk_score": risk,
                "confidence_score": confidence,
                "action": action,
                "action_reason": action_reason,
                "score_explanation": explanation,
                "history_days": int(item_stats.get("history_days", 0)),
                "stage_days": int(item_stats.get("stage_days", 1)),
                "top10_days": int(item_stats.get("top10_days", 0)),
            }
        )
    return pd.concat([out, pd.DataFrame(rows, index=out.index)], axis=1)


def score_opportunity(row: pd.Series | dict) -> float:
    """计算主线机会分，代表是否值得重点研究。"""
    score = 0.0
    score += safe_float(row.get("score")) * 0.28
    score += safe_float(row.get("trend_strength_score")) * 0.18
    score += safe_float(row.get("money_effect_score")) * 0.16
    score += safe_float(row.get("turnover_activity_score")) * 0.13
    score += _lifecycle_opportunity_bonus(str(row.get("lifecycle_state", "")))
    score += clamp((20 - abs(safe_float(row.get("distance_ma20_pct")))) / 20 * 10)
    score += clamp(safe_float(row.get("up_ratio")) * 10)
    return round(clamp(score), 1)


def score_risk(row: pd.Series | dict) -> float:
    """计算主线风险分，越高越不适合追。"""
    risk = 0.0
    risk += safe_float(row.get("overheat_risk_score")) * 0.25
    risk += clamp((safe_float(row.get("distance_ma20_pct")) - 10) / 30 * 28)
    risk += clamp((safe_float(row.get("ret_10d")) - 8) / 22 * 18)
    risk += clamp((safe_float(row.get("amount_ratio_20")) - 1.6) / 2 * 12)
    risk += clamp((0.45 - safe_float(row.get("up_ratio"))) / 0.45 * 14)
    risk += safe_float(row.get("high_open_low_close_count")) * 5
    risk += safe_float(row.get("volume_stall_count")) * 5
    lifecycle = str(row.get("lifecycle_state", ""))
    if lifecycle == "高潮期":
        risk += 18
    elif lifecycle == "分歧期":
        risk += 12
    elif lifecycle == "退潮期":
        risk += 30
    elif lifecycle == "主升期":
        risk -= 6
    return round(clamp(risk), 1)


def score_confidence(row: pd.Series | dict, item_stats: dict | None = None, rules: dict | None = None) -> float:
    """计算信心指数：判断可靠度，不等于强度。"""
    r = {**OPERATING_SYSTEM_RULES, **(rules or {})}
    stats = item_stats or {}
    history_days = safe_float(stats.get("history_days"))
    top10_days = safe_float(stats.get("top10_days"))
    consecutive_days = safe_float(stats.get("consecutive_top10_days") or stats.get("stage_days"))
    confidence = 35.0
    confidence += clamp(history_days / r["full_confidence_days"] * 22)
    confidence += clamp(consecutive_days / r["history_confidence_days"] * 16)
    confidence += clamp(top10_days / r["history_confidence_days"] * 12)
    confidence += _indicator_consistency(row) * 15
    missing_penalty = _missing_penalty(row)
    single_day_penalty = 10 if safe_float(row.get("ret_3d")) <= 0 and safe_float(row.get("change_pct")) > 3 else 0
    confidence -= missing_penalty + single_day_penalty
    return round(clamp(confidence), 1)


def determine_action(row: pd.Series | dict, opportunity: float, risk: float, rules: dict | None = None) -> tuple[str, str]:
    """根据机会分、风险分和生命周期输出今日 Action。"""
    r = {**OPERATING_SYSTEM_RULES, **(rules or {})}
    lifecycle = str(row.get("lifecycle_state", ""))
    if lifecycle == "退潮期" or risk >= r["avoid_risk_min"]:
        return "回避", "退潮或风险分过高，先防止亏钱效应扩散。"
    if lifecycle in {"高潮期", "分歧期"} or risk >= r["wait_pullback_risk_min"]:
        if opportunity >= r["focus_opportunity_min"]:
            return "等回调", "主线仍强，但短期偏离或分歧较高，不适合追高。"
        return "只观察", "强度不足或结构分歧，等待重新转强。"
    if opportunity >= r["focus_opportunity_min"] and risk <= r["focus_risk_max"]:
        return "重点研究", "机会分高且风险可控，适合研究回调或确认机会。"
    return "只观察", "机会和风险不够匹配，等待更清晰信号。"


def explain_sector_score(row: pd.Series | dict, item_stats: dict | None = None) -> list[str]:
    """生成主线评分解释条目。"""
    stats = item_stats or {}
    return [
        f"近 10 日上榜 {int(stats.get('top10_days', 0))} 天",
        f"成交额较 20 日均值放大 {safe_float(row.get('amount_ratio_20')):.2f} 倍",
        f"板块内 {safe_float(row.get('up_ratio')) * 100:.1f}% 股票上涨",
        f"龙头集中度分 {safe_float(row.get('leader_concentration_score')):.1f}",
        f"距 MA20 偏离 {safe_float(row.get('distance_ma20_pct')):.1f}%",
        f"生命周期：{row.get('lifecycle_state', '未知')}第 {int(stats.get('stage_days', 1))} 天",
    ]


def _lifecycle_opportunity_bonus(lifecycle: str) -> float:
    """生命周期对机会分的贡献。"""
    return {
        "启动期": 12,
        "主升期": 16,
        "修复期": 6,
        "分歧期": 2,
        "高潮期": -4,
        "退潮期": -18,
    }.get(lifecycle, 0)


def _indicator_consistency(row: pd.Series | dict) -> float:
    """多个方向一致时提高信心。"""
    checks = [
        bool(row.get("above_ma5")),
        bool(row.get("above_ma10")),
        bool(row.get("above_ma20")),
        bool(row.get("ma_bull")),
        safe_float(row.get("ret_5d")) > 0,
        safe_float(row.get("ret_10d")) > 0,
        safe_float(row.get("amount_ratio_20")) >= 1,
        safe_float(row.get("up_ratio")) >= 0.5,
    ]
    return sum(checks) / len(checks)


def _missing_penalty(row: pd.Series | dict) -> float:
    """关键字段缺失会降低信心。"""
    fields = ["ret_5d", "ret_10d", "amount_ratio_20", "up_ratio", "distance_ma20_pct", "lifecycle_state"]
    missing = 0
    for field in fields:
        value = row.get(field)
        if value is None or value == "":
            missing += 1
    return missing * 6


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
