"""Build and score the canonical core factor library.

Run:
    cd factor_research
    python3 scripts/research/build_factor_store.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factor_store.core_backfill import backfill_core_factors  # noqa: E402
from lake.fingerprint import stamp_vintage  # noqa: E402
from lake.load_lake import (  # noqa: E402
    load_daily_basic_panel,
    load_fundamental_panel,
    load_prices,
    load_raw_close,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill core factor panels and scores.")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--primary-horizon", type=int, default=20)
    args = parser.parse_args()

    print(f"[factor-store] loading price panels from {args.start}", flush=True)
    from lake.units import implied_amount

    prices = load_prices(start=args.start, fields=("close", "volume"))
    close = prices["close"]
    volume = prices["volume"]
    raw_close = load_raw_close(start=args.start).reindex(index=close.index, columns=close.columns)
    amount = implied_amount(volume, raw_close)

    valid = amount.notna().sum(axis=1)
    if len(valid):
        typical = valid.iloc[-60:].median()
        good = valid[valid >= typical * 0.7]
        if len(good):
            cutoff = good.index[-1]
            close = close.loc[:cutoff]
            volume = volume.loc[:cutoff]
            amount = amount.loc[:cutoff]

    fundamentals = load_fundamental_panel(
        close.index,
        fields=["net_profit_yoy"],
    )
    net_profit_yoy = fundamentals.get("net_profit_yoy")
    if net_profit_yoy is None:
        raise RuntimeError("net_profit_yoy panel is unavailable")

    daily_basic = load_daily_basic_panel(close.index, fields=["total_mv"])
    total_mv = daily_basic.get("total_mv")
    data_vintage = stamp_vintage(
        f"data_lake:{close.index[0].date()}:{close.index[-1].date()}",
        close,
    )
    print(
        f"[factor-store] panel {close.shape[0]}x{close.shape[1]}, vintage={data_vintage}",
        flush=True,
    )

    result = backfill_core_factors(
        close=close,
        volume=volume,
        amount=amount,
        net_profit_yoy=net_profit_yoy,
        total_mv=total_mv,
        data_vintage=data_vintage,
        horizons=(1, 5, 10, 20),
        primary_horizon=args.primary_horizon,
    )

    for name, factor_id in result.factor_ids.items():
        print(f"[factor-store] {name}: {factor_id}", flush=True)
    print(f"[factor-store] correlations: {result.correlation_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
