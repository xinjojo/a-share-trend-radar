"""V4 解释层。

本模块只负责把已经计算好的评分、Action 和股票池结果翻译成可审计解释。
不在这里重新取数，也不改变底层行情数据。
"""

from __future__ import annotations

import ast
from typing import Any

import pandas as pd

from src.utils import clamp, safe_float


SCORE_COMPONENTS = [
    ("资金持续性", "rank_stability_score", 0.22, "近 3/5/10 日强度和成交排名稳定"),
    ("成交活跃", "flow_score", 0.18, "真实资金流不可用时使用成交活跃度代理"),
    ("趋势强度", "trend_strength_score", 0.20, "MA5/10/20/60 和均线结构"),
    ("赚钱效应", "money_effect_score", 0.15, "板块内上涨占比和涨停数量"),
    ("龙头集中", "leader_concentration_score", 0.10, "龙头成交占比和辨识度"),
    ("量能放大", "amount_expansion_score", 0.10, "成交额相对 20 日均值放大"),
    ("过热扣分", "overheat_risk_score", -0.05, "短期涨幅和量能过热风险"),
]

STOCK_GROUP_ORDER = {
    "可研究候选": 0,
    "强主线回调观察": 1,
    "等待回调": 2,
    "高位观察/不追": 3,
    "回避": 4,
}


def enrich_sector_explainability(sectors: pd.DataFrame) -> pd.DataFrame:
    """为每条主线补充评分拆解、Action 解释和信心解释。"""
    if sectors is None or sectors.empty:
        return pd.DataFrame() if sectors is None else sectors.copy()
    out = sectors.copy()
    rows = []
    for _, row in out.iterrows():
        breakdown = sector_score_breakdown(row)
        score_lines = _as_list(row.get("score_explanation"))
        component_lines = [
            f"{item['label']}贡献 {item['contribution']:+.1f} 分（原始 {item['raw_score']:.1f}，权重 {item['weight_text']}）"
            for item in breakdown
        ]
        rows.append(
            {
                "score_breakdown": breakdown,
                "score_explanation": _dedupe(score_lines + component_lines),
                "action_explanation": action_explanation(row),
                "confidence_explanation": confidence_explanation(row),
                "explainability_ready": True,
            }
        )
    extra = pd.DataFrame(rows, index=out.index)
    for column in extra.columns:
        out[column] = extra[column]
    return out


