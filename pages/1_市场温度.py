"""市场温度页面。"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from config import EMPTY_HINT, INDEX_SYMBOLS
from src.data_provider import get_provider
from src.scoring import score_market_temperature


st.set_page_config(page_title="市场温度", layout="wide")
st.title("市场温度")


@st.cache_data(ttl=900, show_spinner=False)
def load_market():
    """加载市场温度页面数据。"""
    provider = get_provider()
    market_df = provider.get_market_quotes()
    index_df = provider.get_index_quotes(INDEX_SYMBOLS)
    temperature = score_market_temperature(market_df, index_df)
    return market_df, index_df, temperature


with st.spinner("正在读取全市场行情..."):
    market_df, index_df, temperature = load_market()

metrics = temperature.get("metrics", {})
cols = st.columns(5)
cols[0].metric("温度", f"{temperature.get('score', 0)} / 100")
cols[1].metric("风险偏好", temperature.get("risk_preference", "未知"))
cols[2].metric("上涨家数", metrics.get("up_count", 0))
cols[3].metric("下跌家数", metrics.get("down_count", 0))
cols[4].metric("成交额", f"{metrics.get('total_amount_yi', 0):,.0f} 亿")
st.info(temperature.get("explanation", EMPTY_HINT))

left, right = st.columns([1, 1])
with left:
    st.subheader("主要指数涨跌幅")
    if index_df.empty:
        st.warning(EMPTY_HINT)
    else:
        fig = px.bar(index_df, x="index_name", y="change_pct", text="change_pct")
        fig.update_traces(texttemplate="%{text:.2f}%")
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)
with right:
    st.subheader("全市场涨跌幅分布")
    if market_df.empty:
        st.warning(EMPTY_HINT)
    else:
        fig = px.histogram(market_df, x="change_pct", nbins=80)
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)

st.subheader("大成交额股票")
if market_df.empty:
    st.warning(EMPTY_HINT)
else:
    cols = ["code", "name", "industry", "price", "change_pct", "amount_yi", "turnover_pct", "vol_ratio", "mcap_yi"]
    st.dataframe(market_df.sort_values("amount_yi", ascending=False)[cols].head(50).round(2), use_container_width=True, hide_index=True)

