"""龙头股票池页面。"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT, INDEX_SYMBOLS
from src.data_provider import get_provider
from src.operating_system import build_operating_system
from src.scoring import score_market_temperature
from src.sector_radar import build_sector_radar
from src.stock_radar import build_leader_pool, get_stock_detail
from src.utils import today_str


st.set_page_config(page_title="龙头股票池", layout="wide")
st.title("龙头股票池")


@st.cache_data(ttl=1200, show_spinner=False)
def load_pool():
    """加载股票池。"""
    provider = get_provider()
    market_df = provider.get_market_quotes()
    index_df = provider.get_index_quotes(INDEX_SYMBOLS)
    temperature = score_market_temperature(market_df, index_df)
    radar = build_sector_radar(provider, max_boards=BOARD_ANALYSIS_LIMIT, include_concepts=True)
    pool = build_leader_pool(provider, radar["all"])
    ops = build_operating_system(temperature, radar["all"], pool, report_date=today_str(), persist=True)
    return ops["sectors"], pool, ops


with st.spinner("正在构建龙头观察池..."):
    sector_df, leader_df, ops = load_pool()

stock_groups = ops.get("stock_groups", {})
if not stock_groups:
    st.warning(EMPTY_HINT)
else:
    cols = [
        "stock_research_group",
        "code",
        "name",
        "board_name",
        "matched_lifecycle",
        "matched_action",
        "leader_score",
        "research_priority_score",
        "sector_score",
        "price",
        "price_basis",
        "current_price",
        "price_check_diff_pct",
        "change_pct",
        "amount_yi",
        "ret_20d",
        "ret_60d",
        "close",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "distance_ma20_pct",
        "trend_status",
        "observe_status",
        "stock_group_reason",
        "price_check_status",
        "price_check_detail",
        "invalid_condition",
    ]
    tabs = st.tabs(["可研究候选", "等待回调", "高位观察/不追", "回避"])
    for tab, name in zip(tabs, ["可研究候选", "等待回调", "高位观察/不追", "回避"], strict=False):
        with tab:
            data = stock_groups.get(name)
            if data is None or data.empty:
                st.caption("暂无符合条件的股票。")
            else:
                show_cols = [col for col in cols if col in data.columns]
                st.dataframe(data[show_cols].round(2), use_container_width=True, hide_index=True)

default_code = leader_df.iloc[0]["code"] if not leader_df.empty else "600519"
code = st.text_input("输入股票代码查看详情", value=str(default_code))
provider = get_provider()
detail = get_stock_detail(provider, code)

info = detail["info"]
blocks = detail["blocks"]
history = detail["history"]
fund_flow = detail["fund_flow"]

st.subheader("股票基础信息")
if info is None or info.empty:
    st.warning(EMPTY_HINT)
else:
    st.dataframe(info.round(2), use_container_width=True, hide_index=True)

cols = st.columns(3)
cols[0].metric("趋势状态", detail["trend_status"])
cols[1].metric("观察状态", detail["observe_status"])
cols[2].metric("失效条件", detail["invalid_condition"])

if blocks is not None and not blocks.empty:
    st.caption("所属主线 / 板块：" + "、".join(blocks["board_name"].head(12).astype(str).tolist()))

st.subheader("K线、均线与成交额")
if history is None or history.empty:
    st.warning(EMPTY_HINT)
else:
    price_basis = history["price_basis"].iloc[-1] if "price_basis" in history.columns else "不复权"
    ma_basis = history["ma_basis"].iloc[-1] if "ma_basis" in history.columns else price_basis
    st.caption(f"价格口径：{price_basis}；均线口径：{ma_basis}。")
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=history["date"],
            open=history["open"],
            high=history["high"],
            low=history["low"],
            close=history["close"],
            name="K线",
        )
    )
    for ma in ["ma5", "ma10", "ma20", "ma60"]:
        if ma in history.columns:
            fig.add_trace(go.Scatter(x=history["date"], y=history[ma], name=ma.upper(), mode="lines"))
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=20, b=10), xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

    amount_col = "amount" if "amount" in history.columns else "volume"
    amount_fig = go.Figure()
    amount_fig.add_trace(go.Bar(x=history["date"], y=history[amount_col], name=amount_col))
    amount_fig.update_layout(height=240, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(amount_fig, use_container_width=True)

st.subheader("资金流向")
if fund_flow is None or fund_flow.empty:
    st.info(EMPTY_HINT)
else:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=fund_flow["date"], y=fund_flow["main_net"] / 1e8, name="主力净流入(亿)"))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)
