"""A股主线雷达全局配置。"""

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "radar.db"
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


INDEX_SYMBOLS = {
    "上证指数": "sh000001",
    "沪深300": "sh000300",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
    "科创50": "sh000688",
}


EMPTY_HINT = "该数据源暂不可用"
