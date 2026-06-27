"""A-Share Trend Radar / A股主线雷达 Streamlit 首页。"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, EMPTY_HINT, INDEX_SYMBOLS
from src.data_provider import get_provider
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
    return market_df, index_df, temperature, sector_pack, leader_df


st.title("A股主线雷达")
st.caption("研究辅助工具：扫描资金活跃、趋势较强、值得继续研究的行业主线和代表性股票池。结果不构成投资建议。")

with st.sidebar:
    st.header("扫描设置")
    include_concepts = st.toggle("包含概念板块", value=True)
    max_boards = st.slider("板块扫描数量", min_value=8, max_value=30, value=BOARD_ANALYSIS_LIMIT, step=2)
    st.caption("首次运行需要请求公开接口；缓存命中后会明显加快。")

with st.spinner("正在扫描市场温度、主线板块和股票池..."):
    market_df, index_df, temperature, sector_pack, leader_df = load_dashboard(include_concepts, max_boards)

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
        "score",
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
                data[["board_name", "board_layer", "score", "rank_stability_score", "change_pct", "ret_5d", "ret_10d", "top_stocks"]].round(2),
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
if leader_df.empty:
    st.warning(EMPTY_HINT)
else:
    show_cols = [
        "pool_group",
        "code",
        "name",
        "board_name",
        "leader_score",
        "research_priority_score",
        "sector_category",
        "price",
        "price_basis",
        "quote_price",
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
        "price_check_status",
    ]
    show_cols = [col for col in show_cols if col in leader_df.columns]
    if "pool_group" not in leader_df.columns:
        leader_df = leader_df.assign(pool_group="高位观察/不适合追")
    research_df = leader_df[leader_df["pool_group"] == "可研究候选"]
    watch_df = leader_df[leader_df["pool_group"] != "可研究候选"]
    pool_tab1, pool_tab2 = st.tabs(["可研究候选", "高位观察/不适合追"])
    with pool_tab1:
        if research_df.empty:
            st.caption("暂无符合克制条件的可研究候选。")
        else:
            st.dataframe(research_df[show_cols].round(2), use_container_width=True, hide_index=True)
    with pool_tab2:
        if watch_df.empty:
            st.caption("暂无高位观察标的。")
        else:
            st.dataframe(watch_df[show_cols].round(2), use_container_width=True, hide_index=True)

with st.expander("生成今日 Markdown 日报", expanded=False):
    report = generate_daily_report(temperature, sector_df, leader_df)
    st.download_button("下载日报 Markdown", report, file_name="A股主线雷达日报.md", mime="text/markdown")
    st.code(report[:4000], language="markdown")
