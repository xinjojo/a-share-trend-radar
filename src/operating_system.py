"""每日 A股主线操作系统。

本模块把市场温度、主线评分、生命周期、轮动历史和股票池合成首页/日报可直接消费的决策摘要。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from src.database import get_connection, init_db
from src.scoring import enrich_operating_scores
from src.utils import safe_float, safe_int, today_str


SNAPSHOT_TABLE = "sector_operating_snapshots"
RESEARCH_STATUSES = {"缩量回踩 5 日线", "缩量回踩 10 日线", "缩量回踩 20 日线", "放量反包"}


def build_operating_system(
    market_temperature: dict,
    sector_df: pd.DataFrame,
    leader_df: pd.DataFrame,
    report_date: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """生成首页和日报共用的每日操作系统摘要。"""
    report_date = report_date or today_str()
    sector_df = sector_df if sector_df is not None else pd.DataFrame()
    leader_df = leader_df if leader_df is not None else pd.DataFrame()
    previous_history = load_sector_snapshots(lookback_days=30)
    stats_input = _history_with_current(previous_history, sector_df, report_date)
    history_stats = build_history_stats(stats_input)
    sectors = enrich_operating_scores(sector_df, history_stats=history_stats)
    sectors = sectors.sort_values(["score", "opportunity_score"], ascending=False).reset_index(drop=True)
    sectors["rank"] = range(1, len(sectors) + 1)
    if persist and not sectors.empty:
        save_sector_snapshots(report_date, sectors, market_temperature)
    history = load_sector_snapshots(lookback_days=30)
    changes = build_today_changes(history, sectors, report_date, market_temperature)
    stock_groups = build_stock_groups(leader_df, sectors)
    one_liner = generate_one_liner(market_temperature, sectors, changes, stock_groups)
    actions = build_today_actions(sectors)
    trends = build_history_trends(history, sectors, lookback_days=10)
    observations = build_next_observations(sectors, changes, stock_groups)
    return {
        "report_date": report_date,
        "sectors": sectors,
        "one_liner": one_liner,
        "actions": actions,
        "changes": changes,
        "stock_groups": stock_groups,
        "history_trends": trends,
        "next_observations": observations,
        "history_available": bool(changes.get("history_available")),
    }


def save_sector_snapshots(report_date: str, sector_df: pd.DataFrame, market_temperature: dict) -> None:
    """保存每日主线操作快照。"""
    if sector_df is None or sector_df.empty:
        return
    _init_snapshot_table()
    market_score = safe_float(market_temperature.get("score"))
    created_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for _, row in sector_df.iterrows():
        rows.append(
            (
                report_date,
                str(row.get("board_code", "")),
                str(row.get("board_name", "")),
                str(row.get("board_layer", row.get("board_type", ""))),
                safe_float(row.get("score")),
                safe_float(row.get("opportunity_score")),
                safe_float(row.get("risk_score")),
                safe_float(row.get("confidence_score")),
                str(row.get("lifecycle_state", "")),
                str(row.get("action", "")),
                safe_int(row.get("rank")),
                _top_stock(row),
                market_score,
                safe_float(row.get("amount_ratio_20")),
                safe_float(row.get("up_ratio")),
                safe_float(row.get("distance_ma20_pct")),
                str(row.get("score_explanation", [])),
                created_at,
            )
        )
    with get_connection() as conn:
        conn.execute(f"DELETE FROM {SNAPSHOT_TABLE} WHERE date = ?", (report_date,))
        conn.executemany(
            f"""
            INSERT INTO {SNAPSHOT_TABLE}(
                date, sector_code, sector_name, sector_type, score, opportunity_score,
                risk_score, confidence_score, lifecycle_stage, action, rank, top_stock,
                market_temperature, amount_ratio_20, up_ratio, distance_ma20_pct,
                score_explanation, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def load_sector_snapshots(lookback_days: int = 30) -> pd.DataFrame:
    """读取最近 N 个快照日的主线操作历史。"""
    _init_snapshot_table()
    with get_connection() as conn:
        dates = pd.read_sql_query(
            f"SELECT DISTINCT date FROM {SNAPSHOT_TABLE} ORDER BY date DESC LIMIT ?",
            conn,
            params=(lookback_days,),
        )
        if dates.empty:
            return pd.DataFrame()
        date_list = dates["date"].tolist()
        placeholders = ",".join(["?"] * len(date_list))
        df = pd.read_sql_query(
            f"""
            SELECT * FROM {SNAPSHOT_TABLE}
            WHERE date IN ({placeholders})
            ORDER BY date ASC, rank ASC
            """,
            conn,
            params=date_list,
        )
    return df


def build_history_stats(history: pd.DataFrame) -> dict[str, dict]:
    """从历史快照中提取信心分需要的统计。"""
    if history is None or history.empty:
        return {}
    stats: dict[str, dict] = {}
    for code, group in history.groupby("sector_code", sort=False):
        group = group.sort_values("date")
        latest = group.iloc[-1]
        latest_stage = str(latest.get("lifecycle_stage", ""))
        stats[str(code)] = {
            "history_days": int(group["date"].nunique()),
            "top10_days": int((pd.to_numeric(group["rank"], errors="coerce") <= 10).sum()),
            "consecutive_top10_days": _consecutive_days(group, lambda row: safe_int(row.get("rank")) <= 10),
            "stage_days": _consecutive_days(group, lambda row: str(row.get("lifecycle_stage", "")) == latest_stage),
        }
    return stats


def build_today_changes(
    history: pd.DataFrame,
    current_df: pd.DataFrame,
    report_date: str,
    market_temperature: dict | None = None,
) -> dict[str, Any]:
    """生成今日变化：新增、退出、分数变化、生命周期变化、龙头切换。"""
    empty = {
        "history_available": False,
        "message": "暂无昨日数据，请连续运行后查看变化。",
        "new_sectors": [],
        "exited_sectors": [],
        "score_gainers": [],
        "score_losers": [],
        "lifecycle_changes": [],
        "leader_switches": [],
    }
    if history is None or history.empty:
        return empty
    dates = sorted([date for date in history["date"].astype(str).unique().tolist() if date < str(report_date)])
    if not dates:
        return empty
    previous_date = dates[-1]
    previous = history[history["date"].astype(str) == previous_date].copy()
    current = _current_operating_records(current_df, report_date)
    if previous.empty or current.empty:
        return empty
    previous_market = safe_float(previous.get("market_temperature", pd.Series([0])).iloc[0]) if "market_temperature" in previous.columns else 0.0
    current_market = safe_float((market_temperature or {}).get("score"))
    prev_codes = set(previous["sector_code"].astype(str).tolist())
    current_codes = set(current["sector_code"].astype(str).tolist())
    merged = current.merge(previous, on="sector_code", suffixes=("_today", "_prev"), how="left")
    score_changes = merged.dropna(subset=["score_prev"]).copy()
    score_changes["score_delta"] = pd.to_numeric(score_changes["score_today"], errors="coerce").fillna(0) - pd.to_numeric(
        score_changes["score_prev"], errors="coerce"
    ).fillna(0)
    lifecycle_changes = []
    leader_switches = []
    for _, row in merged.iterrows():
        prev_stage = str(row.get("lifecycle_stage_prev", ""))
        current_stage = str(row.get("lifecycle_stage_today", ""))
        if prev_stage and current_stage and prev_stage != current_stage:
            lifecycle_changes.append(
                {
                    "sector_name": row.get("sector_name_today", ""),
                    "from": prev_stage,
                    "to": current_stage,
                    "text": f"{row.get('sector_name_today', '')}：{prev_stage} → {current_stage}",
                }
            )
        prev_leader = str(row.get("top_stock_prev", ""))
        current_leader = str(row.get("top_stock_today", ""))
        if prev_leader and current_leader and prev_leader != current_leader:
            leader_switches.append(
                {
                    "sector_name": row.get("sector_name_today", ""),
                    "from": prev_leader,
                    "to": current_leader,
                    "text": f"{row.get('sector_name_today', '')}：{prev_leader} → {current_leader}",
                }
            )
    new_sectors = current[current["sector_code"].astype(str).isin(current_codes - prev_codes)]["sector_name"].tolist()
    exited_sectors = previous[previous["sector_code"].astype(str).isin(prev_codes - current_codes)]["sector_name"].tolist()
    gainers = score_changes.sort_values("score_delta", ascending=False).head(3)
    losers = score_changes.sort_values("score_delta", ascending=True).head(3)
    return {
        "history_available": True,
        "previous_date": previous_date,
        "previous_market_temperature": previous_market,
        "current_market_temperature": current_market,
        "market_temperature_delta": round(current_market - previous_market, 2) if previous_market else 0.0,
        "new_sectors": new_sectors,
        "exited_sectors": exited_sectors,
        "score_gainers": _score_delta_records(gainers),
        "score_losers": _score_delta_records(losers),
        "lifecycle_changes": lifecycle_changes[:6],
        "leader_switches": leader_switches[:6],
    }


def generate_one_liner(
    market_temperature: dict,
    sectors: pd.DataFrame,
    changes: dict[str, Any],
    stock_groups: dict[str, pd.DataFrame],
) -> str:
    """自动生成首页今日一句话。"""
    score = safe_float(market_temperature.get("score"))
    risk = str(market_temperature.get("risk_preference", "未知"))
    top = sectors.head(3) if sectors is not None and not sectors.empty else pd.DataFrame()
    top_parts = [
        f"{row.get('board_name', '')}{row.get('lifecycle_state', '')}"
        for _, row in top.iterrows()
    ]
    top_text = "、".join(top_parts) if top_parts else "主线数据不足"
    new_text = "，新增 " + "、".join(changes.get("new_sectors", [])[:2]) if changes.get("new_sectors") else ""
    fading_names = sectors[sectors.get("action", pd.Series(dtype=str)).isin(["回避"])]["board_name"].head(2).tolist() if sectors is not None and not sectors.empty and "action" in sectors.columns else []
    fading_text = "，回避 " + "、".join(fading_names) if fading_names else ""
    research_count = len(stock_groups.get("可研究候选", pd.DataFrame()))
    if score >= 65:
        stance = "适合研究回调机会，不适合追高"
    elif score >= 50:
        stance = "适合小范围研究强主线，控制追高"
    else:
        stance = "以观察和风险控制为主"
    return f"{top_text}。当前市场温度 {score:.0f}（{risk}）{new_text}{fading_text}；可研究股票 {research_count} 只，{stance}。"


def build_today_actions(sectors: pd.DataFrame) -> dict[str, list[dict[str, str]]]:
    """生成今日 Action 四组。"""
    groups = {"重点研究": [], "等回调": [], "只观察": [], "回避": []}
    if sectors is None or sectors.empty or "action" not in sectors.columns:
        return groups
    for action in groups:
        subset = sectors[sectors["action"] == action].sort_values(["opportunity_score", "score"], ascending=False).head(4)
        for _, row in subset.iterrows():
            groups[action].append(
                {
                    "board_name": str(row.get("board_name", "")),
                    "reason": str(row.get("action_reason", "")),
                    "score": f"{safe_float(row.get('score')):.1f}",
                    "opportunity_score": f"{safe_float(row.get('opportunity_score')):.1f}",
                    "risk_score": f"{safe_float(row.get('risk_score')):.1f}",
                    "confidence_score": f"{safe_float(row.get('confidence_score')):.1f}",
                }
            )
    return groups


def build_stock_groups(leader_df: pd.DataFrame, sectors: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """把股票池拆成可研究候选、等待回调、回避/不追三栏。"""
    groups = {
        "可研究候选": [],
        "等待回调": [],
        "回避 / 不追": [],
    }
    if leader_df is None or leader_df.empty:
        return {key: pd.DataFrame() for key in groups}
    sector_lookup = _sector_lookup(sectors)
    seen: set[str] = set()
    for _, stock in leader_df.sort_values("leader_score", ascending=False).iterrows():
        code = str(stock.get("code", ""))
        if not code or code in seen:
            continue
        seen.add(code)
        matched = _matched_sectors(stock, sector_lookup)
        sector_retreat = any(item.get("lifecycle_stage") == "退潮期" or item.get("action") == "回避" for item in matched)
        sector_strong = any(item.get("action") in {"重点研究", "等回调"} for item in matched)
        observe = str(stock.get("observe_status", ""))
        trend = str(stock.get("trend_status", ""))
        distance = safe_float(stock.get("distance_ma20_pct"))
        ret20 = safe_float(stock.get("ret_20d"))
        row = stock.to_dict()
        row["matched_lifecycle"] = " / ".join(sorted({str(item.get("lifecycle_stage", "")) for item in matched if item.get("lifecycle_stage")}))
        row["matched_action"] = " / ".join(sorted({str(item.get("action", "")) for item in matched if item.get("action")}))
        if sector_retreat or observe in {"趋势破坏", "不适合追"} or distance > 35:
            row["stock_research_group"] = "回避 / 不追"
            row["stock_group_reason"] = "所属主线退潮/回避，或个股趋势破坏、距离 MA20 过远。"
        elif observe in RESEARCH_STATUSES and trend in {"多头趋势", "上升趋势"} and distance <= 25:
            row["stock_research_group"] = "可研究候选"
            row["stock_group_reason"] = f"{observe}，趋势未破坏，距 MA20 {distance:.1f}%。"
        elif sector_strong and trend in {"多头趋势", "上升趋势"} and (distance > 20 or ret20 > 35 or observe in {"等待回调", "高位过热"}):
            row["stock_research_group"] = "等待回调"
            row["stock_group_reason"] = "主线较强但个股短期偏离较大，等待回调或重新确认。"
        else:
            row["stock_research_group"] = "回避 / 不追"
            row["stock_group_reason"] = "不满足回踩/反包候选条件，先不追。"
        groups[row["stock_research_group"]].append(row)
    return {key: pd.DataFrame(rows) for key, rows in groups.items()}


def build_history_trends(history: pd.DataFrame, sectors: pd.DataFrame, lookback_days: int = 10) -> pd.DataFrame:
    """生成最近 10 日主线评分/机会/风险趋势。"""
    if history is None or history.empty or sectors is None or sectors.empty:
        return pd.DataFrame()
    top_codes = sectors.head(8)["board_code"].astype(str).tolist()
    dates = sorted(history["date"].astype(str).unique().tolist())[-lookback_days:]
    out = history[
        history["date"].astype(str).isin(dates)
        & history["sector_code"].astype(str).isin(top_codes)
    ].copy()
    return out.sort_values(["date", "rank"]).reset_index(drop=True)


def build_next_observations(sectors: pd.DataFrame, changes: dict[str, Any], stock_groups: dict[str, pd.DataFrame]) -> list[str]:
    """生成下个交易日最重要的 3 个观察点。"""
    points = []
    focus = sectors[sectors.get("action", pd.Series(dtype=str)) == "重点研究"]["board_name"].head(2).tolist() if sectors is not None and not sectors.empty else []
    if focus:
        points.append("观察重点研究主线是否继续保持上涨占比和成交额放大：" + "、".join(focus))
    wait = sectors[sectors.get("action", pd.Series(dtype=str)) == "等回调"]["board_name"].head(2).tolist() if sectors is not None and not sectors.empty else []
    if wait:
        points.append("等待高位强主线缩量回踩，不追加速：" + "、".join(wait))
    avoid = sectors[sectors.get("action", pd.Series(dtype=str)) == "回避"]["board_name"].head(2).tolist() if sectors is not None and not sectors.empty else []
    if avoid:
        points.append("回避退潮或风险分高的方向：" + "、".join(avoid))
    research_count = len(stock_groups.get("可研究候选", pd.DataFrame()))
    points.append(f"可研究股票池数量为 {research_count}，若继续减少，说明交易机会收缩。")
    return points[:3]


def _history_with_current(history: pd.DataFrame, sector_df: pd.DataFrame, report_date: str) -> pd.DataFrame:
    """把当前板块简化成历史统计需要的结构。"""
    if sector_df is None or sector_df.empty:
        return history
    current = _current_operating_records(sector_df, report_date)
    if history is None or history.empty:
        return current
    return pd.concat([history, current], ignore_index=True)


def _current_operating_records(sector_df: pd.DataFrame, report_date: str) -> pd.DataFrame:
    """把当前主线表转成历史快照结构。"""
    if sector_df is None or sector_df.empty:
        return pd.DataFrame()
    rows = []
    for _, row in sector_df.iterrows():
        rows.append(
            {
                "date": report_date,
                "sector_code": str(row.get("board_code", "")),
                "sector_name": str(row.get("board_name", "")),
                "sector_type": str(row.get("board_layer", row.get("board_type", ""))),
                "score": safe_float(row.get("score")),
                "opportunity_score": safe_float(row.get("opportunity_score")),
                "risk_score": safe_float(row.get("risk_score")),
                "confidence_score": safe_float(row.get("confidence_score")),
                "lifecycle_stage": str(row.get("lifecycle_state", "")),
                "action": str(row.get("action", "")),
                "rank": safe_int(row.get("rank")),
                "top_stock": _top_stock(row),
            }
        )
    return pd.DataFrame(rows)


def _score_delta_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """分数变化行转字典。"""
    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "sector_name": row.get("sector_name_today", ""),
                "delta": round(safe_float(row.get("score_delta")), 2),
                "from": round(safe_float(row.get("score_prev")), 2),
                "to": round(safe_float(row.get("score_today")), 2),
            }
        )
    return records


