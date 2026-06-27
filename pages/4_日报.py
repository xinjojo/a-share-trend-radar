"""日报页面。"""

from __future__ import annotations

import streamlit as st

from config import BOARD_ANALYSIS_LIMIT, INDEX_SYMBOLS
from src.data_provider import get_provider
from src.report_generator import generate_daily_report
from src.scoring import score_market_temperature
from src.sector_radar import build_sector_radar
from src.stock_radar import build_leader_pool
from src.utils import today_str


st.set_page_config(page_title="日报", layout="wide")
st.title("A股主线雷达日报")


@st.cache_data(ttl=1200, show_spinner=False)
def load_report_data():
    """加载日报依赖数据。"""
    provider = get_provider()
    market_df = provider.get_market_quotes()
    index_df = provider.get_index_quotes(INDEX_SYMBOLS)
    temperature = score_market_temperature(market_df, index_df)
    radar = build_sector_radar(provider, max_boards=BOARD_ANALYSIS_LIMIT, include_concepts=True)
    leader_df = build_leader_pool(provider, radar["all"])
    return temperature, radar["all"], leader_df


report_date = st.date_input("日报日期").strftime("%Y-%m-%d")
with st.spinner("正在生成 Markdown 日报..."):
    temperature, sector_df, leader_df = load_report_data()
    markdown = generate_daily_report(temperature, sector_df, leader_df, report_date=report_date or today_str())

st.download_button("下载 Markdown", markdown, file_name=f"A股主线雷达日报_{report_date}.md", mime="text/markdown")
st.markdown(markdown)

