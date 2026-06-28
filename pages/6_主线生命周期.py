"""主线生命周期页面。"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT
from src.data_provider import get_provider
from src.sector_radar import build_sector_radar


st.set_page_config(page_title="主线生命周期", layout="wide")
st.title("主线生命周期")
st.caption("生命周期判断用于研究辅助：启动期、主升期、高潮期、分歧期、退潮期、修复期。")


@st.cache_data(ttl=1200, show_spinner=False)
def load_lifecycle():
    """加载带生命周期的主线雷达。"""
    provider = get_provider()
    return build_sector_radar(provider, max_boards=BOARD_ANALYSIS_LIMIT, include_concepts=True)["all"]


with st.spinner("正在计算主线生命周期..."):
    sector_df = load_lifecycle()

if sector_df.empty:
    st.warning(EMPTY_HINT)
    st.stop()

cols = [
    "rank",
    "board_name",
    "board_layer",
    "category",
    "score",
    "lifecycle_state",
    "lifecycle_progress",
    "lifecycle_recommendation",
    "ret_3d",
    "ret_5d",
    "ret_10d",
    "ret_20d",
    "amount_ratio_20",
    "up_ratio",
    "limit_up_count",
    "distance_ma20_pct",
    "high_open_low_close_count",
    "volume_stall_count",
]
st.subheader("生命周期总览")
st.dataframe(sector_df[[c for c in cols if c in sector_df.columns]].round(2), use_container_width=True, hide_index=True)

state_counts = sector_df["lifecycle_state"].value_counts().reset_index()
state_counts.columns = ["生命周期", "数量"]
fig = px.bar(state_counts, x="生命周期", y="数量", text="数量")
st.plotly_chart(fig, use_container_width=True)

selected_name = st.selectbox("选择主线查看解释", sector_df["board_name"].tolist())
selected = sector_df[sector_df["board_name"] == selected_name].iloc[0]

st.subheader(f"{selected_name} 生命周期解释")
metric_cols = st.columns(4)
metric_cols[0].metric("生命周期", selected.get("lifecycle_state", ""))
metric_cols[1].metric("进度", f"{selected.get('lifecycle_progress', 0):.1f} / 100")
metric_cols[2].metric("当前建议", selected.get("lifecycle_recommendation", ""))
metric_cols[3].metric("综合分", f"{selected.get('score', 0):.1f}")
st.progress(min(max(float(selected.get("lifecycle_progress", 0)) / 100, 0), 1))
st.info(selected.get("lifecycle_explanation", "暂无解释。"))

detail_cols = [
    "above_ma5",
    "above_ma10",
    "above_ma20",
    "above_ma60",
    "ma_bull",
    "close",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "distance_ma20_pct",
    "up_ratio",
    "limit_up_count",
    "leader",
    "leader_change",
]
detail = selected[[c for c in detail_cols if c in selected.index]].to_frame("值")
st.dataframe(detail, use_container_width=True)
