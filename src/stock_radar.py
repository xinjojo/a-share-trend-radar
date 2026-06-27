"""龙头股票池筛选。"""

from __future__ import annotations

import pandas as pd

from config import LEADER_CANDIDATES_PER_SECTOR, LEADER_SECTOR_LIMIT, LEADER_STOCKS_PER_SECTOR
from src.data_provider import AStockDataProvider
from src.indicators import (
    add_moving_averages,
    classify_trend,
    invalid_condition,
    latest_trend_flags,
    observe_buy_point,
    trend_score_from_flags,
)
from src.scoring import score_leader_candidates
from src.utils import safe_float, setup_logger


logger = setup_logger(__name__)

RESEARCH_OBSERVE_STATUSES = {"缩量回踩 5 日线", "缩量回踩 10 日线", "缩量回踩 20 日线", "放量反包"}
HIGH_RISK_OBSERVE_STATUSES = {"高位过热", "等待回调", "不适合追", "趋势破坏"}
MAIN_POOL_GROUP = "可研究候选"
WATCH_POOL_GROUP = "高位观察/不适合追"


def build_leader_pool(
    provider: AStockDataProvider,
    sector_df: pd.DataFrame,
    sector_limit: int = LEADER_SECTOR_LIMIT,
    per_sector: int = LEADER_STOCKS_PER_SECTOR,
) -> pd.DataFrame:
    """对强势板块筛选 1-5 只代表性股票。"""
    if sector_df is None or sector_df.empty:
        return pd.DataFrame()

    strong_sectors = sector_df[
        sector_df["category"].isin(["持续主线", "短线热点", "观察中"])
    ].sort_values("score", ascending=False).head(sector_limit)

    candidates = []
    for _, sector in strong_sectors.iterrows():
        board_code = str(sector.get("board_code", ""))
        board_name = str(sector.get("board_name", ""))
        try:
            cons = provider.get_board_constituents(board_code, board_name=board_name)
            if cons is None or cons.empty:
                continue
            cons = _filter_tradeable_candidates(cons)
            cons = cons.sort_values(["amount_yi", "change_pct"], ascending=False).head(LEADER_CANDIDATES_PER_SECTOR)
            for _, stock in cons.iterrows():
                row = _enrich_stock_candidate(provider, stock, sector)
                if row:
                    candidates.append(row)
        except Exception as exc:
            logger.exception("龙头候选筛选失败 %s: %s", board_name, exc)

    df = pd.DataFrame(candidates)
    if df.empty:
        return df
    scored = score_leader_candidates(df)
    per_board = (
        scored.sort_values(["sector_score", "leader_score"], ascending=[False, False])
        .groupby("board_code", group_keys=False)
        .head(per_sector)
    )
    deduped = _deduplicate_leader_pool(per_board)
    return _sort_leader_pool(deduped).reset_index(drop=True)


def _filter_tradeable_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """过滤 ST、小市值、明显一字板等不适合进一步研究的票。"""
    out = df.copy()
    if "name" in out.columns:
        out = out[~out["name"].astype(str).str.contains("ST", case=False, na=False)]
    if "mcap_yi" in out.columns:
        out = out[pd.to_numeric(out["mcap_yi"], errors="coerce").fillna(0) >= 30]
    if {"is_limit_up", "high", "low"}.issubset(out.columns):
        amplitude = (pd.to_numeric(out["high"], errors="coerce") - pd.to_numeric(out["low"], errors="coerce")).abs()
        price = pd.to_numeric(out["price"], errors="coerce").replace(0, pd.NA)
        amplitude_pct = (amplitude / price * 100).fillna(0)
        out = out[~((out["is_limit_up"]) & (amplitude_pct < 1.2))]
    return out