def _top_stock(row: pd.Series | dict) -> str:
    """从 top_stocks 或 leader 字段取第一龙头。"""
    top_stocks = str(row.get("top_stocks", ""))
    if top_stocks:
        return top_stocks.split("、")[0]
    return str(row.get("leader", ""))


def _consecutive_days(group: pd.DataFrame, predicate) -> int:
    """按日期倒序计算连续满足条件天数。"""
    count = 0
    for _, row in group.sort_values("date", ascending=False).iterrows():
        if predicate(row):
            count += 1
        else:
            break
    return count


def _sector_lookup(sectors: pd.DataFrame) -> list[dict[str, Any]]:
    """把主线表转为股票匹配用列表。"""
    if sectors is None or sectors.empty:
        return []
    rows = []
    for _, row in sectors.iterrows():
        rows.append(
            {
                "board_code": str(row.get("board_code", "")),
                "board_name": str(row.get("board_name", "")),
                "lifecycle_stage": str(row.get("lifecycle_state", "")),
                "action": str(row.get("action", "")),
                "risk_score": safe_float(row.get("risk_score")),
                "opportunity_score": safe_float(row.get("opportunity_score")),
            }
        )
    return rows


def _matched_sectors(stock: pd.Series, sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """匹配股票所属主线。"""
    board_codes = {item.strip() for item in str(stock.get("board_code", "")).split("/") if item.strip()}
    board_names = {item.strip() for item in str(stock.get("board_name", "")).split("/") if item.strip()}
    matched = []
    for sector in sectors:
        if sector["board_code"] in board_codes or sector["board_name"] in board_names:
            matched.append(sector)
    return matched


def _init_snapshot_table() -> None:
    """初始化主线操作历史表。"""
    init_db()
    with get_connection() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
                date TEXT NOT NULL,
                sector_code TEXT NOT NULL,
                sector_name TEXT NOT NULL,
                sector_type TEXT,
                score REAL,
                opportunity_score REAL,
                risk_score REAL,
                confidence_score REAL,
                lifecycle_stage TEXT,
                action TEXT,
                rank INTEGER,
                top_stock TEXT,
                market_temperature REAL,
                amount_ratio_20 REAL,
                up_ratio REAL,
                distance_ma20_pct REAL,
                score_explanation TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (date, sector_code)
            )
            """
        )
