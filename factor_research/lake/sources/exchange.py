"""交易所/互联互通资金面源。

两融按交易日下载沪深个股明细。北向持股来自东财每日个股统计；
若本机代理拦截 eastmoney，会记录失败，等待网络直连规则恢复后断点续传。
"""
from app_config.log import get_logger

logger = get_logger(__name__)

import akshare as ak
import pandas as pd

from lake.base import Fetcher, RateLimiter
from lake.schema import MARGIN_RENAME, NORTHBOUND_RENAME
from lake.sources.registry import register

# RENAME mappings now live in lake.schema; module aliases for brevity.
RENAME = MARGIN_RENAME


def _to_numeric(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@register("margin")
class MarginFetcher(Fetcher):
    """
    个股两融明细，按交易日下载（沪+深合并）。key 是日期字符串 'YYYYMMDD'。
    两融2010-03推出，遍历交易日历下载。
    """
    def __init__(self, out_dir: str = "data_lake/capital/margin", **kw):
        super().__init__(
            name="margin", out_dir=out_dir,
            limiter=RateLimiter(min_interval=0.4, jitter=(0.1, 0.3)),
            max_workers=kw.pop("max_workers", 4), **kw,
        )

    def out_path(self, key: str):
        return self.out_dir / f"{key}.parquet"

    def fetch_one(self, date_str: str):
        frames = []
        for src in (ak.stock_margin_detail_sse, ak.stock_margin_detail_szse):
            try:
                d = src(date=date_str)
                if d is not None and len(d):
                    d = d.rename(columns=RENAME)
                    if "code" in d.columns:
                        d["code"] = d["code"].astype(str).str.zfill(6)
                        frames.append(d)
            except Exception:
                continue
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        df = _to_numeric(
            df,
            ["margin_balance", "margin_buy", "short_balance", "short_vol", "short_sell"],
        )
        df["date"] = pd.to_datetime(date_str)
        keep = ["date", "code", "margin_balance", "margin_buy", "short_balance", "short_vol"]
        return df[[c for c in keep if c in df.columns]]


@register("northbound")
class NorthboundFetcher(Fetcher):
    """北向持股每日个股统计，key 是日期字符串 'YYYYMMDD'。"""

    def __init__(self, out_dir: str = "data_lake/capital/northbound", **kw):
        super().__init__(
            name="northbound", out_dir=out_dir,
            limiter=RateLimiter(min_interval=1.0, jitter=(0.3, 0.8)),
            max_workers=kw.pop("max_workers", 1), **kw,
        )

    def out_path(self, key: str):
        return self.out_dir / f"{key}.parquet"

    def fetch_one(self, date_str: str):
        df = ak.stock_hsgt_stock_statistics_em(
            symbol="北向持股",
            start_date=date_str,
            end_date=date_str,
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns=NORTHBOUND_RENAME)
        if "code" not in df.columns:
            return None
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["date"] = pd.to_datetime(df["date"])
        df = _to_numeric(df, [
            "close", "pct_chg", "northbound_hold_shares", "northbound_hold_value",
            "northbound_hold_pct", "northbound_value_chg_1d",
            "northbound_value_chg_5d", "northbound_value_chg_10d",
        ])
        keep = [
            "date", "code", "northbound_hold_shares", "northbound_hold_value",
            "northbound_hold_pct", "northbound_value_chg_1d",
            "northbound_value_chg_5d", "northbound_value_chg_10d",
        ]
        return df[[c for c in keep if c in df.columns]]


@register("northbound_stock")
class NorthboundIndividualFetcher(Fetcher):
    """北向单股完整持股历史，key 是股票代码。"""

    def __init__(self, out_dir: str = "data_lake/capital/northbound_stock", **kw):
        super().__init__(
            name="northbound_stock", out_dir=out_dir,
            limiter=RateLimiter(min_interval=0.8, jitter=(0.2, 0.6)),
            max_workers=kw.pop("max_workers", 1), **kw,
        )

    def fetch_one(self, code: str):
        df = ak.stock_hsgt_individual_em(symbol=str(code).zfill(6))
        if df is None or df.empty:
            return None
        df = df.rename(columns=NORTHBOUND_RENAME)
        if "date" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df["code"] = str(code).zfill(6)
        df = _to_numeric(df, [
            "close", "pct_chg", "northbound_hold_shares", "northbound_hold_value",
            "northbound_hold_pct", "northbound_hold_shares_chg_1d",
            "northbound_buy_value_1d", "northbound_value_chg_1d",
        ])
        keep = [
            "date", "code", "northbound_hold_shares", "northbound_hold_value",
            "northbound_hold_pct", "northbound_hold_shares_chg_1d",
            "northbound_buy_value_1d", "northbound_value_chg_1d",
        ]
        return df[[c for c in keep if c in df.columns]]


def merge_margin(margin_dir: str = "data_lake/capital/margin",
                 out: str = "data_lake/capital/margin_all.parquet"):
    """把按日期的两融文件合并成一个长表 (date×code)"""
    from pathlib import Path
    files = sorted(Path(margin_dir).glob("*.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df.to_parquet(out, index=False)
    logger.info(f"[margin] 合并 {len(files)}个交易日 → {len(df)}行")
    return df


def merge_northbound(northbound_dir: str = "data_lake/capital/northbound",
                     out: str = "data_lake/capital/northbound_daily_all.parquet"):
    """把按日期的北向文件合并成一个长表 (date×code)。"""
    from pathlib import Path
    files = sorted(Path(northbound_dir).glob("*.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df.to_parquet(out, index=False)
    logger.info(f"[northbound] 合并 {len(files)}个交易日 → {len(df)}行")
    return df


def merge_northbound_stock(northbound_dir: str = "data_lake/capital/northbound_stock",
                           out: str = "data_lake/capital/northbound_all.parquet"):
    """把按股票代码的北向历史文件合并成一个长表 (date×code)。"""
    from pathlib import Path
    files = sorted(Path(northbound_dir).glob("*.parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
    df.to_parquet(out, index=False)
    logger.info(f"[northbound_stock] 合并 {len(files)}只 → {len(df)}行")
    return df
