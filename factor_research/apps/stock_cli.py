"""Individual Stock Factor Diagnostics CLI.

Usage:
  python3 apps/stock_cli.py --code 600519
  python3 apps/stock_cli.py --code 000001
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.veto import salience_covariance_veto
from strategies.small_cap import load_price_panels


def get_clean_code(code: str) -> str:
    """Format and pad stock code to 6 digits."""
    return str(code).strip().zfill(6)

def main():
    parser = argparse.ArgumentParser(description="Query individual stock factor diagnostics.")
    parser.add_argument("--code", "-c", required=True, type=str, help="Stock code (e.g. 600519 or 000001)")
    args = parser.parse_args()

    code = get_clean_code(args.code)

    print("=" * 80)
    print(f"  INDIVIDUAL STOCK DIAGNOSTICS: {code}")
    print("=" * 80)

    # 1. Load panels
    print("Loading price panels...", flush=True)
    try:
        close, volume, amount = load_price_panels("2018-01-01")
    except Exception as e:
        print(f"❌ Failed to load price panels: {e}", file=sys.stderr)
        sys.exit(1)

    if code not in close.columns:
        print(f"❌ Stock code '{code}' not found in the A-share data lake.", file=sys.stderr)
        # Suggest some active codes
        active_sample = list(close.dropna(axis=1, how="all").columns[:10])
        print(f"   Available active stock codes sample: {', '.join(active_sample)}...")
        sys.exit(1)

    # Computing factors...
    # Amihud Illiquidity (rolling 20d)
    r_abs = close.pct_change(fill_method=None).abs().replace([np.inf, -np.inf], np.nan)
    illiq_raw = (r_abs / (amount + 1)).rolling(20, min_periods=1).mean()
    
    # Salience Veto (faded salience covariance)
    veto_factor = salience_covariance_veto(close)

    # Size: -log(60d average amount)
    size_factor = -np.log(amount.rolling(60, min_periods=1).mean() + 1)

    # Latest date with valid factor values for this stock
    valid_factor_dates = illiq_raw[code].dropna().index.intersection(veto_factor[code].dropna().index)
    if len(valid_factor_dates) == 0:
        print(f"❌ No valid factor values found for stock '{code}' in the entire history.")
        sys.exit(1)
    latest_date = valid_factor_dates[-1]
    latest_date_str = latest_date.strftime("%Y-%m-%d")

    # 3. Compute cross-sectional ranks
    # Ranks on the latest date to find percentiles (higher = better/higher exposure for long)
    illiq_cs = illiq_raw.loc[latest_date].dropna()
    veto_cs = veto_factor.loc[latest_date].dropna()
    size_cs = size_factor.loc[latest_date].dropna()

    if code not in illiq_cs:
        print(f"⚠️ No active factor values for code '{code}' on the latest date ({latest_date_str}).")
        sys.exit(1)

    # Percentiles: rank(pct=True)
    # Amihud: higher is more illiquid (preferred by the strategy)
    illiq_pct = (illiq_cs.rank(pct=True).loc[code]) * 100
    # Veto: lower is more bubble risk / salient (vetoed if below threshold, e.g., bottom 30%)
    veto_pct = (veto_cs.rank(pct=True).loc[code]) * 100
    # Size: higher is smaller market value / turnover (preferred by small cap)
    size_pct = (size_cs.rank(pct=True).loc[code]) * 100

    # 4. Extract history (last 20 days)
    history_dates = close[code].dropna().index[-20:]
    history_df = pd.DataFrame(index=history_dates)
    history_df["Close"] = close.loc[history_dates, code]
    history_df["Volume(手)"] = volume.loc[history_dates, code]
    history_df["Amount(万元)"] = (amount.loc[history_dates, code] / 1e4)
    history_df["Illiq_Factor"] = illiq_raw.loc[history_dates, code]
    history_df["Veto_Score"] = veto_factor.loc[history_dates, code]

    # Print summary
    print("\n" + "=" * 60)
    print(f"  Summary as of: {latest_date_str}")
    print("=" * 60)
    print(f"  Stock Ticker       : {code}")
    print(f"  Latest Close Price : {close.loc[latest_date, code]:.2f} CNY")
    print(f"  Daily Volume       : {volume.loc[latest_date, code]:,.0f} 手")
    print(f"  Daily Amount       : {amount.loc[latest_date, code] / 1e8:.3f} 亿元")
    print("-" * 60)
    print(f"  Amihud Illiquidity : {illiq_raw.loc[latest_date, code]:.2e}  (全市场排名前 {100 - illiq_pct:.1f}% 最不活跃个股)")
    print(f"  Size Factor (Small): {size_factor.loc[latest_date, code]:.2f}  (全市场排名前 {100 - size_pct:.1f}% 极小市值/成交额个股)")
    
    # Salience veto check (bottom 30% is vetoed)
    is_vetoed = veto_pct <= 30.0
    veto_status = "❌ VETOED / 已否决 (Bubble Risk)" if is_vetoed else "✅ PASSED / 通过 (Safe)"
    print(f"  Salience Veto Score: {veto_factor.loc[latest_date, code]:+.4f}  (百分比分位数: {veto_pct:.1f}%)")
    print(f"  Veto Decision      : {veto_status} (阈值百分位: 30.0%)")
    print("=" * 60)

    # Print history table
    print("\n  Recent 20 Days Factor and Market History:")
    print("  " + "-" * 75)
    print(f"  {'Date':<10} | {'Close':>8} | {'Volume':>9} | {'Amount(亿)':>10} | {'Illiq':>10} | {'Veto':>8}")
    print("  " + "-" * 75)
    for dt in history_dates:
        row = history_df.loc[dt]
        dt_str = dt.strftime("%Y-%m-%d")
        print(f"  {dt_str:<10} | {row['Close']:>8.2f} | {row['Volume(手)']:>9,.0f} | "
              f"{row['Amount(万元)']/10000:>10.3f} | {row['Illiq_Factor']:>10.2e} | {row['Veto_Score']:>+8.4f}")
    print("  " + "-" * 75)
    print("  Note: Illiq is unitless (higher is more illiquid). Veto factor higher is safer.")
    print("=" * 80)

if __name__ == "__main__":
    main()
