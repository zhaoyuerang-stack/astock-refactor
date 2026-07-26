"""Hong Kong daily price cache for research scripts.

Research code may request HK price history through this module; parquet writes
stay under ``scripts/data`` so data-lake writer ownership remains explicit.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HK_DIR = ROOT / "data_lake" / "price" / "hk_daily"


def fetch_hk_history(code: str, start: str = "2018-01-01") -> pd.DataFrame | None:
    """Fetch full HK stock history with Tencent pagination."""
    seen: set[str] = set()
    rows: list[list] = []
    end_date = "2026-12-31"
    for _ in range(15):
        url = (
            "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param=hk{code},day,2010-01-01,{end_date},640,hfq"
        )
        try:
            resp = urllib.request.urlopen(url, timeout=15)
            data = json.loads(resp.read())
            node = data.get("data", {}).get(f"hk{code}", {})
            arr = node.get("hfqday") or node.get("day") or []
            if not arr:
                break
            new = [r for r in arr if r[0] not in seen]
            if not new:
                break
            for row in new:
                seen.add(row[0])
            rows = new + rows
            earliest = arr[0][0]
            if earliest <= "2017-01-01":
                break
            end_date = earliest
        except Exception:
            break
        time.sleep(0.2)
    if not rows:
        return None
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df[df["date"] >= pd.Timestamp(start)]


def load_or_fetch_hk_daily(code: str, *, start: str = "2018-01-01", min_rows: int = 100) -> pd.DataFrame | None:
    """Load cached HK daily prices or fetch and cache them."""
    HK_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = HK_DIR / f"{code}.parquet"
    if cache_file.exists():
        df = pd.read_parquet(cache_file)
        if len(df) >= min_rows:
            return df
    df = fetch_hk_history(code, start=start)
    if df is None or len(df) < min_rows:
        return None
    df.to_parquet(cache_file, index=False)
    return df


def close_series(df: pd.DataFrame, code: str) -> pd.Series:
    """Return a date-indexed close series named for the HK code."""
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    return out.set_index("date")["close"].rename(f"hk_{code}")