def _enrich_stock_candidate(provider: AStockDataProvider, stock: pd.Series, sector: pd.Series) -> dict:
    """补充个股历史趋势、观察状态和所属主线。"""
    code = str(stock.get("code", ""))
    if not code:
        return {}
    hist = provider.get_stock_history(code, limit=120)
    enriched = add_moving_averages(hist) if hist is not None and not hist.empty else pd.DataFrame()
    flags = latest_trend_flags(enriched)
    trend_score = trend_score_from_flags(flags)
    close = safe_float(flags.get("close"))
    ma20 = safe_float(flags.get("ma20"))
    distance_ma20_pct = (close / ma20 - 1) * 100 if ma20 > 0 else 0.0
    board_price = safe_float(stock.get("price"))
    quote_price = 0.0
    current_price = board_price
    quote_source = ""
    price_check_reference = str(stock.get("data_source", ""))
    try:
        quote = provider.tencent_quote([code]).get(code, {})
        if safe_float(quote.get("price")) > 0:
            quote_price = safe_float(quote.get("price"))
            current_price = quote_price
            quote_source = "a-stock-data:tencent_quote"
            price_check_reference = "a-stock-data:tencent_quote"
    except Exception as exc:
        logger.warning("腾讯行情校验失败 %s: %s", code, exc)
    display_price = close
    price_diff_pct = (close / current_price - 1) * 100 if current_price > 0 and close > 0 else 0.0
    price_check_status = "无法校验"
    if current_price > 0 and close > 0:
        price_check_status = "价格校验通过" if abs(price_diff_pct) <= 3 else "价格校验异常"
    price_check_detail = (
        f"不复权close对比{price_check_reference}，偏差{price_diff_pct:.2f}%"
        if current_price > 0 and close > 0
        else "缺少不复权close或当前行情参考价"
    )
    history_source = ""
    if enriched is not None and not enriched.empty and "data_source" in enriched.columns:
        history_source = str(enriched["data_source"].iloc[-1])
    price_basis = str(flags.get("price_basis") or "不复权")
    ma_basis = str(flags.get("ma_basis") or price_basis)
    adjustment = str(flags.get("adjustment") or "")
    last_trade_date = str(flags.get("date") or "")
    trend_status = classify_trend(enriched)
    observe_status = observe_buy_point(enriched)
    if distance_ma20_pct > 25:
        observe_status = "高位过热"
    pool_group = _classify_pool_group(observe_status, trend_status, distance_ma20_pct)

    # 只保留趋势强于板块或至少处于多头/上升结构的候选。
    if trend_score < 45 and safe_float(stock.get("change_pct")) < safe_float(sector.get("change_pct")):
        return {}

    return {
        "code": code,
        "name": stock.get("name", ""),
        "board_code": sector.get("board_code", ""),
        "board_name": sector.get("board_name", ""),
        "sector_category": sector.get("category", ""),
        "sector_score": safe_float(sector.get("score")),
        "price": display_price,
        "price_is_unadjusted_close": close > 0 and price_basis == "不复权",
        "price_basis": price_basis,
        "ma_basis": ma_basis,
        "adjustment": adjustment,
        "last_trade_date": last_trade_date,
        "board_price": board_price,
        "quote_price": quote_price,
        "current_price": current_price,
        "quote_source": quote_source,
        "price_check_reference": price_check_reference,
        "change_pct": safe_float(stock.get("change_pct")),
        "amount_yi": safe_float(stock.get("amount_yi")),
        "turnover_pct": safe_float(stock.get("turnover_pct")),
        "vol_ratio": safe_float(stock.get("vol_ratio")),
        "mcap_yi": safe_float(stock.get("mcap_yi")),
        "ret_20d": safe_float(flags.get("ret_20d")),
        "ret_60d": safe_float(flags.get("ret_60d")),
        "close": close,
        "ma5": safe_float(flags.get("ma5")),
        "ma10": safe_float(flags.get("ma10")),
        "ma20": ma20,
        "ma60": safe_float(flags.get("ma60")),
        "distance_ma20_pct": distance_ma20_pct,
        "price_check_status": price_check_status,
        "price_check_diff_pct": price_diff_pct,
        "price_check_detail": price_check_detail,
        "history_source": history_source,
        "trend_score": trend_score,
        "trend_status": trend_status,
        "observe_status": observe_status,
        "pool_group": pool_group,
        "invalid_condition": invalid_condition(enriched),
        "data_source": stock.get("data_source", ""),
    }


