import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from lake.compact import compact_prices

LAKE = Path("data_lake")
daily_dir = LAKE / "price/daily"
raw_dir = LAKE / "price/daily_raw"

files = sorted(daily_dir.glob("*.parquet"))
repaired_count = 0
repaired_files = 0

PRICE_COLS = ["open", "high", "low", "close"]

for _, fp in enumerate(files):
    code = fp.stem
    raw_fp = raw_dir / f"{code}.parquet"
    if not raw_fp.exists():
        continue
        
    df = pd.read_parquet(fp)
    df_raw = pd.read_parquet(raw_fp)
    
    joined = df.merge(df_raw, on="date")
    joined["factor"] = joined["close"] / joined["raw_close"]
    
    ref_row = joined[joined["date"] == pd.Timestamp("2026-05-29")]
    if ref_row.empty:
        continue
        
    ref_factor = float(ref_row.iloc[0]["factor"])
    if pd.isna(ref_factor) or ref_factor <= 0:
        continue
        
    # Check rows on or after 2026-06-01
    target_rows = joined[joined["date"] >= pd.Timestamp("2026-06-01")]
    
    changed = False
    for _, row in target_rows.iterrows():
        curr_factor = float(row["factor"])
        # If the factor deviated from the reference factor and is close to 1.0
        # (classical Tencent hfqday missing fallback to unadjusted price bug)
        if abs(curr_factor / ref_factor - 1) > 0.001 and abs(curr_factor - 1.0) < 0.001:
            d = row["date"]
            # Find matching row index in df
            df_idx = df[df["date"] == d].index[0]
            
            # Print repair details
            print(f"Repairing {code} on {d.date()}: factor {curr_factor} -> {ref_factor:.6f}")
            
            # Get raw prices on this date
            raw_row = df_raw[df_raw["date"] == d].iloc[0]
            
            # Update OHLC
            for col, rcol in zip(PRICE_COLS, ["raw_open", "raw_high", "raw_low", "raw_close"], strict=True):
                df.at[df_idx, col] = float(raw_row[rcol]) * ref_factor
            # Update amount
            df.at[df_idx, "amount"] = float(df.at[df_idx, "volume"]) * float(df.at[df_idx, "close"])
            
            repaired_count += 1
            changed = True
            
    if changed:
        df.to_parquet(fp, index=False)
        repaired_files += 1

print(f"\nDone: Repaired {repaired_files} files, {repaired_count} rows.")

if repaired_count > 0:
    print("Rebuilding daily_all.parquet...")
    compact_prices(daily_dir, LAKE / "price/daily_all.parquet")
    print("Rebuilding daily_all.parquet complete! ✅")
