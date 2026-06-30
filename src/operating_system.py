"""每日 A股主线操作系统。

本模块把市场温度、主线评分、生命周期、轮动历史和股票池合成首页/日报可直接消费的决策摘要。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from src.database import get_connection, init_db
from src.explainability import (
    build_today_conclusion,
    build_why_today,
    enrich_sector_explainability,
    enrich_stock_explainability,
    market_explanation,
)
from src.history_db import save_radar_history_snapshot
from src.scoring import enrich_operating_scores
from src.utils import safe_float, safe_int, setup_logger, today_str


SNAPSHOT_TABLE = "sector_operating_snapshots"
RESEARCH_STATUSES = {"缩量回踩 5 日线", "缩量回踩 10 日线", "缩量回踩 20 日线", "放量反包"}
logger = setup_logger(__name__)


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
    if sectors.empty:
        empty_groups = {
            "可研究候选": pd.DataFrame(),
            "强主线回调观察": pd.DataFrame(),
            "等待回调": pd.DataFrame(),
            "高位观察/不追": pd.DataFrame(),
            "回避": pd.DataFrame(),
        }
        changes = build_today_changes(previous_history, sectors, report_date, market_temperature)
        return {
            "report_date": report_date,
            "sectors": sectors,
            "one_liner": "主线数据暂不可用，等待数据源恢复后再更新。",
            "today_conclusion": ["市场：数据不足", "策略：暂停更新，等待数据源恢复", "重点关注：暂无", "等待：暂无", "回避：暂无"],
            "why_today": "行业/概念板块数据暂不可用，不能生成可靠主线判断。",
            "market_explanation": market_explanation(market_temperature, changes),
            "actions": build_today_actions(sectors, empty_groups),
            "changes": changes,
            "stock_groups": empty_groups,
            "history_trends": pd.DataFrame(),
            "next_observations": ["数据源不可用时不输出研究方向，等待下一次刷新。"],
            "history_available": bool(changes.get("history_available")),
            "history_snapshot": {"saved": False, "message": "主线数据为空，未保存历史快照。"},
        }
    sectors = sectors.sort_values(["score", "opportunity_score"], ascending=False).reset_index(drop=True)
    sectors["rank"] = range(1, len(sectors) + 1)
    sectors = _apply_final_action_recommendations(sectors)
    sectors = enrich_sector_explainability(sectors)
    if persist and not sectors.empty:
        save_sector_snapshots(report_date, sectors, market_temperature)
    history = load_sector_snapshots(lookback_days=30)
    changes = build_today_changes(history, sectors, report_date, market_temperature)
    stock_groups = build_stock_groups(leader_df, sectors)
    stock_groups = enrich_stock_explainability(stock_groups, sectors)
    actions = build_today_actions(sectors, stock_groups)
    today_conclusion = build_today_conclusion(market_temperature, sectors, stock_groups)
    why_today = build_why_today(market_temperature, sectors, changes)
    market_reason = market_explanation(market_temperature, changes)
    one_liner = "；".join(today_conclusion)
    history_snapshot = {}
    if persist:
        try:
            history_snapshot = save_radar_history_snapshot(
                report_date=report_date,
                market_temperature=market_temperature,
                sector_df=sectors,
                stock_groups=stock_groups,
                actions=actions,
            )
        except Exception as exc:
            logger.exception("保存 V3 真实快照失败: %s", exc)
            history_snapshot = {
                "saved": False,
                "message": f"历史快照保存失败：{exc}",
            }
    trends = build_history_trends(history, sectors, lookback_days=10)
    observations = build_next_observations(sectors, changes, stock_groups)
    return {
        "report_date": report_date,
        "sectors": sectors,
        "one_liner": one_liner,
        "today_conclusion": today_conclusion,
        "why_today": why_today,
        "market_explanation": market_reason,
        "actions": actions,
        "changes": changes,
        "stock_groups": stock_groups,
        "history_trends": trends,
        "next_observations": observations,
        "history_available": bool(changes.get("history_available")),
        "history_snapshot": history_snapshot,
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
    top_parts = [f"{row.get('board_name', '')}({row.get('lifecycle_state', '')})" for _, row in top.iterrows()]
    top_text = "、".join(top_parts) if top_parts else "主线数据不足"
    startup_text = _sector_names(sectors, lifecycle="启动期", limit=3) or "暂无明确启动期"
    climax_text = _sector_names(sectors, lifecycle="高潮期", limit=3) or "暂无明确高潮期"
    retreat_text = _sector_names(sectors, lifecycle="退潮期", action="回避", limit=3) or "暂无明确退潮方向"
    focus_text = _action_names_for_one_liner(sectors, "重点研究", stock_groups, limit=4)
    wait_text = _action_names_for_one_liner(sectors, "等回调", stock_groups, limit=4)
    observe_text = _action_names_for_one_liner(sectors, "只观察 / 不追", stock_groups, limit=4)
    avoid_text = _action_names_for_one_liner(sectors, "回避", stock_groups, limit=4)
    action_parts = []
    if focus_text:
        action_parts.append(f"重点研究 {focus_text}")
    if wait_text:
        action_parts.append(f"等回调 {wait_text}")
    if observe_text:
        action_parts.append(f"只观察/不追 {observe_text}")
    if avoid_text:
        action_parts.append(f"回避 {avoid_text}")
    current_action = "；".join(action_parts) if action_parts else "以观察为主"
    research_count = len(stock_groups.get("可研究候选", pd.DataFrame()))
    return (
        f"市场温度 {score:.0f}（{risk}），Top3 主线为 {top_text}；"
        f"启动期关注 {startup_text}，高潮期留意 {climax_text}，退潮方向为 {retreat_text}；"
        f"当前动作：{current_action}，可研究候选 {research_count} 只。"
    )


def build_today_actions(
    sectors: pd.DataFrame,
    stock_groups: dict[str, pd.DataFrame] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """生成今日 Action 四组。"""
    groups = {"重点研究": [], "等回调": [], "只观察 / 不追": [], "回避": []}
    if sectors is None or sectors.empty or "action" not in sectors.columns:
        return groups
    candidate_df = (stock_groups or {}).get("可研究候选", pd.DataFrame())
    candidate_map = _candidate_count_by_sector(candidate_df)
    for action in groups:
        subset = sectors[sectors["action"] == action].sort_values(["opportunity_score", "score"], ascending=False).head(4)
        for _, row in subset.iterrows():
            board_name = str(row.get("board_name", ""))
            candidate_count = candidate_map.get(board_name, 0)
            signal_note = ""
            if action == "重点研究" and candidate_count <= 0:
                signal_note = "该主线暂无符合条件个股，等待个股信号；暂无缩量回踩/放量确认个股，暂不列入个股候选。"
            groups[action].append(
                {
                    "board_name": board_name,
                    "reason": str(row.get("action_reason", "")),
                    "explanation": str(row.get("action_explanation", "")),
                    "score": f"{safe_float(row.get('score')):.1f}",
                    "opportunity_score": f"{safe_float(row.get('opportunity_score')):.1f}",
                    "risk_score": f"{safe_float(row.get('risk_score')):.1f}",
                    "confidence_score": f"{safe_float(row.get('confidence_score')):.1f}",
                    "candidate_count": str(candidate_count),
                    "signal_note": signal_note,
                }
            )
    return groups


def build_stock_groups(leader_df: pd.DataFrame, sectors: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """把股票池拆成五栏，并让父级主线 Action 约束个股分组。"""
    groups = {
        "可研究候选": [],
        "强主线回调观察": [],
        "等待回调": [],
        "高位观察/不追": [],
        "回避": [],
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
        matched_actions = {str(item.get("action", "")) for item in matched if item.get("action")}
        matched_names = {str(item.get("board_name", "")) for item in matched if item.get("board_name")}
        sector_retreat = any(item.get("lifecycle_stage") == "退潮期" or item.get("action") == "回避" for item in matched)
        observe = str(stock.get("observe_status", ""))
        trend = str(stock.get("trend_status", ""))
        distance = safe_float(stock.get("distance_ma20_pct"))
        ret20 = safe_float(stock.get("ret_20d"))
        row = stock.to_dict()
        row["matched_lifecycle"] = " / ".join(sorted({str(item.get("lifecycle_stage", "")) for item in matched if item.get("lifecycle_stage")}))
        row["matched_action"] = " / ".join(sorted(matched_actions))
        if matched_names:
            row["board_name"] = " / ".join(sorted(matched_names))
        if sector_retreat or observe == "趋势破坏" or trend == "趋势破坏":
            row["stock_research_group"] = "回避"
            row["stock_group_reason"] = "所属主线退潮/回避，或个股趋势破坏。"
        elif "只观察 / 不追" in matched_actions:
            row["stock_research_group"] = "高位观察/不追"
            row["stock_group_reason"] = "所属主线只观察 / 不追，个股只能进入观察池。"
        elif "等回调" in matched_actions:
            row["stock_research_group"] = "强主线回调观察"
            row["stock_group_reason"] = "主线强但处于高潮或偏热，等待板块回调确认。"
        elif observe in {"高位过热", "不适合追"} or distance > 35:
            row["stock_research_group"] = "高位观察/不追"
            row["stock_group_reason"] = "高位过热、不适合追，或距离 MA20 过远。"
        elif (
            matched_actions == {"重点研究"}
            and observe in RESEARCH_STATUSES
            and trend in {"多头趋势", "上升趋势"}
            and distance <= 25
        ):
            row["stock_research_group"] = "可研究候选"
            row["stock_group_reason"] = f"{observe}，趋势未破坏，距 MA20 {distance:.1f}%。"
        elif matched_actions == {"重点研究"} and trend in {"多头趋势", "上升趋势"} and (distance > 15 or ret20 > 30 or observe == "等待回调"):
            row["stock_research_group"] = "等待回调"
            row["stock_group_reason"] = "主线可研究，但个股偏离 MA20 较远，等待个股回调。"
        else:
            row["stock_research_group"] = "高位观察/不追"
            row["stock_group_reason"] = "不满足回踩/反包候选条件，先不追。"
        groups[row["stock_research_group"]].append(row)
    groups = _enforce_stock_group_constraints(groups, sectors)
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


def _sector_names(
    sectors: pd.DataFrame,
    lifecycle: str | None = None,
    action: str | None = None,
    limit: int = 3,
) -> str:
    """按生命周期或 Action 取主线名称。"""
    if sectors is None or sectors.empty:
        return ""
    out = sectors.copy()
    masks = []
    if lifecycle and "lifecycle_state" in out.columns:
        masks.append(out["lifecycle_state"].astype(str) == lifecycle)
    if action and "action" in out.columns:
        masks.append(out["action"].astype(str) == action)
    if masks:
        mask = masks[0]
        for item in masks[1:]:
            mask = mask | item
        out = out[mask]
    if out.empty or "board_name" not in out.columns:
        return ""
    return "、".join(out["board_name"].dropna().astype(str).head(limit).tolist())


def _action_names_for_one_liner(
    sectors: pd.DataFrame,
    action: str,
    stock_groups: dict[str, pd.DataFrame],
    limit: int = 4,
) -> str:
    """按今日 Action 同一排序生成一句话里的主线列表。"""
    if sectors is None or sectors.empty or "action" not in sectors.columns:
        return ""
    out = sectors[sectors["action"].astype(str) == action].sort_values(["opportunity_score", "score"], ascending=False).head(limit)
    if out.empty:
        return ""
    candidate_map = _candidate_count_by_sector(stock_groups.get("可研究候选", pd.DataFrame()))
    names = []
    for _, row in out.iterrows():
        name = str(row.get("board_name", ""))
        if action == "重点研究" and candidate_map.get(name, 0) <= 0:
            names.append(f"{name}（暂无个股信号）")
        else:
            names.append(name)
    return "、".join(names)


def _apply_final_action_recommendations(sectors: pd.DataFrame) -> pd.DataFrame:
    """页面展示的建议字段统一使用最终 Action，避免旧生命周期建议覆盖。"""
    if sectors is None or sectors.empty or "action" not in sectors.columns:
        return sectors
    out = sectors.copy()
    out["lifecycle_recommendation"] = out["action"].astype(str)
    return out


def _candidate_count_by_sector(candidate_df: pd.DataFrame | None) -> dict[str, int]:
    """统计每条重点研究主线下符合条件的个股数量。"""
    counts: dict[str, int] = {}
    if candidate_df is None or candidate_df.empty or "board_name" not in candidate_df.columns:
        return counts
    for board_names in candidate_df["board_name"].dropna().astype(str):
        for name in [item.strip() for item in board_names.split("/") if item.strip()]:
            counts[name] = counts.get(name, 0) + 1
    return counts


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


def _enforce_stock_group_constraints(
    groups: dict[str, list[dict[str, Any]]],
    sectors: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    """按最终展示主线 Action 再做一次硬分流，防止候选池越级。"""
    action_by_sector = {
        str(row.get("board_name", "")).strip(): str(row.get("action", "")).strip()
        for _, row in sectors.iterrows()
    } if sectors is not None and not sectors.empty else {}
    corrected = {key: [] for key in groups}
    for group_name, rows in groups.items():
        for row in rows:
            final_group = _constrained_group_name(row, group_name, action_by_sector)
            if final_group != group_name:
                row = dict(row)
                if final_group == "回避":
                    row["stock_group_reason"] = "最终展示主线 Action 为回避，个股不能进入候选。"
                elif final_group == "强主线回调观察":
                    row["stock_group_reason"] = "主线强但处于高潮或偏热，等待板块回调确认。"
                elif final_group == "高位观察/不追":
                    row["stock_group_reason"] = "最终展示主线 Action 为只观察 / 不追，个股不能进入候选。"
                row["stock_research_group"] = final_group
            corrected[final_group].append(row)
    return corrected


def _constrained_group_name(
    row: dict[str, Any],
    current_group: str,
    action_by_sector: dict[str, str],
) -> str:
    """返回父级 Action 约束后的最终股票池分组。"""
    if current_group != "可研究候选":
        return current_group
    names = [item.strip() for item in str(row.get("board_name", "")).split("/") if item.strip()]
    actions = [action_by_sector.get(name, "") for name in names]
    if actions and all(action == "重点研究" for action in actions):
        return current_group
    if "回避" in actions:
        return "回避"
    if "只观察 / 不追" in actions:
        return "高位观察/不追"
    if "等回调" in actions:
        return "强主线回调观察"
    return "高位观察/不追"


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
