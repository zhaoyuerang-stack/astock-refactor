"""Style-neutral audit for amount-rank factor.

Uses the existing CNE6-style subset implementation from style_neutralization.py
and compares raw amount rank against small-cap and illiquidity proxies.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/research/amount_timing_style_audit.py
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_factor
from factors.utils import mad_clip, safe_zscore
from lake.load_lake import load_daily_basic_panel
from scripts.research.style_neutralization import audit_style_neutral, build_cne6_styles
from strategies.small_cap import load_price_panels


def main() -> None:
    close, volume, amount = load_price_panels("2018-01-01")
    db = load_daily_basic_panel(close.index, fields=["total_mv", "turnover_rate"])
    styles = build_cne6_styles(
        close,
        amount,
        total_mv=db["total_mv"],
        turnover=db["turnover_rate"],
    )
    ret = close.pct_change(fill_method=None)
    candidates = {
        "amount_rank": -amount.rank(axis=1, pct=True),
        "small_cap60": small_cap_factor(amount, 60),
        "illiq20": safe_zscore(
            mad_clip((ret.abs() / (amount.replace(0, float("nan")) + 1.0)).rolling(20).mean())
        ),
    }

    for horizon in [5, 20]:
        fwd = close.pct_change(horizon, fill_method=None).shift(-horizon)
        print(f"\n=== style neutral audit horizon={horizon} ===")
        print(f"{'alpha':<13}{'NW':>8}{'true_inc':>11}{'R2':>8}  {'verdict':<16} loadings")
        print("-" * 88)
        for row in audit_style_neutral(candidates, styles, fwd, close, horizon=horizon):
            loads = ", ".join(f"{name}{value:+.2f}" for name, value in row["loadings"][:4])
            print(
                f"{row['name']:<13}{row['nw_icir']:>+8.3f}"
                f"{row['true_inc']:>+11.4f}{row['r2']:>8.1%}  "
                f"{row['verdict'].upper():<16} {loads}"
            )


if __name__ == "__main__":
    main()