def _classify_pool_group(observe_status: str, trend_status: str, distance_ma20_pct: float) -> str:
    """按观察状态和 MA20 距离拆分主候选与高位观察组。"""
    if distance_ma20_pct > 35:
        return WATCH_POOL_GROUP
    if distance_ma20_pct > 25:
        return WATCH_POOL_GROUP
    if observe_status in HIGH_RISK_OBSERVE_STATUSES:
        return WATCH_POOL_GROUP
    if trend_status == "趋势破坏":
        return WATCH_POOL_GROUP
    if observe_status in RESEARCH_OBSERVE_STATUSES:
        return MAIN_POOL_GROUP
    return WATCH_POOL_GROUP


def _deduplicate_leader_pool(df: pd.DataFrame) -> pd.DataFrame:
    """同一股票只保留一行，并合并其所属多个主线。"""
    if df is None or df.empty or "code" not in df.columns:
        return pd.DataFrame() if df is None else df.copy()
    sort_cols = [col for col in ["leader_score", "sector_score", "amount_yi"] if col in df.columns]
    sorted_df = df.sort_values(sort_cols, ascending=[False] * len(sort_cols)) if sort_cols else df.copy()
    rows = []
    for _, group in sorted_df.groupby("code", sort=False):
        group_sorted = group.sort_values(
            [col for col in ["leader_score", "sector_score", "amount_yi"] if col in group.columns],
            ascending=False,
        )
        best = group_sorted.iloc[0].copy()
        best["board_name"] = _join_unique(group_sorted.get("board_name", pd.Series(dtype=str)))
        best["board_code"] = _join_unique(group_sorted.get("board_code", pd.Series(dtype=str)))
        best["sector_category"] = _join_unique(group_sorted.get("sector_category", pd.Series(dtype=str)))
        best["merged_board_count"] = int(group_sorted.get("board_name", pd.Series(dtype=str)).astype(str).nunique())
        rows.append(best.to_dict())
    return pd.DataFrame(rows)


def _sort_leader_pool(df: pd.DataFrame) -> pd.DataFrame:
    """可研究候选优先，其次按研究优先级和龙头分排序。"""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    out = df.copy()
    if "pool_group" not in out.columns:
        out["pool_group"] = WATCH_POOL_GROUP
    if "research_priority_score" not in out.columns:
        out["research_priority_score"] = out.get("leader_score", pd.Series(0, index=out.index)).map(safe_float)
    out["_pool_group_order"] = out["pool_group"].map({MAIN_POOL_GROUP: 0, WATCH_POOL_GROUP: 1}).fillna(1)
    out = out.sort_values(
        ["_pool_group_order", "research_priority_score", "leader_score", "amount_yi"],
        ascending=[True, False, False, False],
    )
    return out.drop(columns=["_pool_group_order"], errors="ignore")


def _join_unique(values: pd.Series) -> str:
    """按原顺序合并去重后的文本值。"""
    seen = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
    return " / ".join(seen)


def get_stock_detail(provider: AStockDataProvider, code: str) -> dict[str, pd.DataFrame | str]:
    """个股详情页数据。"""
    hist = provider.get_stock_history(code, limit=160)
    enriched = add_moving_averages(hist) if hist is not None and not hist.empty else hist
    return {
        "history": enriched,
        "info": provider.get_stock_info(code),
        "blocks": provider.get_stock_blocks(code),
        "fund_flow": provider.get_stock_fund_flow(code),
        "trend_status": classify_trend(enriched),
        "observe_status": observe_buy_point(enriched),
        "invalid_condition": invalid_condition(enriched),
    }
