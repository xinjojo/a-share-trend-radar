"""主线生命周期判断。

生命周期判断只使用当前日期及之前已经形成的板块指标，不读取未来数据。
"""

from __future__ import annotations

import pandas as pd

from config import LIFECYCLE_RULES
from src.utils import clamp, safe_float


LIFECYCLE_ORDER = ["启动期", "主升期", "高潮期", "分歧期", "退潮期", "修复期"]


def attach_lifecycle(df: pd.DataFrame, rules: dict | None = None) -> pd.DataFrame:
    """给板块评分表附加生命周期状态、进度和建议。"""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    out = df.copy()
    lifecycle_rows = [classify_lifecycle(row, rules=rules) for _, row in out.iterrows()]
    lifecycle_df = pd.DataFrame(lifecycle_rows, index=out.index)
    return pd.concat([out, lifecycle_df], axis=1)


def classify_lifecycle(row: pd.Series | dict, rules: dict | None = None) -> dict:
    """判断单个板块的生命周期。"""
    r = {**LIFECYCLE_RULES, **(rules or {})}
    score = safe_float(row.get("score"))
    ret_3d = safe_float(row.get("ret_3d"))
    ret_5d = safe_float(row.get("ret_5d"))
    ret_10d = safe_float(row.get("ret_10d"))
    ret_20d = safe_float(row.get("ret_20d"))
    amount_ratio = safe_float(row.get("amount_ratio_20"))
    up_ratio = safe_float(row.get("up_ratio"))
    limit_up_count = safe_float(row.get("limit_up_count"))
    distance_ma20 = safe_float(row.get("distance_ma20_pct"))
    high_open_low_close_count = safe_float(row.get("high_open_low_close_count"))
    volume_stall_count = safe_float(row.get("volume_stall_count"))
    above_ma5 = bool(row.get("above_ma5"))
    above_ma10 = bool(row.get("above_ma10"))
    above_ma20 = bool(row.get("above_ma20"))
    above_ma60 = bool(row.get("above_ma60"))
    ma_bull = bool(row.get("ma_bull"))
    leader_change = safe_float(row.get("leader_change"))
    category = str(row.get("category", ""))

    if _is_fading(row, r):
        state = "退潮期"
        progress = _progress(70 + abs(min(ret_5d, 0)) * 4 + (0 if above_ma20 else 18))
        recommendation = "回避"
        explanation = "板块跌破或接近跌破关键趋势，短期收益效应转弱。"
    elif _is_climax(row, r):
        state = "高潮期"
        progress = _progress(72 + max(distance_ma20 - r["climax_distance_ma20"], 0) * 1.8 + limit_up_count * 2)
        recommendation = "等回调"
        explanation = "短期涨幅、MA20 偏离或成交额放大已经偏高，追涨风险上升。"
    elif _is_divergence(row, r):
        state = "分歧期"
        progress = _progress(58 + high_open_low_close_count * 8 + volume_stall_count * 8 + max(0.45 - up_ratio, 0) * 80)
        recommendation = "只观察"
        explanation = "板块仍有强度，但内部上涨占比、K线或量价结构出现分化。"
    elif _is_main_rise(row, r):
        state = "主升期"
        progress = _progress(55 + score * 0.35 + max(ret_10d, 0) * 0.8 + max(up_ratio - 0.5, 0) * 60)
        recommendation = "可研究"
        explanation = "趋势结构较完整，成交额温和放大，板块内赚钱效应较好。"
    elif _is_startup(row, r):
        state = "启动期"
        progress = _progress(35 + score * 0.35 + max(ret_5d, 0) * 1.2 + max(amount_ratio - 1, 0) * 14)
        recommendation = "可研究"
        explanation = "板块刚转强或进入前排，距离 MA20 不远，仍处于验证阶段。"
    elif _is_repair(row, r):
        state = "修复期"
        progress = _progress(38 + score * 0.25 + max(ret_5d, 0) * 1.2)
        recommendation = "只观察"
        explanation = "退潮后重新修复均线，但尚未形成稳定主升结构。"
    else:
        state = "分歧期" if above_ma20 else "退潮期"
        progress = 45 if above_ma20 else 65
        recommendation = "只观察" if above_ma20 else "回避"
        explanation = "当前指标不满足启动或主升条件，等待更清晰的量价确认。"

    signs = _signs(
        ret_3d=ret_3d,
        ret_5d=ret_5d,
        ret_10d=ret_10d,
        ret_20d=ret_20d,
        amount_ratio=amount_ratio,
        up_ratio=up_ratio,
        distance_ma20=distance_ma20,
        ma_bull=ma_bull,
        above_ma60=above_ma60,
        leader_change=leader_change,
        category=category,
    )
    return {
        "lifecycle_state": state,
        "lifecycle_progress": round(progress, 1),
        "lifecycle_explanation": explanation + signs,
        "lifecycle_recommendation": recommendation,
    }


