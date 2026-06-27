"""通用工具函数。"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Any

import pandas as pd

from config import LOG_PATH


def setup_logger(name: str = "a_share_trend_radar") -> logging.Logger:
    """初始化文件日志，所有数据异常都写入 data/cache/radar.log。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def safe_float(value: Any, default: float = 0.0) -> float:
    """把接口里的 '-', None, 空字符串等安全转成 float。"""
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if value in {"", "-", "--", "None", "nan"}:
            return default
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """安全转 int。"""
    try:
        return int(safe_float(value, default))
    except Exception:
        return default


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """把分数限制在指定区间。"""
    return max(low, min(high, value))


def normalize_code(code: str) -> str:
    """把 sh600519、600519.SH 等格式统一成 6 位代码。"""
    if code is None:
        return ""
    text = str(code).strip().lower()
    text = text.replace(".sh", "").replace(".sz", "").replace(".bj", "")
    text = text.replace("sh", "").replace("sz", "").replace("bj", "")
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else text.zfill(6)[-6:]


def get_prefix(code: str) -> str:
    """按 A 股代码判断市场前缀。"""
    code = normalize_code(code)
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("8", "4")):
        return "bj"
    return "sz"


def eastmoney_market_id(code: str) -> int:
    """东财 secid 市场编号：沪市 1，其余 0。"""
    return 1 if normalize_code(code).startswith(("6", "9")) else 0


def tencent_symbol(code: str) -> str:
    """生成腾讯行情 symbol，支持已带 sh/sz/bj 的指数代码。"""
    text = str(code).strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        return text
    return get_prefix(text) + normalize_code(text)


def infer_limit_threshold(code: str) -> float:
    """按板块粗略判断涨跌停幅度，用于无法直接取得涨跌停价时的估算。"""
    code = normalize_code(code)
    if code.startswith(("8", "4")):
        return 29.5
    if code.startswith(("300", "301", "688", "689")):
        return 19.5
    return 9.5


def add_limit_flags(df: pd.DataFrame) -> pd.DataFrame:
    """根据涨跌幅估算涨停/跌停标记，作为公开接口不可用时的兜底。"""
    if df.empty or "change_pct" not in df.columns or "code" not in df.columns:
        return df
    out = df.copy()
    thresholds = out["code"].map(infer_limit_threshold)
    out["is_limit_up"] = out["change_pct"].astype(float) >= thresholds
    out["is_limit_down"] = out["change_pct"].astype(float) <= -thresholds
    out["limit_source"] = "derived_from_change_pct"
    return out


def empty_df(columns: list[str] | None = None) -> pd.DataFrame:
    """返回带指定列的空 DataFrame，页面可稳定处理。"""
    return pd.DataFrame(columns=columns or [])


def today_str() -> str:
    """当前日期字符串。"""
    return datetime.now().strftime("%Y-%m-%d")


def format_yi(value: float) -> str:
    """格式化亿元。"""
    return f"{safe_float(value):,.1f} 亿"


def pct_text(value: float) -> str:
    """格式化百分比。"""
    return f"{safe_float(value):.2f}%"

