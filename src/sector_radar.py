"""主线行业/概念雷达。"""

from __future__ import annotations

import pandas as pd

from config import BOARD_ANALYSIS_LIMIT
from src.data_provider import AStockDataProvider
from src.indicators import add_moving_averages, latest_trend_flags
from src.scoring import score_sector_dataframe
from src.utils import safe_float, safe_int, setup_logger


logger = setup_logger(__name__)


def build_sector_radar(
    provider: AStockDataProvider,
    max_boards: int = BOARD_ANALYSIS_LIMIT,
    include_concepts: bool = True,
) -> dict[str, pd.DataFrame]:
    """扫描行业/概念板块，输出持续主线、短线热点、退潮板块。"""
    industry_df = provider.get_industry_boards()
    frames = [industry_df]
    if include_concepts:
        concept_df = provider.get_concept_boards()
        frames.append(concept_df)
    board_df = pd.concat([df for df in frames if df is not None and not df.empty], ignore_index=True)
    if board_df.empty:
        empty = pd.DataFrame()
        return {"all": empty, "持续主线": empty, "短线热点": empty, "退潮板块": empty}

    # 兼顾涨幅和成交额，避免只扫到单日脉冲。
    candidates = (
        pd.concat(
            [
                board_df.sort_values("change_pct", ascending=False).head(max_boards),
                board_df.sort_values("amount_yi", ascending=False).head(max_boards),
            ],
            ignore_index=True,
        )
        .drop_duplicates("board_code")
        .head(max_boards)
    )

    rows = []
    for _, board in candidates.iterrows():
        board_code = str(board.get("board_code", ""))
        board_name = str(board.get("board_name", ""))
        try:
            hist = provider.get_board_history(board_code, board_name=board_name, limit=90)
            constituents = provider.get_board_constituents(board_code, board_name=board_name)
            rows.append(_build_board_metrics(board, hist, constituents))
        except Exception as exc:
            logger.exception("板块指标计算失败 %s %s: %s", board_code, board_name, exc)

    metrics_df = pd.DataFrame(rows)
    scored = score_sector_dataframe(metrics_df)
    return {
        "all": scored,
        "持续主线": scored[scored["category"] == "持续主线"].reset_index(drop=True) if not scored.empty else scored,
        "短线热点": scored[scored["category"] == "短线热点"].reset_index(drop=True) if not scored.empty else scored,
        "退潮板块": scored[scored["category"] == "退潮板块"].reset_index(drop=True) if not scored.empty else scored,
    }


def _build_board_metrics(board: pd.Series, hist: pd.DataFrame, constituents: pd.DataFrame) -> dict:
    """把单个板块的行情、历史和成分股合成为评分输入。"""
    board_code = str(board.get("board_code", ""))
    board_name = str(board.get("board_name", ""))
    enriched = add_moving_averages(hist) if hist is not None and not hist.empty else pd.DataFrame()
    flags = latest_trend_flags(enriched)

    amount_3d = _rolling_sum(enriched, "amount_yi", 3)
    amount_5d = _rolling_sum(enriched, "amount_yi", 5)
    amount_10d = _rolling_sum(enriched, "amount_yi", 10)
    ret_3d = _period_return(enriched, 3)
    ret_5d = _period_return(enriched, 5)
    ret_10d = _period_return(enriched, 10)

    # MVP 中板块资金流若无法直接取到，使用“成交额 * 阶段涨幅”的符号代理。
    fund_3d = amount_3d * ret_3d / 100
    fund_5d = amount_5d * ret_5d / 100
    fund_10d = amount_10d * ret_10d / 100

    up_count = safe_int(board.get("up_count"))
    down_count = safe_int(board.get("down_count"))
    up_ratio = up_count / max(up_count + down_count, 1)

    limit_up_count = 0
    leader_amount_share = 0.0
    top_stocks = ""
    if constituents is not None and not constituents.empty:
        if "is_limit_up" in constituents.columns:
            limit_up_count = int(constituents["is_limit_up"].sum())
        total_amount = safe_float(constituents.get("amount_yi", pd.Series(dtype=float)).sum())
        top_amount = safe_float(constituents.sort_values("amount_yi", ascending=False).head(3).get("amount_yi", 0).sum())
        leader_amount_share = top_amount / total_amount if total_amount > 0 else 0
        top_stocks = "、".join(
            constituents.sort_values(["amount_yi", "change_pct"], ascending=False)
            .head(5)
            .apply(lambda r: f"{r.get('name', '')}({r.get('code', '')})", axis=1)
            .tolist()
        )

    return {
        "board_code": board_code,
        "board_name": board_name,
        "board_type": board.get("board_type", ""),
        "change_pct": safe_float(board.get("change_pct")),
        "amount_yi": safe_float(board.get("amount_yi")),
        "amount_3d": amount_3d,
        "amount_5d": amount_5d,
        "amount_10d": amount_10d,
        "fund_3d": fund_3d,
        "fund_5d": fund_5d,
        "fund_10d": fund_10d,
        "fund_source": "signed_amount_proxy",
        "ret_3d": ret_3d,
        "ret_5d": ret_5d,
        "ret_10d": ret_10d,
        "ret_20d": safe_float(flags.get("ret_20d")),
        "above_ma5": bool(flags.get("above_ma5")),
        "above_ma10": bool(flags.get("above_ma10")),
        "above_ma20": bool(flags.get("above_ma20")),
        "above_ma60": bool(flags.get("above_ma60")),
        "ma_bull": bool(flags.get("ma_bull")),
        "amount_ratio_20": safe_float(flags.get("amount_ratio_20")),
        "up_count": up_count,
        "down_count": down_count,
        "up_ratio": up_ratio,
        "limit_up_count": limit_up_count,
        "leader": board.get("leader", ""),
        "leader_change": safe_float(board.get("leader_change")),
        "leader_amount_share": leader_amount_share,
        "top_stocks": top_stocks,
        "data_source": board.get("data_source", ""),
    }


def _rolling_sum(df: pd.DataFrame, col: str, window: int) -> float:
    """取最近 N 日求和。"""
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return safe_float(pd.to_numeric(df[col], errors="coerce").fillna(0).tail(window).sum())


def _period_return(df: pd.DataFrame, window: int) -> float:
    """计算最近 N 日涨幅。"""
    if df is None or df.empty or "close" not in df.columns or len(df) <= window:
        return 0.0
    close = pd.to_numeric(df["close"], errors="coerce").fillna(0)
    base = safe_float(close.iloc[-window - 1])
    latest = safe_float(close.iloc[-1])
    if base <= 0:
        return 0.0
    return (latest / base - 1) * 100


def get_board_detail(provider: AStockDataProvider, board_code: str, board_name: str = "") -> dict[str, pd.DataFrame]:
    """板块详情页所需数据。"""
    hist = provider.get_board_history(board_code, board_name=board_name, limit=120)
    fund = provider.get_board_fund_flow(board_code)
    constituents = provider.get_board_constituents(board_code, board_name=board_name)
    return {
        "history": add_moving_averages(hist) if hist is not None and not hist.empty else hist,
        "fund_flow": fund,
        "constituents": constituents,
    }

