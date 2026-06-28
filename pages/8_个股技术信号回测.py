"""个股技术信号回测页面。"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from config import EMPTY_HINT
from src.data_provider import get_provider
from src.technical_backtest import (
    TechnicalBacktestParams,
    generate_technical_backtest_report,
    run_technical_signal_backtest,
)
from src.utils import normalize_code


st.set_page_config(page_title="个股技术信号回测", layout="wide")
st.title("个股技术信号回测")
st.caption("V3 第一版只验证个股技术形态后的收益分布，不回测系统生成的历史主线 Action。")


@st.cache_data(ttl=900, show_spinner=False)
def load_market_universe() -> pd.DataFrame:
    """加载全市场行情，用于默认股票池和名称映射。"""
    provider = get_provider()
    return provider.get_market_quotes()


market_df = load_market_universe()
if market_df.empty:
    st.warning(EMPTY_HINT)

default_codes = []
if not market_df.empty and {"code", "amount_yi"}.issubset(market_df.columns):
    default_codes = (
        market_df.sort_values("amount_yi", ascending=False)["code"]
        .dropna()
        .astype(str)
        .head(12)
        .tolist()
    )

with st.sidebar:
    st.header("回测设置")
    end_default = date.today()
    start_default = end_default - timedelta(days=365)
    start_date = st.date_input("开始日期", value=start_default)
    end_date = st.date_input("结束日期", value=end_default)
    max_codes = st.slider("最多股票数", 1, 50, min(max(len(default_codes), 12), 30))
    history_limit = st.slider("历史K线根数", 180, 1500, 900, step=60)
    hot_distance = st.slider("高位过热距MA20阈值", 15, 50, 25)
    hot_ret20 = st.slider("高位过热20日涨幅阈值", 15, 80, 35)
    run_button = st.button("运行技术信号回测", type="primary")

st.info(
    "信号包括：MA多头排列、距MA20偏离分层、缩量回踩MA5/MA10/MA20、放量反包、跌破MA20、高位过热。"
    " 信号只使用当日及以前数据，统计之后 1/3/5/10/20 个交易日收益。"
)

manual_codes = st.text_area(
    "股票代码",
    value="\n".join(default_codes),
    height=150,
    help="可输入 600519、sh600519、000001.SZ 等格式，逗号、空格或换行分隔。",
)
codes: list[str] = []
for item in re.split(r"[\s,，;；]+", manual_codes.strip()):
    code = normalize_code(item)
    if code and code not in codes:
        codes.append(code)
codes = codes[:max_codes]

name_map = {}
if not market_df.empty and {"code", "name"}.issubset(market_df.columns):
    name_map = dict(zip(market_df["code"].astype(str), market_df["name"].astype(str), strict=False))

st.subheader("样本股票")
if not codes:
    st.warning("请输入至少一个有效股票代码。")
else:
    preview = pd.DataFrame({"code": codes, "name": [name_map.get(code, "") for code in codes]})
    st.dataframe(preview, use_container_width=True, hide_index=True)

if run_button:
    if not codes:
        st.stop()
    params = TechnicalBacktestParams(
        start_date=str(start_date),
        end_date=str(end_date),
        history_limit=int(history_limit),
        max_codes=int(max_codes),
        hot_distance_ma20=float(hot_distance),
        hot_ret20=float(hot_ret20),
    )
    with st.spinner("正在拉取个股历史 K 线并统计信号收益分布..."):
        result = run_technical_signal_backtest(get_provider(), codes, params, name_map=name_map, save=True)

    for warning in result.get("warnings", []):
        st.warning(warning)

    summary = result.get("summary", pd.DataFrame())
    events = result.get("events", pd.DataFrame())
    history_status = result.get("history_status", pd.DataFrame())
    report_md = generate_technical_backtest_report(result)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("有效事件数", len(events))
    m2.metric("信号类型数", summary["signal"].nunique() if not summary.empty else 0)
    m3.metric("股票数", len(codes))
    m4.metric("Run ID", str(result.get("run_id", ""))[:8])

    st.subheader("K线加载状态")
    if history_status.empty:
        st.info("暂无状态。")
    else:
        st.dataframe(history_status, use_container_width=True, hide_index=True)

    st.subheader("信号收益摘要")
    if summary.empty:
        st.info("本区间没有形成有效信号事件。")
    else:
        st.dataframe(summary.round(2), use_container_width=True, hide_index=True)
        left, right = st.columns(2)
        with left:
            fig = px.bar(
                summary,
                x="signal",
                y="avg_return_pct",
                color="horizon",
                barmode="group",
                labels={"signal": "信号", "avg_return_pct": "平均收益%", "horizon": "持有天数"},
            )
            fig.update_layout(height=420, xaxis_tickangle=-35)
            st.plotly_chart(fig, use_container_width=True)
        with right:
            fig = px.line(
                summary,
                x="horizon",
                y="win_rate_pct",
                color="signal",
                markers=True,
                labels={"horizon": "持有天数", "win_rate_pct": "胜率%"},
            )
            fig.update_layout(height=420)
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("收益分布")
    if events.empty:
        st.info("暂无事件明细。")
    else:
        fig = px.box(
            events,
            x="signal",
            y="forward_return_pct",
            color="horizon",
            points=False,
            labels={"signal": "信号", "forward_return_pct": "未来收益%", "horizon": "持有天数"},
        )
        fig.update_layout(height=460, xaxis_tickangle=-35)
        st.plotly_chart(fig, use_container_width=True)

        show_cols = [
            "code",
            "name",
            "signal",
            "signal_date",
            "horizon",
            "target_date",
            "entry_close",
            "target_close",
            "forward_return_pct",
            "max_drawdown_pct",
            "distance_ma20_pct",
            "volume_ratio_20",
            "ret_20d",
            "data_source",
            "price_basis",
        ]
        st.dataframe(events[[c for c in show_cols if c in events.columns]].round(2), use_container_width=True, hide_index=True)

    st.subheader("导出报告")
    csv_summary = summary.to_csv(index=False).encode("utf-8-sig") if isinstance(summary, pd.DataFrame) else b""
    csv_events = events.to_csv(index=False).encode("utf-8-sig") if isinstance(events, pd.DataFrame) else b""
    dl1, dl2, dl3 = st.columns(3)
    dl1.download_button("下载信号摘要 CSV", csv_summary, file_name="technical_signal_summary.csv", mime="text/csv")
    dl2.download_button("下载事件明细 CSV", csv_events, file_name="technical_signal_events.csv", mime="text/csv")
    dl3.download_button("下载 Markdown 报告", report_md, file_name="个股技术信号回测报告.md", mime="text/markdown")
else:
    st.info("确认股票代码和参数后，点击“运行技术信号回测”。")
