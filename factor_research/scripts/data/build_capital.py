"""Build/update capital-flow data_lake tables.

Outputs:
- data_lake/capital/margin/YYYYMMDD.parquet
- data_lake/capital/margin_all.parquet
- data_lake/capital/northbound/YYYYMMDD.parquet
- data_lake/capital/northbound_all.parquet

Northbound uses Eastmoney. If the local proxy blocks eastmoney, this script
keeps margin data usable and reports northbound failures for later retry.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from lake.sources.exchange import (  # noqa: E402
    merge_margin,
    merge_northbound,
    merge_northbound_stock,
)
from lake.sources.registry import resolve_source  # noqa: E402

LAKE = Path("data_lake")
REPORT_DIR = Path("reports/data")


def trade_date_keys(start, end, limit=None):
    cal = pd.read_parquet(LAKE / "meta/trade_calendar.parquet")
    dates = pd.to_datetime(cal["date"])
    mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    keys = dates.loc[mask].dt.strftime("%Y%m%d").tolist()
    return keys[:limit] if limit else keys


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))


def top_liquidity_codes(limit=1000, lookback_days=120):
    daily = sorted((LAKE / "price/daily").glob("*.parquet"))
    rows = []
    for fp in daily:
        try:
            df = pd.read_parquet(fp, columns=["date", "amount"])
        except Exception:
            continue
        if df.empty:
            continue
        tail = df.sort_values("date").tail(lookback_days)
        rows.append((fp.stem, float(tail["amount"].mean())))
    rows = sorted(rows, key=lambda row: row[1], reverse=True)
    return [code for code, _ in rows[:limit]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit", type=int, default=None, help="Optional first-N trade days for smoke runs.")
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    ap.add_argument("--margin", action="store_true")
    ap.add_argument("--northbound", action="store_true")
    ap.add_argument("--northbound-stock", action="store_true", help="Fallback: fetch full northbound history by stock code.")
    ap.add_argument("--code-limit", type=int, default=1000, help="Top-liquidity stocks for --northbound-stock.")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    keys = trade_date_keys(args.start, end, limit=args.limit)
    do_all = not (args.margin or args.northbound or args.northbound_stock)
    report = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "start": args.start,
        "end": end,
        "trade_days": len(keys),
        "outputs": {},
    }

    if do_all or args.margin:
        fetcher = resolve_source("margin", max_workers=args.workers, timeout=30, retries=2)
        stats = fetcher.run(keys, skip_existing=args.skip_existing, progress_every=50)
        merged = merge_margin()
        report["margin"] = stats
        report["outputs"]["margin_all"] = "data_lake/capital/margin_all.parquet" if merged is not None else None

    if do_all or args.northbound:
        fetcher = resolve_source("northbound", max_workers=1, timeout=30, retries=2)
        stats = fetcher.run(keys, skip_existing=args.skip_existing, progress_every=20)
        merged = merge_northbound()   # writes to northbound_daily_all.parquet (不覆盖 stock 版)
        report["northbound"] = stats
        report["outputs"]["northbound_daily_all"] = "data_lake/capital/northbound_daily_all.parquet" if merged is not None else None

    if args.northbound_stock:
        codes = top_liquidity_codes(limit=args.code_limit)
        fetcher = resolve_source("northbound_stock", max_workers=args.workers, timeout=30, retries=2)
        stats = fetcher.run(codes, skip_existing=args.skip_existing, progress_every=50)
        merged = merge_northbound_stock()
        report["northbound_stock"] = {
            **stats,
            "code_limit": args.code_limit,
            "universe": "top_liquidity_by_recent_amount",
            "note": "fallback per-stock history endpoint; use only because Eastmoney daily aggregate returned 9701/None",
        }
        report["outputs"]["northbound_all"] = "data_lake/capital/northbound_all.parquet" if merged is not None else None

    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    out = REPORT_DIR / "capital_build_report.json"
    write_report(out, report)
    print(f"\nSaved report: {out}", flush=True)


if __name__ == "__main__":
    main()
