"""策略回测页面。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from config import BACKTEST_DEFAULTS, BOARD_ANALYSIS_LIMIT, EMPTY_HINT, INDEX_SYMBOLS
from src.backtest import BacktestParams, run_rotation_backtest
from src.data_provider import get_provider
from src.scoring import score_market_temperature
from src.sector_radar import build_sector_radar
from src.stock_radar import build_leader_pool


st.set_page_config(page_title="策略回测", layout="wide")
st.title("策略回测")
st.caption("MVP 回测用于验证主线轮动规则，不构成投资建议。当前版本存在候选池幸存者偏差。")


@st.cache_data(ttl=1200, show_spinner=False)
def load_backtest_universe():
    """加载回测依赖的当前主线和股票池。"""
    provider = get_provider()
    market_df = provider.get_market_quotes()
    index_df = provider.get_index_quotes(INDEX_SYMBOLS)
    temperature = score_market_temperature(market_df, index_df)
    radar = build_sector_radar(provider, max_boards=BOARD_ANALYSIS_LIMIT, include_concepts=True)
    leader_df = build_leader_pool(provider, radar["all"])
    return temperature, radar["all"], leader_df


with st.spinner("正在加载当前主线和股票池..."):
    temperature, sector_df, leader_df = load_backtest_universe()

if sector_df.empty or leader_df.empty:
    st.warning(EMPTY_HINT)
    st.stop()

with st.sidebar:
    st.header("回测参数")
    end_default = date.today()
    start_default = end_default - timedelta(days=180)
    start_date = st.date_input("开始日期", value=start_default)
    end_date = st.date_input("结束日期", value=end_default)
    market_threshold = st.slider(
        "市场温度阈值",
        min_value=0,
        max_value=100,
        value=int(BACKTEST_DEFAULTS["market_temperature_threshold"]),
    )
    top_sectors = st.slider("主线数量", 1, 5, int(BACKTEST_DEFAULTS["top_sectors"]))
    stocks_per_sector = st.slider("每个主线股票数", 1, 3, int(BACKTEST_DEFAULTS["stocks_per_sector"]))
    ma20_limit = st.slider("MA20 偏离上限", 5, 40, int(BACKTEST_DEFAULTS["ma20_distance_limit"]))
    stop_loss = st.slider("止损比例%", -20, -3, int(BACKTEST_DEFAULTS["stop_loss_pct"]))
    enable_trailing = st.toggle("启用移动止盈", value=bool(BACKTEST_DEFAULTS["enable_trailing_stop"]))
    trailing_stop = st.slider("移动止盈回撤%", 3, 25, int(BACKTEST_DEFAULTS["trailing_stop_pct"]))
    take_profit = st.slider("固定止盈%", 5, 60, int(BACKTEST_DEFAULTS["take_profit_pct"]))
    max_holding_days = st.slider("最大持仓天数", 3, 60, int(BACKTEST_DEFAULTS["max_holding_days"]))
    run_button = st.button("运行回测", type="primary")

params = BacktestParams(
    market_temperature_threshold=float(market_threshold),
    top_sectors=int(top_sectors),
    stocks_per_sector=int(stocks_per_sector),
    max_positions=int(top_sectors * stocks_per_sector),
    ma20_distance_limit=float(ma20_limit),
    stop_loss_pct=float(stop_loss),
    take_profit_pct=float(take_profit),
    trailing_stop_pct=float(trailing_stop),
    enable_trailing_stop=enable_trailing,
    max_holding_days=int(max_holding_days),
)

st.subheader("当前回测宇宙")
col1, col2, col3 = st.columns(3)
col1.metric("当前市场温度", f"{temperature.get('score', 0)} / 100", temperature.get("risk_preference", ""))
col2.metric("主线数量", len(sector_df))
col3.metric("股票池数量", len(leader_df))

if run_button:
    with st.spinner("正在运行回测，首次需要拉取指数和个股历史 K 线..."):
        result = run_rotation_backtest(
            get_provider(),
            sector_df,
            leader_df,
            start_date=str(start_date),
            end_date=str(end_date),
            params=params,
            save=True,
        )

    for warning in result.get("warnings", []):
        st.warning(warning)

    metrics = result["metrics"]
    metric_cols = st.columns(4)
    metric_items = list(metrics.items())
    for index, (name, value) in enumerate(metric_items):
        metric_cols[index % 4].metric(name, value)

    equity = result["equity_curve"]
    drawdown = result["drawdown_curve"]
    benchmarks = result["benchmarks"]
    trades = result["trades"]
    annual = result["annual_returns"]

    st.subheader("收益曲线")
    if equity.empty:
        st.info("回测没有形成有效净值曲线。")
    else:
        curve = equity[["date", "equity"]].copy()
        curve["series"] = "策略"
        curve = curve.rename(columns={"equity": "value"})
        compare = pd.concat([curve, benchmarks.rename(columns={"benchmark": "series"})[["date", "series", "value"]]], ignore_index=True) if not benchmarks.empty else curve
        fig = px.line(compare, x="date", y="value", color="series", labels={"value": "净值/权益", "date": "日期"})
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("回撤曲线")
    if drawdown.empty:
        st.info("暂无回撤数据。")
    else:
        fig = px.area(drawdown, x="date", y="drawdown_pct", labels={"drawdown_pct": "回撤%", "date": "日期"})
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("交易明细")
    if trades.empty:
        st.info("本区间没有触发交易。")
    else:
        show_cols = [
            "status",
            "code",
            "name",
            "board_name",
            "entry_signal_date",
            "entry_date",
            "entry_price",
            "exit_date",
            "exit_price",
            "pnl_pct",
            "holding_days",
            "exit_reason",
        ]
        st.dataframe(trades[[c for c in show_cols if c in trades.columns]].round(2), use_container_width=True, hide_index=True)

    st.subheader("年度收益表")
    if annual.empty:
        st.info("暂无年度收益数据。")
    else:
        st.dataframe(annual, use_container_width=True, hide_index=True)
else:
    st.info("设置参数后点击“运行回测”。")