def enrich_stock_explainability(
    stock_groups: dict[str, pd.DataFrame],
    sectors: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """为最终五栏股票池补充研究优先级、推荐原因和排序解释。"""
    if not stock_groups:
        return stock_groups
    sector_lookup = _sector_lookup(sectors)
    enriched: dict[str, pd.DataFrame] = {}
    group_tops: dict[str, dict[str, Any]] = {}
    prepared: dict[str, pd.DataFrame] = {}
    for group_name, df in stock_groups.items():
        frame = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
        if frame.empty:
            prepared[group_name] = frame
            continue
        rows = []
        for _, row in frame.iterrows():
            item = row.to_dict()
            item["priority_score"] = stock_priority_score(item)
            item["priority"] = stock_priority_label(item["priority_score"])
            item["recommendation_reasons"] = stock_recommendation_reasons(item, sector_lookup)
            item["explainability_ready"] = True
            rows.append(item)
        frame = pd.DataFrame(rows)
        frame = frame.sort_values(
            ["priority_score", "research_priority_score", "leader_score", "amount_yi"],
            ascending=[False, False, False, False],
        ).reset_index(drop=True)
        prepared[group_name] = frame
        if not frame.empty:
            group_tops[group_name] = frame.iloc[0].to_dict()

    for group_name, frame in prepared.items():
        if frame.empty:
            enriched[group_name] = frame
            continue
        top = group_tops.get(group_name, {})
        frame = frame.copy()
        frame["why_not_first"] = [stock_why_not_first(row.to_dict(), top) for _, row in frame.iterrows()]
        enriched[group_name] = frame
    return enriched


def build_today_conclusion(
    market_temperature: dict[str, Any],
    sectors: pd.DataFrame,
    stock_groups: dict[str, pd.DataFrame],
) -> list[str]:
    """生成 4-6 行首页结论。"""
    market = market_state_text(market_temperature)
    strategy = strategy_text(market_temperature)
    focus = _action_sector_names(sectors, "重点研究", stock_groups, limit=3) or "暂无明确重点"
    wait = _action_sector_names(sectors, "等回调", stock_groups, limit=3) or "暂无"
    avoid = _action_sector_names(sectors, "回避", stock_groups, limit=3)
    lines = [
        f"市场：{market}",
        f"策略：{strategy}",
        f"重点关注：{focus}",
        f"等待：{wait}",
    ]
    if avoid:
        lines.append(f"回避：{avoid}")
    return lines[:6]


def build_why_today(
    market_temperature: dict[str, Any],
    sectors: pd.DataFrame,
    changes: dict[str, Any] | None = None,
) -> str:
    """生成首页“为什么今天这样判断”。"""
    metrics = market_temperature.get("metrics", {})
    up_count = safe_float(metrics.get("up_count"))
    down_count = safe_float(metrics.get("down_count"))
    breadth = up_count / max(up_count + down_count, 1)
    top = sectors.head(6) if sectors is not None and not sectors.empty else pd.DataFrame()
    climax = int((top.get("lifecycle_state", pd.Series(dtype=str)).astype(str) == "高潮期").sum()) if not top.empty else 0
    focus = int((top.get("action", pd.Series(dtype=str)).astype(str) == "重点研究").sum()) if not top.empty else 0
    retreat = int((top.get("action", pd.Series(dtype=str)).astype(str) == "回避").sum()) if not top.empty else 0
    if breadth < 0.35 and climax >= 2:
        return "市场宽度偏弱但科技高分主线仍集中，说明资金在抱团，高位方向等回调，低位启动方向优先研究。"
    if focus >= 2 and retreat == 0:
        return "多个主线处在启动或主升阶段，风险分未明显抬升，因此优先研究确认信号而不是追高。"
    if retreat >= 2:
        return "退潮方向增多，赚钱效应不稳定，因此先压低研究范围，回避趋势破坏和高风险主线。"
    return "主线强度和市场宽度并不完全一致，因此只研究有解释、有信号、风险不过热的方向。"


def market_explanation(market_temperature: dict[str, Any], changes: dict[str, Any] | None = None) -> dict[str, Any]:
    """拆解市场温度为什么是这个分数。"""
    metrics = market_temperature.get("metrics", {})
    up_count = int(safe_float(metrics.get("up_count")))
    down_count = int(safe_float(metrics.get("down_count")))
    limit_up = int(safe_float(metrics.get("limit_up")))
    limit_down = int(safe_float(metrics.get("limit_down")))
    amount = safe_float(metrics.get("total_amount_yi"))
    score = safe_float(market_temperature.get("score"))
    breadth_ratio = up_count / max(up_count + down_count, 1)
    amount_text = "继续放大" if amount >= 10_000 else "仍偏温和"
    breadth_text = "市场宽度偏弱" if breadth_ratio < 0.4 else "市场宽度尚可" if breadth_ratio < 0.6 else "上涨家数占优"
    limit_text = "涨停多于跌停" if limit_up > limit_down else "跌停压力仍在"
    change_text = ""
    if changes and changes.get("history_available"):
        delta = safe_float(changes.get("market_temperature_delta"))
        if delta > 2:
            change_text = f"较昨日升温 {delta:.1f} 分"
        elif delta < -2:
            change_text = f"较昨日降温 {abs(delta):.1f} 分"
        else:
            change_text = "较昨日变化不大"
    return {
        "summary": f"{breadth_text}，成交额{amount_text}，{limit_text}，市场温度为 {score:.1f}。",
        "items": [
            f"上涨 {up_count} 家 / 下跌 {down_count} 家，宽度占比 {breadth_ratio * 100:.1f}%。",
            f"涨停 {limit_up} 家 / 跌停 {limit_down} 家。",
            f"全市场成交额约 {amount:,.0f} 亿。",
            change_text or "暂无昨日市场温度快照。",
        ],
    }


def sector_score_breakdown(row: pd.Series | dict) -> list[dict[str, Any]]:
    """按当前评分公式拆解每个主线的得分贡献。"""
    items = []
    for label, field, weight, description in SCORE_COMPONENTS:
        raw = safe_float(row.get(field))
        contribution = raw * weight
        items.append(
            {
                "label": label,
                "field": field,
                "raw_score": round(raw, 1),
                "weight": weight,
                "weight_text": f"{weight * 100:+.0f}%",
                "contribution": round(contribution, 1),
                "bar_pct": round(clamp(abs(contribution) / 40 * 100), 1),
                "description": description,
            }
        )
    return items


def action_explanation(row: pd.Series | dict) -> str:
    """解释为什么主线被放入当前 Action。"""
    action = str(row.get("action", ""))
    lifecycle = str(row.get("lifecycle_state", "未知"))
    score = safe_float(row.get("score"))
    opportunity = safe_float(row.get("opportunity_score"))
    risk = safe_float(row.get("risk_score"))
    distance = safe_float(row.get("distance_ma20_pct"))
    amount_ratio = safe_float(row.get("amount_ratio_20"))
    up_ratio = safe_float(row.get("up_ratio")) * 100
    if action == "重点研究":
        return (
            f"综合分 {score:.1f}、机会分 {opportunity:.1f}，风险分 {risk:.1f} 仍可控；"
            f"生命周期为{lifecycle}，距 MA20 {distance:.1f}%，适合研究回踩或放量确认。"
        )
    if action == "等回调":
        return (
            f"主线强度仍在，量能 {amount_ratio:.2f} 倍、上涨占比 {up_ratio:.1f}%；"
            f"但阶段为{lifecycle}或风险分升至 {risk:.1f}，距 MA20 {distance:.1f}%，因此等待回调而不追高。"
        )
    if action == "只观察 / 不追":
        return (
            f"综合分 {score:.1f} 但机会/风险不够匹配，阶段为{lifecycle}；"
            f"当前更适合观察结构是否重新转强，不把个股升级为候选。"
        )
    if action == "回避":
        return (
            f"生命周期为{lifecycle}或风险分 {risk:.1f} 过高，优先防止亏钱效应扩散；"
            f"相关个股即使出现单日反包，也不能越过主线约束进入候选。"
        )
    return str(row.get("action_reason", "")) or "暂无 Action 解释。"


def confidence_explanation(row: pd.Series | dict) -> str:
    """解释信心分来源。"""
    confidence = safe_float(row.get("confidence_score"))
    history_days = int(safe_float(row.get("history_days")))
    top10_days = int(safe_float(row.get("top10_days")))
    stage_days = int(safe_float(row.get("stage_days")))
    consistency = _indicator_consistency(row)
    return (
        f"信心 {confidence:.1f} 来自历史快照 {history_days} 天、Top10 上榜 {top10_days} 天、"
        f"当前阶段连续 {stage_days} 天、指标一致性 {consistency * 100:.0f}%。"
    )


def stock_priority_score(row: pd.Series | dict) -> float:
    """计算研究优先级，排序目标是研究价值而不是单纯涨幅。"""
    group = str(row.get("stock_research_group") or row.get("pool_group") or "")
    base = safe_float(row.get("research_priority_score") or row.get("leader_score"))
    distance = safe_float(row.get("distance_ma20_pct"))
    trend = str(row.get("trend_status", ""))
    observe = str(row.get("observe_status", ""))
    score = base
    if group == "可研究候选":
        score += 12
    elif group == "强主线回调观察":
        score += 4
    elif group == "等待回调":
        score -= 6
    elif group == "高位观察/不追":
        score -= 18
    elif group == "回避":
        score -= 35
    if 0 <= distance <= 15 and trend in {"多头趋势", "上升趋势"}:
        score += 10
    if distance > 25:
        score -= 18
    if observe in {"缩量回踩 5 日线", "缩量回踩 10 日线", "缩量回踩 20 日线"}:
        score += 8
    elif observe == "放量反包":
        score += 5
    elif observe in {"高位过热", "趋势破坏", "不适合追"}:
        score -= 20
    return round(clamp(score), 1)


def stock_priority_label(score: float) -> str:
    """把研究优先级分数转成 A+/A/B/C。"""
    if score >= 85:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    return "C"


def stock_recommendation_reasons(row: pd.Series | dict, sector_lookup: dict[str, dict[str, Any]]) -> list[str]:
    """生成股票推荐/分组原因。"""
    board_names = [item.strip() for item in str(row.get("board_name", "")).split("/") if item.strip()]
    actions = [sector_lookup.get(name, {}).get("action", "") for name in board_names]
    stages = [sector_lookup.get(name, {}).get("lifecycle_state", "") for name in board_names]
    group = str(row.get("stock_research_group") or row.get("pool_group") or "")
    reasons = []
    if "重点研究" in actions:
        focus_names = [name for name in board_names if sector_lookup.get(name, {}).get("action") == "重点研究"]
        reasons.append("属于重点研究主线（" + " / ".join(focus_names) + "）")
    elif actions:
        reasons.append("所属主线 Action 为 " + " / ".join(_dedupe(actions)) + "，个股分组受父级主线约束")
    if stages:
        reasons.append("生命周期：" + " / ".join(_dedupe(stages)))
    observe = str(row.get("observe_status", ""))
    if observe:
        reasons.append(observe)
    trend = str(row.get("trend_status", ""))
    if trend:
        reasons.append(trend)
    reasons.append(f"MA20 偏离 {safe_float(row.get('distance_ma20_pct')):.1f}%")
    if group == "可研究候选":
        reasons.append("风险未破坏，满足候选池条件")
    elif group == "强主线回调观察":
        reasons.append("主线强但偏热，等待板块回调确认")
    elif group == "回避":
        reasons.append("父级主线回避或个股趋势破坏")
    else:
        reasons.append(str(row.get("stock_group_reason", "")) or "暂不进入主候选")
    return _dedupe([item for item in reasons if item])


def stock_why_not_first(row: dict[str, Any], top: dict[str, Any]) -> str:
    """解释同组排序为什么不是第一。"""
    if not top or str(row.get("code", "")) == str(top.get("code", "")):
        return "当前为本组研究优先级最高，仍需等待实际交易确认。"
    diffs = []
    row_distance = safe_float(row.get("distance_ma20_pct"))
    top_distance = safe_float(top.get("distance_ma20_pct"))
    if row_distance > top_distance + 5:
        diffs.append(f"距 MA20 {row_distance:.1f}% 高于本组第一的 {top_distance:.1f}%")
    row_priority = safe_float(row.get("priority_score"))
    top_priority = safe_float(top.get("priority_score"))
    if row_priority + 0.1 < top_priority:
        diffs.append(f"研究优先级 {row_priority:.1f} 低于本组第一的 {top_priority:.1f}")
    if safe_float(row.get("leader_score")) + 0.1 < safe_float(top.get("leader_score")):
        diffs.append("龙头分低于本组第一")
    if not diffs:
        diffs.append("排序差异主要来自流动性、趋势和风险的综合分")
    return "虽然也在本组内，但" + "，".join(diffs) + "，所以排序靠后。"


def market_state_text(market_temperature: dict[str, Any]) -> str:
    """把市场温度转成短文本。"""
    score = safe_float(market_temperature.get("score"))
    metrics = market_temperature.get("metrics", {})
    up = safe_float(metrics.get("up_count"))
    down = safe_float(metrics.get("down_count"))
    breadth = up / max(up + down, 1)
    if score < 45:
        return "偏弱"
    if score < 60 and breadth < 0.35:
        return "中性偏弱"
    if score < 60:
        return "中性"
    if score < 78:
        return "偏热"
    return "过热"


def strategy_text(market_temperature: dict[str, Any]) -> str:
    """生成首页策略短句。"""
    state = market_state_text(market_temperature)
    if state in {"偏弱", "中性偏弱"}:
        return "不追高，缩小研究范围"
    if state == "中性":
        return "只研究低位确认和回调机会"
    if state == "偏热":
        return "研究回调，不追加速"
    return "控制追高，优先看风险"


def _action_sector_names(
    sectors: pd.DataFrame,
    action: str,
    stock_groups: dict[str, pd.DataFrame],
    limit: int,
) -> str:
    """按 Action 输出主线名称，重点研究无个股信号时明确提示。"""
    if sectors is None or sectors.empty or "action" not in sectors.columns:
        return ""
    rows = sectors[sectors["action"].astype(str) == action].sort_values(["opportunity_score", "score"], ascending=False).head(limit)
    if rows.empty:
        return ""
    candidate_map = _candidate_count_by_sector(stock_groups.get("可研究候选"))
    names = []
    for _, row in rows.iterrows():
        name = str(row.get("board_name", ""))
        if action == "重点研究" and candidate_map.get(name, 0) <= 0:
            names.append(f"{name}（暂无个股信号）")
        else:
            names.append(name)
    return "、".join(names)


def _candidate_count_by_sector(candidate_df: pd.DataFrame | None) -> dict[str, int]:
    """统计候选股票覆盖的主线。"""
    counts: dict[str, int] = {}
    if candidate_df is None or candidate_df.empty or "board_name" not in candidate_df.columns:
        return counts
    for text in candidate_df["board_name"].dropna().astype(str):
        for name in [item.strip() for item in text.split("/") if item.strip()]:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _sector_lookup(sectors: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """把主线表转成按名称索引。"""
    if sectors is None or sectors.empty:
        return {}
    lookup = {}
    for _, row in sectors.iterrows():
        name = str(row.get("board_name", "")).strip()
        if not name:
            continue
        lookup[name] = row.to_dict()
    return lookup


def _indicator_consistency(row: pd.Series | dict) -> float:
    """计算解释用指标一致性。"""
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
    return sum(checks) / max(len(checks), 1)


def _as_list(value: Any) -> list[str]:
    """把 list 或字符串解释统一为列表。"""
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if str(item)]
            except Exception:
                pass
        return [text]
    return []


def _dedupe(values: list[str]) -> list[str]:
    """按原顺序去重。"""
    out = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out
