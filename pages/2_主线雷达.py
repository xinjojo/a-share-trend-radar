"""主线雷达页面。"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT
from src.data_provider import get_provider
from src.sector_radar import build_sector_radar, get_board_detail


st.set_page_config(page_title="主线雷达", layout="wide")
st.title("主线雷达")


@st.cache_data(ttl=1200, show_spinner=False)
def load_radar(include_concepts: bool, max_boards: int):
    """加载板块雷达数据。"""
    provider = get_provider()
    return build_sector_radar(provider, max_boards=max_boards, include_concepts=include_concepts)


include_concepts = st.sidebar.toggle("包含概念板块", value=True)
max_boards = st.sidebar.slider("扫描数量", min_value=8, max_value=30, value=BOARD_ANALYSIS_LIMIT, step=2)

with st.spinner("正在扫描板块持续性..."):
    radar = load_radar(include_concepts, max_boards)

sector_df = radar["all"]
if sector_df.empty:
    st.warning(EMPTY_HINT)
    st.stop()

show_cols = [
    "rank",
    "board_name",
    "board_layer",
    "category",
    "score",
    "rank_stability_score",
    "flow_score_label",
    "flow_score",
    "turnover_activity_score",
    "trend_strength_score",
    "money_effect_score",
    "change_pct",
    "ret_3d",
    "ret_5d",
    "ret_10d",
    "amount_ratio_20",
    "up_ratio",
]
st.subheader("板块评分表")
st.dataframe(sector_df[show_cols].round(2), use_container_width=True, hide_index=True)

layer_tab1, layer_tab2, layer_tab3 = st.tabs(["行业板块", "概念板块", "短线情绪观察"])
with layer_tab1:
    data = radar.get("industry", sector_df)
    st.dataframe(data[show_cols].round(2), use_container_width=True, hide_index=True) if not data.empty else st.caption("暂无行业板块数据。")
with layer_tab2:
    data = radar.get("concept", sector_df.head(0))
    st.dataframe(data[show_cols].round(2), use_container_width=True, hide_index=True) if not data.empty else st.caption("暂无概念板块数据。")
with layer_tab3:
    emotion_df = radar.get("emotion", sector_df.head(0))
    if emotion_df is None or emotion_df.empty:
        st.caption("暂无短线情绪标签。")
    else:
        cols = ["board_name", "change_pct", "amount_yi", "up_count", "down_count", "leader", "emotion_reason"]
        st.dataframe(emotion_df[[c for c in cols if c in emotion_df.columns]].round(2), use_container_width=True, hide_index=True)

selected_name = st.selectbox("选择板块查看详情", sector_df["board_name"].tolist())
selected = sector_df[sector_df["board_name"] == selected_name].iloc[0]
provider = get_provider()
detail = get_board_detail(provider, selected["board_code"], selected["board_name"])
history = detail["history"]
fund_flow = detail["fund_flow"]
constituents = detail["constituents"]

st.subheader(f"{selected_name} 趋势与成交额")
if history is None or history.empty:
    st.warning(EMPTY_HINT)
else:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=history["date"], y=history["close"], name="收盘"))
    for ma in ["ma5", "ma10", "ma20", "ma60"]:
        if ma in history.columns:
            fig.add_trace(go.Scatter(x=history["date"], y=history[ma], name=ma.upper()))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)

    amount_fig = go.Figure()
    amount_fig.add_trace(go.Bar(x=history["date"], y=history["amount_yi"], name="成交额(亿)"))
    amount_fig.update_layout(height=260, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(amount_fig, use_container_width=True)

st.subheader("板块资金持续性")
if fund_flow is None or fund_flow.empty:
    st.info("该数据源暂不可用，当前评分使用“成交活跃度代理评分”，不写作真实资金流。")
else:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=fund_flow["date"], y=fund_flow["main_net"] / 1e8, name="主力净流入(亿)"))
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)

st.subheader("板块成分股强弱排名")
if constituents is None or constituents.empty:
    st.warning(EMPTY_HINT)
else:
    cols = ["code", "name", "price", "change_pct", "amount_yi", "turnover_pct", "vol_ratio", "mcap_yi"]
    st.dataframe(constituents[cols].sort_values(["amount_yi", "change_pct"], ascending=False).head(80).round(2), use_container_width=True, hide_index=True)
