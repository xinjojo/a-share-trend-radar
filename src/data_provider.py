"""统一数据源封装。

数据源优先级遵循 a-stock-data SKILL.md：
1. 行情/K线优先 mootdx、腾讯、百度股市通等直连公开端点；
2. 东财仅用于它独有或更适合批量的数据，并统一走 em_get 串行限流；
3. AKShare 只作为 fallback，不在业务模块直接调用。
"""

from __future__ import annotations

import hashlib
import json
import pickle
import random
import socket
import time
import urllib.request
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests

from config import (
    CACHE_DIR,
    CACHE_TTL_SECONDS,
    EM_MIN_INTERVAL,
    FULL_MARKET_MIN_COUNT,
    HISTORY_CACHE_TTL_SECONDS,
    MARKET_CACHE_TTL_SECONDS,
    MARKET_MAX_PAGES,
    MARKET_PAGE_SIZE,
    REQUEST_TIMEOUT,
)
from src.utils import (
    add_limit_flags,
    eastmoney_market_id,
    empty_df,
    normalize_code,
    safe_float,
    safe_int,
    setup_logger,
    tencent_symbol,
)


logger = setup_logger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CACHE_SCHEMA_VERSION = "2026-06-28-price-basis-and-pool-v1"

EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
_em_last_call = [0.0]


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = REQUEST_TIMEOUT,
    **kwargs: Any,
) -> requests.Response:
    """东财统一请求入口：串行限流 + session 复用 + 默认 UA。"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.45))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


def file_cache(ttl_seconds: int = CACHE_TTL_SECONDS) -> Callable:
    """轻量文件缓存，适合 Streamlit 多页面共享。"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, refresh: bool = False, **kwargs: Any) -> Any:
            key_args = args[1:] if args else args
            payload = json.dumps(
                {"version": CACHE_SCHEMA_VERSION, "func": func.__name__, "args": key_args, "kwargs": kwargs},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            digest = hashlib.md5(payload.encode("utf-8")).hexdigest()
            cache_path = CACHE_DIR / f"{func.__name__}_{digest}.pkl"

            if not refresh and cache_path.exists():
                age = time.time() - cache_path.stat().st_mtime
                if age <= ttl_seconds:
                    try:
                        with cache_path.open("rb") as f:
                            return pickle.load(f)
                    except Exception as exc:
                        logger.warning("读取缓存失败 %s: %s", cache_path.name, exc)

            try:
                result = func(*args, **kwargs)
                with cache_path.open("wb") as f:
                    pickle.dump(result, f)
                return result
            except Exception as exc:
                logger.exception("%s 执行失败: %s", func.__name__, exc)
                if cache_path.exists():
                    try:
                        with cache_path.open("rb") as f:
                            return pickle.load(f)
                    except Exception:
                        pass
                return empty_df()

        return wrapper

    return decorator


_TDX_SERVERS = [
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("116.205.163.254", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("124.71.9.153", 7709),
    ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]


def _probe(ip: str, port: int, timeout: float = 0.8) -> bool:
    """TCP 握手探测通达信服务器。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def tdx_client(market: str = "std"):
    """创建 mootdx 客户端，规避 BESTIP.HQ 空串问题。"""
    try:
        from mootdx.quotes import Quotes
    except Exception as exc:
        raise RuntimeError(f"mootdx 未安装或不可用: {exc}") from exc

    for ip, port in _TDX_SERVERS:
        if _probe(ip, port):
            return Quotes.factory(market=market, server=(ip, port))
    try:
        return Quotes.factory(market=market, bestip=True)
    except Exception:
        pass
    try:
        return Quotes.factory(market=market)
    except Exception as exc:
        raise RuntimeError(f"所有 mootdx 服务器均不可达: {exc}") from exc


class AStockDataProvider:
    """A股数据统一入口，页面和业务模块只能通过本类取数。"""

    def __init__(self) -> None:
        self.logger = logger

    def _akshare(self):
        """延迟导入 AKShare，确保它只作为 fallback。"""
        try:
            import akshare as ak  # type: ignore

            return ak
        except Exception as exc:
            self.logger.warning("AKShare fallback 不可用: %s", exc)
            return None

    def _eastmoney_clist(
        self,
        fs: str,
        fields: str,
        page_size: int = 200,
        max_pages: int = 1,
        sort_columns: str = "f3",
        sort_types: str = "-1",
        return_total: bool = False,
    ) -> list[dict] | tuple[list[dict], int]:
        """东财 clist 通用分页查询。"""
        rows: list[dict] = []
        source_total = 0
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        for page in range(1, max_pages + 1):
            params = {
                "pn": str(page),
                "pz": str(page_size),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": sort_columns,
                "fs": fs,
                "fields": fields,
            }
            if sort_types == "1":
                params["po"] = "0"
            resp = em_get(url, params=params, headers={"User-Agent": UA})
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            diff = data.get("diff") or []
            items = list(diff.values()) if isinstance(diff, dict) else list(diff)
            if not items:
                break
            rows.extend(items)
            total = safe_int(data.get("total"), 0)
            source_total = total or source_total
            if total and len(rows) >= total:
                break
        if return_total:
            return rows, source_total
        return rows

    def _normalize_quote_rows(self, items: list[dict]) -> pd.DataFrame:
        """把东财 clist 行情字段统一为项目标准字段。"""
        rows: list[dict] = []
        for item in items:
            code = str(item.get("f12", "")).strip()
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": item.get("f14", ""),
                    "price": safe_float(item.get("f2")),
                    "change_pct": safe_float(item.get("f3")),
                    "change_amt": safe_float(item.get("f4")),
                    "volume": safe_float(item.get("f5")),
                    "amount_yuan": safe_float(item.get("f6")),
                    "amount_yi": safe_float(item.get("f6")) / 1e8,
                    "turnover_pct": safe_float(item.get("f8")),
                    "pe_ttm": safe_float(item.get("f9") or item.get("f115")),
                    "vol_ratio": safe_float(item.get("f10")),
                    "high": safe_float(item.get("f15")),
                    "low": safe_float(item.get("f16")),
                    "open": safe_float(item.get("f17")),
                    "last_close": safe_float(item.get("f18")),
                    "mcap_yi": safe_float(item.get("f20")) / 1e8,
                    "float_mcap_yi": safe_float(item.get("f21")) / 1e8,
                    "pb": safe_float(item.get("f23")),
                    "industry": item.get("f100", ""),
                    "market_id": safe_int(item.get("f13")),
                    "data_source": "a-stock-data:eastmoney_clist",
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            return empty_df(
                [
                    "code",
                    "name",
                    "price",
                    "change_pct",
                    "amount_yi",
                    "turnover_pct",
                    "vol_ratio",
                    "mcap_yi",
                    "industry",
                    "is_limit_up",
                    "is_limit_down",
                ]
            )
        return add_limit_flags(df)

    def _attach_market_sample_metadata(
        self,
        df: pd.DataFrame,
        source_total: int = 0,
        source_name: str = "a-stock-data:eastmoney_clist",
    ) -> pd.DataFrame:
        """给全市场行情附加样本完整性元数据。"""
        if df is None or df.empty:
            return df
        out = df.copy()
        sample_count = len(out)
        expected_count = max(source_total, FULL_MARKET_MIN_COUNT)
        is_full = sample_count >= min(expected_count, FULL_MARKET_MIN_COUNT)
        note = "全市场样本" if is_full else "非全市场样本"
        if source_total and sample_count < source_total:
            note = f"非全市场样本：数据源声明 {source_total} 只，本次仅返回 {sample_count} 只"
        elif sample_count < FULL_MARKET_MIN_COUNT:
            note = f"非全市场样本：本次仅返回 {sample_count} 只，低于全市场阈值 {FULL_MARKET_MIN_COUNT} 只"
        out["sample_count"] = sample_count
        out["sample_expected_count"] = expected_count
        out["source_total_count"] = source_total
        out["is_full_market_sample"] = bool(is_full)
        out["sample_note"] = note
        out["sample_source"] = source_name
        return out

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_market_quotes(self, max_pages: int = MARKET_MAX_PAGES) -> pd.DataFrame:
        """获取 A 股全市场行情；失败后尝试 AKShare fallback。"""
        try:
            fields = (
                "f2,f3,f4,f5,f6,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
                "f20,f21,f23,f100,f115"
            )
            fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
            rows, source_total = self._eastmoney_clist(
                fs=fs,
                fields=fields,
                page_size=MARKET_PAGE_SIZE,
                max_pages=max_pages,
                sort_columns="f6",
                return_total=True,
            )
            df = self._normalize_quote_rows(rows)
            if not df.empty:
                df = df.sort_values("amount_yi", ascending=False).drop_duplicates("code").reset_index(drop=True)
                return self._attach_market_sample_metadata(df, source_total=source_total)
        except Exception as exc:
            self.logger.exception("a-stock-data 全市场行情失败: %s", exc)
        return self._fallback_ak_market_quotes()

    def _fallback_ak_market_quotes(self) -> pd.DataFrame:
        """AKShare 全市场行情兜底。"""
        ak = self._akshare()
        if ak is None:
            return empty_df()
        try:
            df = ak.stock_zh_a_spot_em()
            mapping = {
                "代码": "code",
                "名称": "name",
                "最新价": "price",
                "涨跌幅": "change_pct",
                "涨跌额": "change_amt",
                "成交量": "volume",
                "成交额": "amount_yuan",
                "换手率": "turnover_pct",
                "量比": "vol_ratio",
                "最高": "high",
                "最低": "low",
                "今开": "open",
                "昨收": "last_close",
                "市盈率-动态": "pe_ttm",
                "市净率": "pb",
                "总市值": "mcap_yuan",
                "流通市值": "float_mcap_yuan",
            }
            df = df.rename(columns=mapping)
            for col in ["amount_yuan", "mcap_yuan", "float_mcap_yuan"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            df["amount_yi"] = df.get("amount_yuan", 0) / 1e8
            df["mcap_yi"] = df.get("mcap_yuan", 0) / 1e8
            df["float_mcap_yi"] = df.get("float_mcap_yuan", 0) / 1e8
            df["data_source"] = "fallback:akshare"
            df = add_limit_flags(df)
            return self._attach_market_sample_metadata(df, source_total=len(df), source_name="fallback:akshare")
        except Exception as exc:
            self.logger.exception("AKShare 全市场行情 fallback 失败: %s", exc)
            return empty_df()

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_index_quotes(self, symbols: dict[str, str]) -> pd.DataFrame:
        """获取主要指数行情，优先腾讯财经。"""
        try:
            raw = self.tencent_quote(list(symbols.values()))
            rows = []
            reverse = {v[-6:]: k for k, v in symbols.items()}
            for code, quote in raw.items():
                rows.append(
                    {
                        "index_name": reverse.get(code, quote.get("name", code)),
                        "code": code,
                        **quote,
                        "data_source": "a-stock-data:tencent_quote",
                    }
                )
            return pd.DataFrame(rows)
        except Exception as exc:
            self.logger.exception("腾讯指数行情失败: %s", exc)
            return empty_df()

    def tencent_quote(self, codes: list[str]) -> dict[str, dict]:
        """腾讯财经批量行情，适合指数和已知个股列表。"""
        if not codes:
            return {}
        prefixed = [tencent_symbol(code) for code in codes]
        url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk", errors="ignore")

        result: dict[str, dict] = {}
        for line in data.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            vals = line.split('"')[1].split("~")
            if len(vals) < 53:
                continue
            code = key[2:]
            result[code] = {
                "name": vals[1],
                "price": safe_float(vals[3]),
                "last_close": safe_float(vals[4]),
                "open": safe_float(vals[5]),
                "change_amt": safe_float(vals[31]),
                "change_pct": safe_float(vals[32]),
                "high": safe_float(vals[33]),
                "low": safe_float(vals[34]),
                "amount_wan": safe_float(vals[37]),
                "amount_yi": safe_float(vals[37]) / 10000,
                "turnover_pct": safe_float(vals[38]),
                "pe_ttm": safe_float(vals[39]),
                "amplitude_pct": safe_float(vals[43]),
                "mcap_yi": safe_float(vals[44]),
                "float_mcap_yi": safe_float(vals[45]),
                "pb": safe_float(vals[46]),
                "limit_up": safe_float(vals[47]),
                "limit_down": safe_float(vals[48]),
                "vol_ratio": safe_float(vals[49]),
                "pe_static": safe_float(vals[52]),
            }
        return result

    @file_cache(ttl_seconds=HISTORY_CACHE_TTL_SECONDS)
    def get_stock_history(self, code: str, limit: int = 120) -> pd.DataFrame:
        """个股历史日 K，优先 mootdx，其次百度股市通，最后 AKShare。"""
        code = normalize_code(code)
        for getter in (
            self._stock_history_mootdx,
            self._stock_history_baidu,
            self._fallback_ak_stock_history,
        ):
            try:
                df = getter(code, limit)
                if not df.empty:
                    return df.tail(limit).reset_index(drop=True)
            except Exception as exc:
                self.logger.warning("%s 获取 %s K线失败: %s", getter.__name__, code, exc)
        return empty_df()

    def _stock_history_mootdx(self, code: str, limit: int = 120) -> pd.DataFrame:
        """mootdx 日 K。"""
        client = tdx_client()
        try:
            raw = client.bars(symbol=code, category=4, offset=0, count=limit)
        except TypeError:
            raw = client.bars(symbol=code, category=4, offset=limit)
        if raw is None or len(raw) == 0:
            return empty_df()
        df = pd.DataFrame(raw).copy()
        rename = {"vol": "volume", "datetime": "date"}
        df = df.rename(columns=rename)
        if "date" not in df.columns and "time" in df.columns:
            df["date"] = df["time"]
        keep = [c for c in ["date", "open", "close", "high", "low", "volume", "amount"] if c in df.columns]
        df = df[keep]
        for col in ["open", "close", "high", "low", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["data_source"] = "a-stock-data:mootdx"
        df["price_basis"] = "不复权"
        df["ma_basis"] = "不复权"
        df["adjustment"] = ""
        return df

    def _stock_history_baidu(self, code: str, limit: int = 120) -> pd.DataFrame:
        """百度股市通 K线兜底，返回自带均线字段。"""
        url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
        params = {
            "all": "1",
            "isIndex": "false",
            "isBk": "false",
            "isBlock": "false",
            "isFutures": "false",
            "isStock": "true",
            "newFormat": "1",
            "group": "quotation_kline_ab",
            "finClientType": "pc",
            "code": code,
            "ktype": "1",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/vnd.finance-web.v1+json",
            "Origin": "https://gushitong.baidu.com",
            "Referer": "https://gushitong.baidu.com/",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        md = (data.get("Result") or {}).get("newMarketData") or {}
        keys = md.get("keys") or []
        rows = [row for row in str(md.get("marketData", "")).split(";") if row]
        parsed = []
        for row in rows:
            parts = row.split(",")
            parsed.append({keys[i]: parts[i] for i in range(min(len(keys), len(parts)))})
        df = pd.DataFrame(parsed)
        if df.empty:
            return empty_df()
        rename = {
            "time": "date",
            "volume": "volume",
            "amount": "amount",
            "ma5avgprice": "ma5",
            "ma10avgprice": "ma10",
            "ma20avgprice": "ma20",
        }
        df = df.rename(columns=rename)
        for col in ["open", "close", "high", "low", "volume", "amount", "ma5", "ma10", "ma20"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df["data_source"] = "a-stock-data:baidu_kline"
        df["price_basis"] = "不复权"
        df["ma_basis"] = "不复权"
        df["adjustment"] = ""
        return df.tail(limit).reset_index(drop=True)

    def _fallback_ak_stock_history(self, code: str, limit: int = 120) -> pd.DataFrame:
        """AKShare 个股 K线兜底。"""
        ak = self._akshare()
        if ak is None:
            return empty_df()
        # 用不复权日线，确保 close/MA 与实时行情价格口径一致，便于人工校验。
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="")
        if df is None or df.empty:
            return empty_df()
        df = df.rename(
            columns={
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "change_pct",
            }
        )
        for col in ["open", "close", "high", "low", "volume", "amount", "change_pct"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df["data_source"] = "fallback:akshare"
        df["price_basis"] = "不复权"
        df["ma_basis"] = "不复权"
        df["adjustment"] = ""
        return df.tail(limit).reset_index(drop=True)

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_industry_boards(self) -> pd.DataFrame:
        """行业板块行情，a-stock-data 东财 clist 端点。"""
        return self._get_board_quotes("industry")

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_concept_boards(self) -> pd.DataFrame:
        """概念板块行情，a-stock-data 东财 clist 端点。"""
        return self._get_board_quotes("concept")

    def _get_board_quotes(self, board_type: str) -> pd.DataFrame:
        """获取行业/概念板块行情；失败后 AKShare fallback。"""
        try:
            fs = "m:90+t:2" if board_type == "industry" else "m:90+t:3"
            fields = "f2,f3,f4,f5,f6,f8,f12,f13,f14,f20,f104,f105,f128,f136,f140,f141"
            rows = self._eastmoney_clist(
                fs=fs,
                fields=fields,
                page_size=200,
                max_pages=2,
                sort_columns="f3",
            )
            parsed = []
            for item in rows:
                parsed.append(
                    {
                        "board_code": item.get("f12", ""),
                        "board_name": item.get("f14", ""),
                        "board_type": board_type,
                        "price": safe_float(item.get("f2")),
                        "change_pct": safe_float(item.get("f3")),
                        "change_amt": safe_float(item.get("f4")),
                        "volume": safe_float(item.get("f5")),
                        "amount_yuan": safe_float(item.get("f6")),
                        "amount_yi": safe_float(item.get("f6")) / 1e8,
                        "turnover_pct": safe_float(item.get("f8")),
                        "up_count": safe_int(item.get("f104")),
                        "down_count": safe_int(item.get("f105")),
                        "leader_code": item.get("f128", ""),
                        "leader_change": safe_float(item.get("f136")),
                        "leader": item.get("f140", "") or item.get("f128", ""),
                        "data_source": f"a-stock-data:eastmoney_{board_type}_clist",
                    }
                )
            df = pd.DataFrame(parsed)
            if not df.empty:
                return df.sort_values("change_pct", ascending=False).reset_index(drop=True)
        except Exception as exc:
            self.logger.exception("a-stock-data 板块行情失败 %s: %s", board_type, exc)
        return self._fallback_ak_boards(board_type)

    def _fallback_ak_boards(self, board_type: str) -> pd.DataFrame:
        """AKShare 板块行情兜底。"""
        ak = self._akshare()
        if ak is None:
            return empty_df()
        try:
            if board_type == "industry":
                df = ak.stock_board_industry_name_em()
            else:
                df = ak.stock_board_concept_name_em()
            df = df.rename(
                columns={
                    "板块代码": "board_code",
                    "板块名称": "board_name",
                    "最新价": "price",
                    "涨跌幅": "change_pct",
                    "涨跌额": "change_amt",
                    "成交额": "amount_yuan",
                    "换手率": "turnover_pct",
                    "上涨家数": "up_count",
                    "下跌家数": "down_count",
                    "领涨股票": "leader",
                    "领涨股票-涨跌幅": "leader_change",
                }
            )
            df["board_type"] = board_type
            df["amount_yi"] = pd.to_numeric(df.get("amount_yuan", 0), errors="coerce").fillna(0) / 1e8
            df["data_source"] = "fallback:akshare"
            return df
        except Exception as exc:
            self.logger.exception("AKShare 板块 fallback 失败 %s: %s", board_type, exc)
            return empty_df()

    @file_cache(ttl_seconds=HISTORY_CACHE_TTL_SECONDS)
    def get_board_history(self, board_code: str, board_name: str = "", limit: int = 90) -> pd.DataFrame:
        """板块历史 K线，优先东财 push2his 板块 K线，失败后 AKShare。"""
        board_code = str(board_code).upper()
        try:
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": f"90.{board_code}",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1",
                "lmt": str(limit),
                "end": "20500101",
            }
            resp = em_get(url, params=params, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
            rows = (resp.json().get("data") or {}).get("klines") or []
            parsed = []
            for line in rows:
                parts = line.split(",")
                if len(parts) >= 11:
                    parsed.append(
                        {
                            "date": parts[0],
                            "open": safe_float(parts[1]),
                            "close": safe_float(parts[2]),
                            "high": safe_float(parts[3]),
                            "low": safe_float(parts[4]),
                            "volume": safe_float(parts[5]),
                            "amount": safe_float(parts[6]),
                            "amount_yi": safe_float(parts[6]) / 1e8,
                            "amplitude_pct": safe_float(parts[7]),
                            "change_pct": safe_float(parts[8]),
                            "change_amt": safe_float(parts[9]),
                            "turnover_pct": safe_float(parts[10]),
                            "data_source": "a-stock-data:eastmoney_board_kline",
                        }
                    )
            df = pd.DataFrame(parsed)
            if not df.empty:
                return df.tail(limit).reset_index(drop=True)
        except Exception as exc:
            self.logger.exception("板块历史K线失败 %s: %s", board_code, exc)
        return self._fallback_ak_board_history(board_name, limit)

    def _fallback_ak_board_history(self, board_name: str, limit: int = 90) -> pd.DataFrame:
        """AKShare 板块历史 K线兜底。"""
        if not board_name:
            return empty_df()
        ak = self._akshare()
        if ak is None:
            return empty_df()
        for func_name in ("stock_board_industry_hist_em", "stock_board_concept_hist_em"):
            try:
                func = getattr(ak, func_name)
                df = func(symbol=board_name)
                if df is None or df.empty:
                    continue
                df = df.rename(
                    columns={
                        "日期": "date",
                        "开盘": "open",
                        "收盘": "close",
                        "最高": "high",
                        "最低": "low",
                        "成交量": "volume",
                        "成交额": "amount",
                        "涨跌幅": "change_pct",
                    }
                )
                df["amount_yi"] = pd.to_numeric(df.get("amount", 0), errors="coerce").fillna(0) / 1e8
                df["data_source"] = "fallback:akshare"
                return df.tail(limit).reset_index(drop=True)
            except Exception:
                continue
        return empty_df()

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_board_constituents(self, board_code: str, board_name: str = "") -> pd.DataFrame:
        """板块成分股强弱排名。"""
        board_code = str(board_code).upper()
        try:
            fields = (
                "f2,f3,f4,f5,f6,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
                "f20,f21,f23,f100,f115"
            )
            rows = self._eastmoney_clist(
                fs=f"b:{board_code} f:!50",
                fields=fields,
                page_size=300,
                max_pages=3,
                sort_columns="f6",
            )
            df = self._normalize_quote_rows(rows)
            if not df.empty:
                df["board_code"] = board_code
                df["board_name"] = board_name
                return df
        except Exception as exc:
            self.logger.exception("板块成分股失败 %s: %s", board_code, exc)
        return self._fallback_ak_board_constituents(board_name)

    def _fallback_ak_board_constituents(self, board_name: str) -> pd.DataFrame:
        """AKShare 成分股 fallback。"""
        if not board_name:
            return empty_df()
        ak = self._akshare()
        if ak is None:
            return empty_df()
        for func_name in ("stock_board_industry_cons_em", "stock_board_concept_cons_em"):
            try:
                func = getattr(ak, func_name)
                df = func(symbol=board_name)
                if df is None or df.empty:
                    continue
                df = df.rename(
                    columns={
                        "代码": "code",
                        "名称": "name",
                        "最新价": "price",
                        "涨跌幅": "change_pct",
                        "涨跌额": "change_amt",
                        "成交量": "volume",
                        "成交额": "amount_yuan",
                        "换手率": "turnover_pct",
                    }
                )
                df["amount_yi"] = pd.to_numeric(df.get("amount_yuan", 0), errors="coerce").fillna(0) / 1e8
                df["data_source"] = "fallback:akshare"
                return add_limit_flags(df)
            except Exception:
                continue
        return empty_df()

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_stock_blocks(self, code: str) -> pd.DataFrame:
        """个股所属行业/概念/地域，来自东财 slist。"""
        code = normalize_code(code)
        params = {
            "fltt": "2",
            "invt": "2",
            "secid": f"{eastmoney_market_id(code)}.{code}",
            "spt": "3",
            "pi": "0",
            "pz": "200",
            "po": "1",
            "fields": "f12,f14,f3,f128",
        }
        headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
        try:
            resp = em_get("https://push2.eastmoney.com/api/qt/slist/get", params=params, headers=headers)
            data = resp.json()
            diff = ((data.get("data") or {}).get("diff")) or {}
            items = diff.values() if isinstance(diff, dict) else diff
            rows = [
                {
                    "board_name": item.get("f14", ""),
                    "board_code": item.get("f12", ""),
                    "change_pct": safe_float(item.get("f3")),
                    "lead_stock": item.get("f128", ""),
                    "data_source": "a-stock-data:eastmoney_slist",
                }
                for item in items
            ]
            return pd.DataFrame(rows)
        except Exception as exc:
            self.logger.exception("个股板块归属失败 %s: %s", code, exc)
            return empty_df()

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_stock_info(self, code: str) -> pd.DataFrame:
        """个股基础信息。"""
        code = normalize_code(code)
        try:
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "fltt": "2",
                "invt": "2",
                "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
                "secid": f"{eastmoney_market_id(code)}.{code}",
            }
            resp = em_get(url, params=params, headers={"User-Agent": UA})
            data = resp.json().get("data") or {}
            row = {
                "code": data.get("f57", code),
                "name": data.get("f58", ""),
                "industry": data.get("f127", ""),
                "total_shares": safe_float(data.get("f84")),
                "float_shares": safe_float(data.get("f85")),
                "mcap_yi": safe_float(data.get("f116")) / 1e8,
                "float_mcap_yi": safe_float(data.get("f117")) / 1e8,
                "list_date": str(data.get("f189", "")),
                "price": safe_float(data.get("f43")),
                "data_source": "a-stock-data:eastmoney_stock_info",
            }
            return pd.DataFrame([row])
        except Exception as exc:
            self.logger.exception("个股基础信息失败 %s: %s", code, exc)
            return empty_df()

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_stock_fund_flow(self, code: str) -> pd.DataFrame:
        """个股 120 日日级资金流。"""
        code = normalize_code(code)
        return self._fund_flow_daykline(f"{eastmoney_market_id(code)}.{code}", "stock")

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_stock_intraday_fund_flow(self, code: str) -> pd.DataFrame:
        """个股分钟级资金流。"""
        code = normalize_code(code)
        secid = f"{eastmoney_market_id(code)}.{code}"
        try:
            url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
            params = {
                "secid": secid,
                "klt": 1,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            }
            headers = {
                "User-Agent": UA,
                "Referer": "https://quote.eastmoney.com/",
                "Origin": "https://quote.eastmoney.com",
            }
            resp = em_get(url, params=params, headers=headers, timeout=10)
            rows = (resp.json().get("data") or {}).get("klines") or []
            parsed = []
            for line in rows:
                parts = line.split(",")
                if len(parts) >= 6:
                    parsed.append(
                        {
                            "time": parts[0],
                            "main_net": safe_float(parts[1]),
                            "small_net": safe_float(parts[2]),
                            "mid_net": safe_float(parts[3]),
                            "large_net": safe_float(parts[4]),
                            "super_net": safe_float(parts[5]),
                            "data_source": "a-stock-data:eastmoney_fund_flow_minute",
                        }
                    )
            return pd.DataFrame(parsed)
        except Exception as exc:
            self.logger.exception("个股分钟资金流失败 %s: %s", code, exc)
            return empty_df()

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_board_fund_flow(self, board_code: str) -> pd.DataFrame:
        """板块资金流。若东财不返回板块资金流，则返回空表。"""
        board_code = str(board_code).upper()
        return self._fund_flow_daykline(f"90.{board_code}", "board")

    def _fund_flow_daykline(self, secid: str, source_kind: str) -> pd.DataFrame:
        """东财 push2his 日级资金流通用解析。"""
        try:
            url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            params = {
                "secid": secid,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "lmt": "120",
            }
            headers = {
                "User-Agent": UA,
                "Referer": "https://quote.eastmoney.com/",
                "Origin": "https://quote.eastmoney.com",
            }
            resp = em_get(url, params=params, headers=headers, timeout=15)
            klines = (resp.json().get("data") or {}).get("klines") or []
            rows = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 6:
                    rows.append(
                        {
                            "date": parts[0],
                            "main_net": safe_float(parts[1]),
                            "small_net": safe_float(parts[2]),
                            "mid_net": safe_float(parts[3]),
                            "large_net": safe_float(parts[4]),
                            "super_net": safe_float(parts[5]),
                            "data_source": f"a-stock-data:eastmoney_{source_kind}_fund_flow",
                        }
                    )
            return pd.DataFrame(rows)
        except Exception as exc:
            self.logger.exception("资金流失败 %s %s: %s", source_kind, secid, exc)
            return empty_df()

    @file_cache(ttl_seconds=MARKET_CACHE_TTL_SECONDS)
    def get_limit_pool(self) -> pd.DataFrame:
        """涨停/跌停池。MVP 使用全市场涨跌幅估算，公开端点不可用时不崩溃。"""
        df = self.get_market_quotes()
        if df.empty:
            return empty_df()
        cols = ["code", "name", "price", "change_pct", "amount_yi", "is_limit_up", "is_limit_down"]
        return df.loc[df["is_limit_up"] | df["is_limit_down"], cols].reset_index(drop=True)


def get_provider() -> AStockDataProvider:
    """页面侧获取统一 provider。"""
    return AStockDataProvider()
