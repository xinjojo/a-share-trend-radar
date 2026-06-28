"""A-Share Trend Radar / A股主线雷达 Streamlit 首页。"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT, INDEX_SYMBOLS
from src.data_provider import get_provider
from src.operating_system import build_operating_system
from src.report_generator import generate_daily_report
from src.scoring import score_market_temperature
from src.sector_radar import build_sector_radar
from src.stock_radar import build_leader_pool
from src.utils import today_str


st.set_page_config(page_title="A股主线雷达", layout="wide")


@st.cache_data(ttl=900, show_spinner=False)
def load_dashboard(include_concepts: bool, max_boards: int):
    """首页数据加载。"""
    provider = get_provider()
    market_df = provider.get_market_quotes()
    index_df = provider.get_index_quotes(INDEX_SYMBOLS)
    temperature = score_market_temperature(market_df, index_df)
    sector_pack = build_sector_radar(provider, max_boards=max_boards, include_concepts=include_concepts)
    leader_df = build_leader_pool(provider, sector_pack["all"])
    ops = build_operating_system(temperature, sector_pack["all"], leader_df, report_date=today_str(), persist=True)
    sector_pack["all"] = ops["sectors"]
    if not ops["sectors"].empty and "board_layer" in ops["sectors"].columns:
        sector_pack["industry"] = ops["sectors"][ops["sectors"]["board_layer"] == "industry"].reset_index(drop=True)
        sector_pack["concept"] = ops["sectors"][ops["sectors"]["board_layer"] == "concept"].reset_index(drop=True)
        sector_pack["持续主线"] = ops["sectors"][ops["sectors"]["category"] == "持续主线"].reset_index(drop=True)
        sector_pack["短线热点"] = ops["sectors"][ops["sectors"]["category"] == "短线热点"].reset_index(drop=True)
        sector_pack["退潮板块"] = ops["sectors"][ops["sectors"]["category"] == "退潮板块"].reset_index(drop=True)
    return market_df, index_df, temperature, sector_pack, leader_df, ops


st.title("A股主线雷达")
st.caption("研究辅助工具：扫描资金活跃、趋势较强、值得继续研究的行业主线和代表性股票池。结果不构成投资建议。")

with st.sidebar:
    st.header("扫描设置")
    include_concepts = st.toggle("包含概念板块", value=True)
    max_boards = st.slider("板块扫描数量", min_value=8, max_value=30, value=BOARD_ANALYSIS_LIMIT, step=2)
    st.caption("首次运行需要请求公开接口；缓存命中后会明显加快。")

with st.spinner("正在扫描市场温度、主线板块和股票池..."):
    market_df, index_df, temperature, sector_pack, leader_df, ops = load_dashboard(include_concepts, max_boards)

metrics = temperature.get("metrics", {})
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("市场温度", f"{temperature.get('score', 0)} / 100", temperature.get("risk_preference", "未知"))
col2.metric("统计股票数", f"{metrics.get('sample_count', metrics.get('total', 0))}")
col3.metric("上涨/下跌", f"{metrics.get('up_count', 0)} / {metrics.get('down_count', 0)}")
col4.metric("涨停/跌停", f"{metrics.get('limit_up', 0)} / {metrics.get('limit_down', 0)}")
col5.metric("成交额", f"{metrics.get('total_amount_yi', 0):,.0f} 亿")
st.info(temperature.get("explanation", EMPTY_HINT))
if not metrics.get("is_full_market_sample", True):
    st.warning(metrics.get("sample_note", "非全市场样本"))

data_date = today_str()
if not leader_df.empty and "last_trade_date" in leader_df.columns:
    dates = leader_df["last_trade_date"].dropna().astype(str)
    dates = dates[dates != ""]
    if not dates.empty:
        data_date = dates.max()
price_basis_text = "不复权"
if not leader_df.empty and "price_basis" in leader_df.columns:
    basis = [item for item in leader_df["price_basis"].dropna().astype(str).unique().tolist() if item]
    price_basis_text = " / ".join(basis) if basis else "不复权"
fund_basis_text = "成交活跃度代理评分"
sector_df = sector_pack["all"]
if sector_df is not None and not sector_df.empty and "flow_score_label" in sector_df.columns:
    labels = [item for item in sector_df["flow_score_label"].dropna().astype(str).unique().tolist() if item]
    fund_basis_text = " / ".join(labels) if labels else fund_basis_text
st.caption(
    f"数据口径：数据日期 {data_date}；股票池范围：{metrics.get('sample_note', '全市场样本')}，"
    "龙头池来自强势行业/概念成分股并按股票代码去重；"
    f"价格口径：{price_basis_text} 最新日K收盘价，实时行情仅用于偏差校验；"
    f"资金口径：{fund_basis_text}。"
)

st.subheader("今日一句话")
st.info(ops.get("one_liner", "主线数据不足，先观察数据源状态。"))

st.subheader("今日 Action")
action_cols = st.columns(4)
for col, label in zip(action_cols, ["重点研究", "等回调", "只观察 / 不追", "回避"], strict=False):
    with col:
        st.markdown(f"**{label}**")
        rows = ops.get("actions", {}).get(label, [])
        if not rows:
            st.caption("暂无")
        for row in rows:
            st.write(f"{row.get('board_name', '')}")
            st.caption(
                f"{row.get('reason', '')} | 综合 {row.get('score', '')} / "
                f"机会 {row.get('opportunity_score', '')} / 风险 {row.get('risk_score', '')} / 信心 {row.get('confidence_score', '')}"
            )

st.subheader("今日变化")
changes = ops.get("changes", {})
if not changes.get("history_available"):
    st.warning(changes.get("message", "暂无昨日数据，请连续运行后查看变化。"))
else:
    st.caption(f"对比日期：{changes.get('previous_date')}")
    ch1, ch2, ch3 = st.columns(3)
    ch1.write("新增主线")
    ch1.caption("、".join(changes.get("new_sectors", [])[:6]) or "暂无")
    ch2.write("退出主线")
    ch2.caption("、".join(changes.get("exited_sectors", [])[:6]) or "暂无")
    ch3.write("生命周期变化")
    ch3.caption("；".join(item.get("text", "") for item in changes.get("lifecycle_changes", [])[:4]) or "暂无")
    ch4, ch5, ch6 = st.columns(3)
    ch4.write("评分上升最多")
    ch4.dataframe(changes.get("score_gainers", []), use_container_width=True, hide_index=True)
    ch5.write("评分下降最多")
    ch5.dataframe(changes.get("score_losers", []), use_container_width=True, hide_index=True)
    ch6.write("龙头切换")
    ch6.caption("；".join(item.get("text", "") for item in changes.get("leader_switches", [])[:4]) or "暂无")

idx_col, dist_col = st.columns([1, 1])
with idx_col:
    st.subheader("主要指数")
    if index_df.empty:
        st.warning(EMPTY_HINT)
    else:
        st.dataframe(
            index_df[["index_name", "price", "change_pct", "amount_yi", "turnover_pct"]].round(2),
            use_container_width=True,
            hide_index=True,
        )
with dist_col:
    st.subheader("涨跌幅分布")
    if market_df.empty:
        st.warning(EMPTY_HINT)
    else:
        fig = px.histogram(market_df, x="change_pct", nbins=60, labels={"change_pct": "涨跌幅(%)"})
        fig.update_layout(height=280, margin=dict(l=10, r=10, t=25, b=10))
        st.plotly_chart(fig, use_container_width=True)

st.subheader("今日最强主线 Top 10")
if sector_df.empty:
    st.warning(EMPTY_HINT)
else:
    show_cols = [
        "rank",
        "board_name",
        "board_layer",
        "category",
        "action",
        "lifecycle_state",
        "score",
        "opportunity_score",
        "risk_score",
        "confidence_score",
        "rank_stability_score",
        "flow_score_label",
        "flow_score",
        "change_pct",
        "ret_5d",
        "ret_10d",
        "amount_ratio_20",
        "up_ratio",
        "leader",
    ]
    st.dataframe(sector_df[show_cols].round(2).head(10), use_container_width=True, hide_index=True)

st.subheader("主线状态")
tab1, tab2, tab3, tab4, tab5 = st.tabs(["持续主线", "短线热点", "退潮板块", "行业/概念分层", "短线情绪观察"])
for tab, name in [(tab1, "持续主线"), (tab2, "短线热点"), (tab3, "退潮板块")]:
    with tab:
        data = sector_pack[name]
        if data.empty:
            st.caption("暂无板块进入该分类。")
        else:
            st.dataframe(
                data[
                    [
                        "board_name",
                        "board_layer",
                        "lifecycle_state",
                        "lifecycle_recommendation",
                        "score",
                        "rank_stability_score",
                        "change_pct",
                        "ret_5d",
                        "ret_10d",
                        "top_stocks",
                    ]
                ].round(2),
                use_container_width=True,
                hide_index=True,
            )
with tab4:
    layer = st.radio("分层", ["行业板块", "概念板块"], horizontal=True)
    data = sector_pack["industry"] if layer == "行业板块" else sector_pack["concept"]
    if data.empty:
        st.caption("暂无数据。")
    else:
        st.dataframe(data[show_cols].round(2), use_container_width=True, hide_index=True)
with tab5:
    emotion_df = sector_pack.get("emotion")
    if emotion_df is None or emotion_df.empty:
        st.caption("暂无短线情绪标签。")
    else:
        cols = ["board_name", "change_pct", "amount_yi", "up_count", "down_count", "leader", "emotion_reason"]
        st.dataframe(emotion_df[[c for c in cols if c in emotion_df.columns]].round(2), use_container_width=True, hide_index=True)

st.subheader("今日可研究股票池")
stock_groups = ops.get("stock_groups", {})
if not stock_groups:
    st.warning(EMPTY_HINT)
else:
    show_cols = [
        "stock_research_group",
        "code",
        "name",
        "board_name",
        "matched_lifecycle",
        "matched_action",
        "leader_score",
        "research_priority_score",
        "sector_category",
        "price",
        "price_basis",
        "current_price",
        "price_check_diff_pct",
        "change_pct",
        "amount_yi",
        "ret_20d",
        "ret_60d",
        "close",
        "ma20",
        "distance_ma20_pct",
        "trend_status",
        "observe_status",
        "stock_group_reason",
        "price_check_status",
        "price_check_detail",
    ]
    group_names = ["可研究候选", "强主线回调观察", "等待回调", "高位观察/不追", "回避"]
    pool_tabs = st.tabs(group_names)
    for tab, name in zip(pool_tabs, group_names, strict=False):
        with tab:
            data = stock_groups.get(name)
            if data is None or data.empty:
                st.caption("暂无符合条件的股票。")
            else:
                cols = [col for col in show_cols if col in data.columns]
                st.dataframe(data[cols].round(2), use_container_width=True, hide_index=True)

trend_df = ops.get("history_trends")
if trend_df is not None and not trend_df.empty:
    st.subheader("最近 10 日主线趋势")
    st.dataframe(
        trend_df[["date", "sector_name", "rank", "score", "opportunity_score", "risk_score", "confidence_score", "lifecycle_stage", "action"]].round(2),
        use_container_width=True,
        hide_index=True,
    )

with st.expander("生成今日 Markdown 日报", expanded=False):
    report = generate_daily_report(temperature, sector_df, leader_df, ops_summary=ops)
    st.download_button("下载日报 Markdown", report, file_name="A股主线雷达日报.md", mime="text/markdown")
    st.code(report[:4000], language="markdown")
