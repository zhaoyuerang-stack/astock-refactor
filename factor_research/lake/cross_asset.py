"""跨资产 ETF 日线读取(data_lake/cross_asset/etf/)。

列: date,open,close,high,low,volume,amount (后复权)
    + raw_open,raw_close,raw_high,raw_low (不复权;旧格式文件可能缺失)
写入方: scripts/data/fetch_cross_asset_etf.py(全量/增量)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ETF_DIR = ROOT / "data_lake" / "cross_asset" / "etf"


def load_etf_daily(code: str) -> pd.DataFrame | None:
    """读单只 ETF 日线;文件不存在返回 None。date 列为 datetime64。"""
    fp = ETF_DIR / f"{code}.parquet"
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    df["date"] = pd.to_datetime(df["date"])
    return df