def _is_startup(row: pd.Series | dict, r: dict) -> bool:
    """启动期：刚转强，量能开始放大，距离 MA20 不远。"""
    return (
        safe_float(row.get("score")) >= r["startup_score_min"]
        and bool(row.get("above_ma20"))
        and safe_float(row.get("ret_3d")) > 0
        and safe_float(row.get("ret_5d")) > 0
        and safe_float(row.get("amount_ratio_20")) >= 1.0
        and 0 <= safe_float(row.get("distance_ma20_pct")) <= r["distance_near_ma20"]
    )


def _is_main_rise(row: pd.Series | dict, r: dict) -> bool:
    """主升期：均线多头，趋势和赚钱效应同时较好。"""
    return (
        safe_float(row.get("score")) >= r["main_rise_score_min"]
        and bool(row.get("above_ma5"))
        and bool(row.get("above_ma10"))
        and bool(row.get("above_ma20"))
        and bool(row.get("ma_bull"))
        and safe_float(row.get("amount_ratio_20")) >= 1.15
        and safe_float(row.get("up_ratio")) >= 0.5
        and safe_float(row.get("ret_10d")) > 0
        and safe_float(row.get("distance_ma20_pct")) <= r["climax_distance_ma20"]
    )


def _is_climax(row: pd.Series | dict, r: dict) -> bool:
    """高潮期：短期加速和过热风险同时出现。"""
    return (
        safe_float(row.get("distance_ma20_pct")) >= r["climax_distance_ma20"]
        or safe_float(row.get("ret_10d")) >= r["climax_ret_10d"]
        or safe_float(row.get("ret_20d")) >= r["climax_ret_20d"]
        or safe_float(row.get("amount_ratio_20")) >= r["climax_amount_ratio"]
        or safe_float(row.get("limit_up_count")) >= r["climax_limit_up_count"]
    ) and bool(row.get("above_ma20"))


def _is_divergence(row: pd.Series | dict, r: dict) -> bool:
    """分歧期：板块表面仍强，但内部或量价开始分化。"""
    still_strong = bool(row.get("above_ma20")) or safe_float(row.get("score")) >= r["startup_score_min"]
    return still_strong and (
        safe_float(row.get("up_ratio")) < r["divergence_up_ratio"]
        or safe_float(row.get("high_open_low_close_count")) >= r["divergence_high_open_low_close_count"]
        or safe_float(row.get("volume_stall_count")) >= r["divergence_volume_stall_count"]
        or (safe_float(row.get("ret_3d")) <= 0 and safe_float(row.get("amount_ratio_20")) >= 1.5)
    )


def _is_fading(row: pd.Series | dict, r: dict) -> bool:
    """退潮期：跌破趋势或短期收益效应明显走弱。"""
    return (
        not bool(row.get("above_ma20"))
        or safe_float(row.get("ret_5d")) <= r["fading_ret_5d"]
        or safe_float(row.get("up_ratio")) < 0.35
    )


def _is_repair(row: pd.Series | dict, r: dict) -> bool:
    """修复期：退潮后重新站回 MA10/MA20，但主升结构不足。"""
    return (
        safe_float(row.get("score")) >= r["repair_score_min"]
        and bool(row.get("above_ma10"))
        and bool(row.get("above_ma20"))
        and safe_float(row.get("ret_5d")) >= 0
        and not bool(row.get("ma_bull"))
    )


def _progress(value: float) -> float:
    """生命周期进度条限制在 0-100。"""
    return clamp(value, 5, 100)


def _signs(**kwargs: float | bool | str) -> str:
    """把关键指标拼成简短解释。"""
    return (
        f" 关键指标：3/5/10/20日涨幅 "
        f"{safe_float(kwargs.get('ret_3d')):.1f}%/"
        f"{safe_float(kwargs.get('ret_5d')):.1f}%/"
        f"{safe_float(kwargs.get('ret_10d')):.1f}%/"
        f"{safe_float(kwargs.get('ret_20d')):.1f}%，"
        f"量能倍数 {safe_float(kwargs.get('amount_ratio')):.2f}，"
        f"上涨占比 {safe_float(kwargs.get('up_ratio')) * 100:.1f}%，"
        f"距MA20 {safe_float(kwargs.get('distance_ma20')):.1f}%，"
        f"多头排列 {'是' if kwargs.get('ma_bull') else '否'}。"
    )
