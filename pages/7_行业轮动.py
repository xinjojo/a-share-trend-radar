"""行业轮动追踪页面。"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT
from src.data_provider import get_provider
from src.rotation import build_rotation_tracker
from src.sector_radar import build_sector_radar


st.set_page_config(page_title="行业轮动", layout="wide")
st.title("行业轮动追踪")
st.caption("每天记录 Top10 主线，用于观察资金/热度迁移。历史不足时先显示已有快照。")


@st.cache_data(ttl=1200, show_spinner=False)
def load_rotation(lookback_days: int):
    """加载轮动追踪数据。"""
    provider = get_provider()
    sector_df = build_sector_radar(provider, max_boards=BOARD_ANALYSIS_LIMIT, include_concepts=True)["all"]
    return build_rotation_tracker(sector_df, lookback_days=lookback_days, persist=True)


lookback_days = st.sidebar.slider("观察快照数量", min_value=5, max_value=60, value=20, step=5)

with st.spinner("正在更新并分析主线轮动..."):
    rotation = load_rotation(lookback_days)

history = rotation["history"]
migration = rotation["migration"]
summary = rotation["summary"]
current_top = rotation["current_top"]

if current_top.empty:
    st.warning(EMPTY_HINT)
    st.stop()

if history["report_date"].nunique() < 5:
    st.warning("轮动历史快照不足 5 天，当前统计仅供占位观察；连续性会随每日更新自动累积。")

st.subheader("今日 Top10 主线")
st.dataframe(
    current_top[
        [
            "rank",
            "board_name",
            "board_layer",
            "category",
            "score",
            "lifecycle_state",
            "lifecycle_progress",
            "lifecycle_recommendation",
        ]
    ].round(2),
    use_container_width=True,
    hide_index=True,
)

st.subheader("近 20 日主线迁移表")
if migration.empty:
    st.info("暂无轮动历史。")
else:
    st.dataframe(migration, use_container_width=True, hide_index=True)

st.subheader("主线连续性与迁移状态")
if summary.empty:
    st.info("暂无轮动统计。")
else:
    show_cols = [
        "board_name",
        "board_layer",
        "轮动状态",
        "连续上榜天数",
        "首次上榜日期",
        "最近上榜日期",
        "当前排名",
        "排名变化",
        "分数变化",
        "生命周期",
        "生命周期变化",
        "score",
    ]
    st.dataframe(summary[[c for c in show_cols if c in summary.columns]].round(2), use_container_width=True, hide_index=True)
    fig = px.scatter(
        summary,
        x="排名变化",
        y="分数变化",
        color="轮动状态",
        size="连续上榜天数",
        hover_name="board_name",
    )
    st.plotly_chart(fig, use_container_width=True)

st.subheader("历史上榜明细")
if history.empty:
    st.info("暂无历史明细。")
else:
    st.dataframe(history.sort_values(["report_date", "rank"], ascending=[False, True]), use_container_width=True, hide_index=True)
