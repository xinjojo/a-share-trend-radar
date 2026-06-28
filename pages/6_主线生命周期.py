"""主线生命周期页面。"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT, INDEX_SYMBOLS
from src.data_provider import get_provider
from src.operating_system import build_operating_system
from src.scoring import score_market_temperature
from src.sector_radar import build_sector_radar
from src.stock_radar import build_leader_pool
from src.utils import today_str


st.set_page_config(page_title="主线生命周期", layout="wide")
st.title("主线生命周期")
st.caption("生命周期判断用于研究辅助：启动期、主升期、高潮期、分歧期、退潮期、修复期。")


@st.cache_data(ttl=1200, show_spinner=False)
def load_lifecycle():
    """加载带生命周期的主线雷达。"""
    provider = get_provider()
    market_df = provider.get_market_quotes()
    index_df = provider.get_index_quotes(INDEX_SYMBOLS)
    temperature = score_market_temperature(market_df, index_df)
    sector_df = build_sector_radar(provider, max_boards=BOARD_ANALYSIS_LIMIT, include_concepts=True)["all"]
    leader_df = build_leader_pool(provider, sector_df)
    ops = build_operating_system(temperature, sector_df, leader_df, report_date=today_str(), persist=True)
    return ops["sectors"], ops


with st.spinner("正在计算主线生命周期..."):
    sector_df, ops = load_lifecycle()

if sector_df.empty:
    st.warning(EMPTY_HINT)
    st.stop()

sector_df = sector_df.sort_values(["opportunity_score", "score"], ascending=False).reset_index(drop=True)

cols = [
    "rank",
    "board_name",
    "board_layer",
    "category",
    "score",
    "opportunity_score",
    "risk_score",
    "confidence_score",
    "action",
    "lifecycle_state",
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
    "stage_days",
]
st.subheader("生命周期总览")
st.dataframe(sector_df[[c for c in cols if c in sector_df.columns]].round(2), use_container_width=True, hide_index=True)

state_counts = sector_df["lifecycle_state"].value_counts().reset_index()
state_counts.columns = ["生命周期", "数量"]
fig = px.bar(state_counts, x="生命周期", y="数量", text="数量")
st.plotly_chart(fig, use_container_width=True)

trend_df = ops.get("history_trends")
if trend_df is not None and not trend_df.empty:
    st.subheader("最近 10 日机会/风险/信心趋势")
    plot_df = trend_df.melt(
        id_vars=["date", "sector_name"],
        value_vars=["score", "opportunity_score", "risk_score"],
        var_name="指标",
        value_name="分数",
    )
    plot_df["主线指标"] = plot_df["sector_name"].astype(str) + " · " + plot_df["指标"].astype(str)
    fig2 = px.line(
        plot_df,
        x="date",
        y="分数",
        color="主线指标",
        markers=True,
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.dataframe(
        trend_df[["date", "sector_name", "rank", "score", "opportunity_score", "risk_score", "confidence_score", "lifecycle_stage", "action"]].round(2),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("暂无历史趋势，请连续运行后查看。")

selected_name = st.selectbox("选择主线查看解释", sector_df["board_name"].tolist())
selected = sector_df[sector_df["board_name"] == selected_name].iloc[0]

st.subheader(f"{selected_name} 生命周期解释")
metric_cols = st.columns(4)
metric_cols[0].metric("生命周期", selected.get("lifecycle_state", ""))
metric_cols[1].metric("当前建议", selected.get("lifecycle_recommendation", ""))
metric_cols[2].metric("综合分", f"{selected.get('score', 0):.1f}")
metric_cols[3].metric("今日 Action", selected.get("action", ""))
score_cols = st.columns(4)
score_cols[0].metric("机会分", f"{selected.get('opportunity_score', 0):.1f}")
score_cols[1].metric("风险分", f"{selected.get('risk_score', 0):.1f}")
score_cols[2].metric("信心指数", f"{selected.get('confidence_score', 0):.1f}")
score_cols[3].metric("阶段持续", f"{selected.get('stage_days', 0)} 天")
st.info(selected.get("lifecycle_explanation", "暂无解释。"))
with st.expander("为什么是这个分数", expanded=True):
    explanation = selected.get("score_explanation", [])
    if not isinstance(explanation, list):
        explanation = [str(explanation)]
    for item in explanation:
        st.write(f"- {item}")

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
