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
    return (
        scored.sort_values(["sector_score", "leader_score"], ascending=[False, False])
        .groupby("board_code", group_keys=False)
        .head(per_sector)
        .sort_values("leader_score", ascending=False)
        .reset_index(drop=True)
    )


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
        "price": safe_float(stock.get("price")),
        "change_pct": safe_float(stock.get("change_pct")),
        "amount_yi": safe_float(stock.get("amount_yi")),
        "turnover_pct": safe_float(stock.get("turnover_pct")),
        "vol_ratio": safe_float(stock.get("vol_ratio")),
        "mcap_yi": safe_float(stock.get("mcap_yi")),
        "ret_20d": safe_float(flags.get("ret_20d")),
        "ret_60d": safe_float(flags.get("ret_60d")),
        "trend_score": trend_score,
        "trend_status": classify_trend(enriched),
        "observe_status": observe_buy_point(enriched),
        "invalid_condition": invalid_condition(enriched),
        "data_source": stock.get("data_source", ""),
    }


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

