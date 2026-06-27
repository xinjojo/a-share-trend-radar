"""Markdown 日报生成。"""

from __future__ import annotations

import pandas as pd

from src.database import save_report
from src.utils import pct_text, safe_float, today_str


def generate_daily_report(
    market_temperature: dict,
    sector_df: pd.DataFrame,
    leader_df: pd.DataFrame,
    report_date: str | None = None,
) -> str:
    """生成《A股主线雷达日报》Markdown。"""
    report_date = report_date or today_str()
    sector_df = sector_df if sector_df is not None else pd.DataFrame()
    leader_df = leader_df if leader_df is not None else pd.DataFrame()
    metrics = market_temperature.get("metrics", {})

    lines = [
        f"# A股主线雷达日报",
        "",
        f"日期：{report_date}",
        "",
        "> 本报告仅用于研究辅助，不构成投资建议。",
        "",
        "## 1. 市场温度",
        "",
        f"- 市场温度：**{market_temperature.get('score', 0)} / 100**",
        f"- 风险偏好：**{market_temperature.get('risk_preference', '未知')}**",
        f"- 参与统计股票数：**{metrics.get('sample_count', metrics.get('total', 0))}**",
        f"- 样本完整性：{metrics.get('sample_note', '全市场样本')}",
        f"- 解释：{market_temperature.get('explanation', '数据不足')}",
        "",
        "## 2. 主线排名",
        "",
    ]
    lines.extend(_sector_lines(sector_df.head(10)))

    lines.extend(["", "## 3. 持续主线", ""])
    lines.extend(_sector_lines(_filter_category(sector_df, "持续主线").head(8)))

    lines.extend(["", "## 4. 短线热点", ""])
    lines.extend(_sector_lines(_filter_category(sector_df, "短线热点").head(8)))

    lines.extend(["", "## 5. 退潮板块", ""])
    lines.extend(_sector_lines(_filter_category(sector_df, "退潮板块").head(8)))

    lines.extend(["", "## 6. 龙头观察池", ""])
    if leader_df.empty:
        lines.append("- 暂无可输出的龙头观察池。")
    else:
        for _, row in leader_df.head(20).iterrows():
            lines.append(
                f"- {row.get('name', '')}({row.get('code', '')})："
                f"{row.get('board_name', '')}，{row.get('pool_group', '观察池')}，"
                f"龙头分 {row.get('leader_score', 0)}，"
                f"close={safe_float(row.get('close')):.2f}，"
                f"MA20={safe_float(row.get('ma20')):.2f}，"
                f"距MA20={safe_float(row.get('distance_ma20_pct')):.2f}%，"
                f"价格口径={row.get('price_basis', '不复权')}，"
                f"价格校验={row.get('price_check_status', '')}，"
                f"观察状态：{row.get('observe_status', '')}，"
                f"失效条件：{row.get('invalid_condition', '')}"
            )

    lines.extend(["", "## 7. 今日可进一步研究清单", ""])
    if leader_df.empty:
        lines.append("- 数据不足，建议等待数据源恢复后再筛选。")
    else:
        focus = _filter_research_candidates(leader_df)
        if focus.empty:
            lines.append("- 今日无符合克制条件的研究清单；高位过热、趋势破坏或距离 MA20 过远的标的已过滤。")
        for _, row in focus.head(10).iterrows():
            lines.append(
                f"- {row.get('name', '')}({row.get('code', '')})："
                f"{row.get('observe_status', '')}，所属主线 {row.get('board_name', '')}，"
                f"close={safe_float(row.get('close')):.2f}，"
                f"MA20={safe_float(row.get('ma20')):.2f}，"
                f"距MA20={safe_float(row.get('distance_ma20_pct')):.2f}%，"
                f"价格口径={row.get('price_basis', '不复权')}"
            )

    lines.extend(
        [
            "",
            "## 8. 风险提示",
            "",
            "- 本系统依赖公开数据源，接口延迟、缺失或临时风控会影响结果。",
            "- 若真实资金流数据不可用，板块评分使用“成交活跃度代理评分”，不等同于真实资金净流入。",
            "- 观察状态不是买卖建议，需结合基本面、公告、流动性和个人风险承受能力继续研究。",
            "",
            "## 9. 下个交易日观察点",
            "",
            "- 持续主线是否继续保持成交额放大和上涨家数占优。",
            "- 短线热点能否转化为 3/5/10 日持续性，而不是单日脉冲。",
            "- 退潮板块是否出现跌破 20 日线后的扩散效应。",
            "- 龙头观察池是否出现缩量回踩、放量反包或趋势破坏。",
        ]
    )
    markdown = "\n".join(lines)
    save_report(report_date, markdown)
    return markdown


def _sector_lines(df: pd.DataFrame) -> list[str]:
    """把板块表转成 Markdown bullet。"""
    if df is None or df.empty:
        return ["- 暂无可用数据。"]
    lines = []
    for _, row in df.iterrows():
        lines.append(
            f"- {row.get('board_name', '')}：综合分 {row.get('score', 0)}，"
            f"分类 {row.get('category', '')}，"
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
