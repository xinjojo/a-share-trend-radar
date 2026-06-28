"""Markdown 日报生成。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.database import save_report
from src.utils import pct_text, safe_float, today_str


def generate_daily_report(
    market_temperature: dict,
    sector_df: pd.DataFrame,
    leader_df: pd.DataFrame,
    report_date: str | None = None,
    ops_summary: dict[str, Any] | None = None,
) -> str:
    """生成《A 股主线操作系统日报》Markdown。"""
    report_date = report_date or today_str()
    sector_df = sector_df if sector_df is not None else pd.DataFrame()
    leader_df = leader_df if leader_df is not None else pd.DataFrame()
    if ops_summary is None:
        from src.operating_system import build_operating_system

        ops_summary = build_operating_system(market_temperature, sector_df, leader_df, report_date=report_date, persist=False)
    enriched_sectors = ops_summary.get("sectors")
    if isinstance(enriched_sectors, pd.DataFrame):
        sector_df = enriched_sectors
    metrics = market_temperature.get("metrics", {})
    changes = ops_summary.get("changes", {})
    stock_groups = ops_summary.get("stock_groups", {})

    lines = [
        "# A 股主线操作系统日报",
        "",
        f"日期：{report_date}",
        "",
        "> 本报告仅用于研究辅助，不构成投资建议。",
        "> 3 分钟摘要：先看今日一句话、今日 Action 和今日变化，再看股票池与风险方向。",
        "",
        "## 1. 今日一句话",
        "",
        ops_summary.get("one_liner", "主线数据不足，先观察数据源状态。"),
        "",
        "## 2. 市场温度变化",
        "",
    ]
    lines.extend(_market_temperature_lines(market_temperature, changes, metrics))

    lines.extend(["", "## 3. 今日 Action", ""])
    lines.extend(_action_lines(ops_summary.get("actions", {})))

    lines.extend(["", "## 4. 今日变化", ""])
    lines.extend(_change_lines(changes))

    lines.extend(["", "## 5. Top 主线解释", ""])
    lines.extend(_top_sector_explanations(sector_df.head(5)))

    lines.extend(["", "## 6. 可研究股票池", ""])
    lines.extend(_stock_group_lines(stock_groups.get("可研究候选"), limit=10))

    lines.extend(["", "## 7. 高位风险池", ""])
    lines.extend(_stock_group_lines(stock_groups.get("等待回调"), limit=8, empty_text="暂无等待回调标的。"))

    lines.extend(["", "## 8. 退潮/回避方向", ""])
    avoid_sectors = sector_df[sector_df.get("action", pd.Series(dtype=str)) == "回避"] if not sector_df.empty and "action" in sector_df.columns else pd.DataFrame()
    lines.extend(_sector_lines(avoid_sectors.head(8), compact=True))
    lines.extend(_stock_group_lines(stock_groups.get("回避 / 不追"), limit=8, empty_text="暂无回避股票池。"))

    lines.extend(
        [
            "",
            "## 9. 下个交易日最重要的 3 个观察点",
            "",
        ]
    )
    observations = ops_summary.get("next_observations") or [
        "持续主线是否继续保持成交额放大和上涨家数占优。",
        "短线热点能否转化为 3/5/10 日持续性，而不是单日脉冲。",
        "退潮板块是否出现跌破 20 日线后的扩散效应。",
    ]
    lines.extend(f"- {item}" for item in observations[:3])
    lines.extend(
        [
            "",
            "## 风险提示",
            "",
            "- 本系统依赖公开数据源，接口延迟、缺失或临时风控会影响结果。",
            "- 若真实资金流数据不可用，板块评分使用“成交活跃度代理评分”，不等同于真实资金净流入。",
            "- 观察状态不是买卖建议，需结合基本面、公告、流动性和个人风险承受能力继续研究。",
        ]
    )
    markdown = "\n".join(lines)
    save_report(report_date, markdown)
    return markdown


def _market_temperature_lines(market_temperature: dict, changes: dict[str, Any], metrics: dict) -> list[str]:
    """市场温度变化摘要。"""
    lines = [
        f"- 市场温度：**{market_temperature.get('score', 0)} / 100**，风险偏好：**{market_temperature.get('risk_preference', '未知')}**",
        f"- 参与统计股票数：**{metrics.get('sample_count', metrics.get('total', 0))}**；样本完整性：{metrics.get('sample_note', '全市场样本')}",
        f"- 市场宽度：上涨 {metrics.get('up_count', 0)} 家、下跌 {metrics.get('down_count', 0)} 家；涨停约 {metrics.get('limit_up', 0)} 家、跌停约 {metrics.get('limit_down', 0)} 家。",
    ]
    if changes.get("history_available") and safe_float(changes.get("previous_market_temperature")) > 0:
        lines.append(
            f"- 较 {changes.get('previous_date')}：市场温度 {safe_float(changes.get('previous_market_temperature')):.1f} → "
            f"{safe_float(changes.get('current_market_temperature')):.1f}，变化 {safe_float(changes.get('market_temperature_delta')):+.1f}。"
        )
    else:
        lines.append("- 暂无昨日市场温度快照，请连续运行后查看变化。")
    return lines


def _action_lines(actions: dict[str, list[dict[str, Any]]]) -> list[str]:
    """今日 Action Markdown。"""
    lines = []
    for label in ["重点研究", "等回调", "只观察", "回避"]:
        rows = actions.get(label, []) if isinstance(actions, dict) else []
        if not rows:
            lines.append(f"- {label}：暂无。")
            continue
        text = "；".join(
            f"{row.get('board_name', '')}（{row.get('reason', '')}，机会 {row.get('opportunity_score', '')}，风险 {row.get('risk_score', '')}，信心 {row.get('confidence_score', '')}）"
            for row in rows[:3]
        )
        lines.append(f"- {label}：{text}。")
    return lines


def _change_lines(changes: dict[str, Any]) -> list[str]:
    """今日变化 Markdown。"""
    if not changes or not changes.get("history_available"):
        return [f"- {changes.get('message', '暂无昨日数据，请连续运行后查看变化。') if changes else '暂无昨日数据，请连续运行后查看变化。'}"]
    lines = [f"- 对比日期：{changes.get('previous_date')}"]
    lines.append("- 新增主线：" + (_join_text(changes.get("new_sectors")) or "暂无"))
    lines.append("- 退出主线：" + (_join_text(changes.get("exited_sectors")) or "暂无"))
    lines.append("- 评分上升最多：" + (_delta_text(changes.get("score_gainers")) or "暂无"))
    lines.append("- 评分下降最多：" + (_delta_text(changes.get("score_losers")) or "暂无"))
    lines.append("- 生命周期变化：" + (_record_text(changes.get("lifecycle_changes")) or "暂无"))
    lines.append("- 龙头切换：" + (_record_text(changes.get("leader_switches")) or "暂无"))
    return lines


def _top_sector_explanations(df: pd.DataFrame) -> list[str]:
    """Top 主线解释。"""
    if df is None or df.empty:
        return ["- 暂无可用数据。"]
    lines = []
    for _, row in df.iterrows():
        explanation = "；".join(_as_list(row.get("score_explanation"))[:4])
        lines.append(
            f"- {row.get('board_name', '')}：综合分 {row.get('score', 0)}，机会 {row.get('opportunity_score', 0)}，"
            f"风险 {row.get('risk_score', 0)}，信心 {row.get('confidence_score', 0)}；Action：{row.get('action', '')}；{explanation}。"
        )
    return lines


def _stock_group_lines(df: pd.DataFrame | None, limit: int = 10, empty_text: str = "今日无符合克制条件的研究清单。") -> list[str]:
    """股票池分组 Markdown。"""
    if df is None or df.empty:
        return [f"- {empty_text}"]
    lines = []
    for _, row in df.head(limit).iterrows():
        lines.append(
            f"- {row.get('name', '')}({row.get('code', '')})：{row.get('observe_status', '')}，"
            f"所属主线 {row.get('board_name', '')}，"
            f"close={safe_float(row.get('close')):.2f}，MA20={safe_float(row.get('ma20')):.2f}，"
            f"距MA20={safe_float(row.get('distance_ma20_pct')):.2f}%，"
            f"价格口径={row.get('price_basis', '不复权')}，原因：{row.get('stock_group_reason', '')}"
        )
    return lines


def _sector_lines(df: pd.DataFrame, compact: bool = False) -> list[str]:
    """把板块表转成 Markdown bullet。"""
    if df is None or df.empty:
        return ["- 暂无可用数据。"]
    lines = []
    for _, row in df.iterrows():
        if compact:
            lines.append(
                f"- {row.get('board_name', '')}：综合分 {row.get('score', 0)}，风险 {row.get('risk_score', 0)}，"
                f"生命周期 {row.get('lifecycle_state', '未知')}，原因：{row.get('action_reason', '')}"
            )
        else:
            lines.append(
                f"- {row.get('board_name', '')}：综合分 {row.get('score', 0)}，"
                f"分类 {row.get('category', '')}，"
                f"生命周期 {row.get('lifecycle_state', '未知')}（{row.get('lifecycle_recommendation', '观察')}），"
                f"当日涨幅 {pct_text(row.get('change_pct', 0))}，"
                f"5日涨幅 {pct_text(row.get('ret_5d', 0))}，"
                f"10日涨幅 {pct_text(row.get('ret_10d', 0))}，"
                f"量能倍数 {safe_float(row.get('amount_ratio_20', 0)):.2f}，"
                f"{row.get('flow_score_label', '成交活跃度代理评分')} {safe_float(row.get('flow_score', 0)):.1f}。"
            )
    return lines


def _filter_category(df: pd.DataFrame, category: str) -> pd.DataFrame:
    """缺少 category 列时安全返回空表。"""
    if df is None or df.empty or "category" not in df.columns:
        return pd.DataFrame()
    return df[df["category"] == category]


def _filter_research_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """日报研究清单过滤：高位过热和趋势破坏不输出。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    for col, default in {
        "observe_status": "",
        "trend_status": "",
        "pool_group": "",
        "distance_ma20_pct": 999,
        "leader_score": 0,
        "research_priority_score": 0,
        "amount_yi": 0,
    }.items():
        if col not in out.columns:
            out[col] = default
    out = out[out["pool_group"] == "可研究候选"]
    out = out[~out["observe_status"].isin(["高位过热", "趋势破坏", "不适合追", "等待回调"])]
    out = out[out["trend_status"] != "趋势破坏"]
    pullback = out["observe_status"].isin(["缩量回踩 5 日线", "缩量回踩 10 日线", "缩量回踩 20 日线"])
    distance = out["distance_ma20_pct"].map(safe_float).abs()
    reversal = (out["observe_status"] == "放量反包") & (distance <= 25)
    filtered = out[(pullback & (distance <= 25)) | reversal].copy()
    if filtered.empty:
        return filtered
    return filtered.sort_values(["research_priority_score", "leader_score", "amount_yi"], ascending=[False, False, False])


def _join_text(items: list[Any] | None) -> str:
    """列表合并。"""
    if not items:
        return ""
    return "、".join(str(item) for item in items[:6] if str(item))


def _delta_text(items: list[dict[str, Any]] | None) -> str:
    """分数变化文字。"""
    if not items:
        return ""
    return "；".join(
        f"{item.get('sector_name', '')} {safe_float(item.get('from')):.1f}→{safe_float(item.get('to')):.1f}（{safe_float(item.get('delta')):+.1f}）"
        for item in items[:3]
    )


def _record_text(items: list[dict[str, Any]] | None) -> str:
    """带 text 字段的记录合并。"""
    if not items:
        return ""
    return "；".join(str(item.get("text", "")) for item in items[:4] if item.get("text"))


def _as_list(value: Any) -> list[str]:
    """评分解释字段转列表。"""
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []
