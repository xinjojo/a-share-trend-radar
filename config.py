"""A股主线雷达全局配置。"""

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "radar.db"
HISTORY_DB_PATH = DATA_DIR / "radar_history.db"
LOG_PATH = CACHE_DIR / "radar.log"

for path in (DATA_DIR, CACHE_DIR):
    path.mkdir(parents=True, exist_ok=True)


# 文件缓存时间，避免 Streamlit 每次刷新都重新请求公开接口。
CACHE_TTL_SECONDS = 30 * 60
MARKET_CACHE_TTL_SECONDS = 10 * 60
HISTORY_CACHE_TTL_SECONDS = 6 * 60 * 60


# 遵循 a-stock-data SKILL.md：东财接口串行限流，至少 1 秒加抖动。
EM_MIN_INTERVAL = 1.05
REQUEST_TIMEOUT = 15


# MVP 默认扫描规模。全市场行情必须覆盖沪深京主要 A 股；东财 clist 单页通常最多 100 条。
MARKET_MAX_PAGES = 80
MARKET_PAGE_SIZE = 100
FULL_MARKET_MIN_COUNT = 4500
BOARD_ANALYSIS_LIMIT = 16
LEADER_SECTOR_LIMIT = 8
LEADER_CANDIDATES_PER_SECTOR = 8
LEADER_STOCKS_PER_SECTOR = 5


# V2 生命周期默认规则。页面可覆盖这些阈值，但规则集中维护在配置与 scoring/lifecycle 模块。
LIFECYCLE_RULES = {
    "startup_score_min": 55,
    "main_rise_score_min": 68,
    "climax_ret_10d": 15,
    "climax_ret_20d": 25,
    "climax_distance_ma20": 20,
    "climax_amount_ratio": 2.0,
    "climax_limit_up_count": 4,
    "divergence_up_ratio": 0.45,
    "divergence_high_open_low_close_count": 2,
    "divergence_volume_stall_count": 2,
    "fading_ret_5d": -3,
    "repair_score_min": 45,
    "distance_near_ma20": 12,
}


# V2 回测默认参数。回测页面会把这些值作为初始值。
BACKTEST_DEFAULTS = {
    "initial_cash": 1_000_000,
    "market_temperature_threshold": 50,
    "top_sectors": 3,
    "stocks_per_sector": 1,
    "max_positions": 3,
    "ma20_distance_limit": 25,
    "stop_loss_pct": -8,
    "take_profit_pct": 25,
    "trailing_stop_pct": 10,
    "enable_trailing_stop": True,
    "max_holding_days": 20,
    "exit_if_not_profitable_after_days": 10,
    "execution_price": "close",
}


# 每日主线操作系统默认规则。
OPERATING_SYSTEM_RULES = {
    "focus_opportunity_min": 62,
    "focus_risk_max": 52,
    "wait_pullback_risk_min": 52,
    "avoid_risk_min": 72,
    "high_distance_ma20": 20,
    "extreme_distance_ma20": 35,
    "min_market_temperature_for_focus": 45,
    "history_confidence_days": 10,
    "full_confidence_days": 20,
}


INDEX_SYMBOLS = {
    "上证指数": "sh000001",
    "沪深300": "sh000300",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
    "科创50": "sh000688",
}


EMPTY_HINT = "该数据源暂不可用"
