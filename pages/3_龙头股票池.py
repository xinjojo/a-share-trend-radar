"""龙头股票池页面。"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT
from src.data_provider import get_provider
from src.sector_radar import build_sector_radar
from src.stock_radar import build_leader_pool, get_stock_detail


st.set_page_config(page_title="龙头股票池", layout="wide")
st.title("龙头股票池")


@st.cache_data(ttl=1200, show_spinner=False)
def load_pool():
    """加载股票池。"""
    provider = get_provider()
    radar = build_sector_radar(provider, max_boards=BOARD_ANALYSIS_LIMIT, include_concepts=True)
    pool = build_leader_pool(provider, radar["all"])
    return radar["all"], pool


with st.spinner("正在构建龙头观察池..."):
    sector_df, leader_df = load_pool()

if leader_df.empty:
    st.warning(EMPTY_HINT)
else:
    cols = [
        "pool_group",
        "code",
        "name",
        "board_name",
        "leader_score",
        "research_priority_score",
        "sector_score",
        "price",
        "price_basis",
        "quote_price",
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
        "price_check_status",
        "invalid_condition",
    ]
    cols = [col for col in cols if col in leader_df.columns]
    if "pool_group" not in leader_df.columns:
        leader_df = leader_df.assign(pool_group="高位观察/不适合追")
    research_df = leader_df[leader_df["pool_group"] == "可研究候选"]
    watch_df = leader_df[leader_df["pool_group"] != "可研究候选"]
    tab1, tab2 = st.tabs(["可研究候选", "高位观察/不适合追"])
    with tab1:
        if research_df.empty:
            st.caption("暂无符合克制条件的可研究候选。")
        else:
            st.dataframe(research_df[cols].round(2), use_container_width=True, hide_index=True)
    with tab2:
        if watch_df.empty:
            st.caption("暂无高位观察标的。")
        else:
            st.dataframe(watch_df[cols].round(2), use_container_width=True, hide_index=True)

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
