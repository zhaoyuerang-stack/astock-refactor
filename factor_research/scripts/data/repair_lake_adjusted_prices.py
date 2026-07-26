"""Script to repair daily_all.parquet adjusted prices on 2026-06-10 and 2026-06-11.

Calculates the adjustment factor for each stock on 2026-06-09:
  adj_factor = close_adj / raw_close
Then applies this factor to raw OHLC columns on 2026-06-10 and 2026-06-11.
"""
from pathlib import Path

import pandas as pd


def main():
    lake_path = Path("/Users/kiki/astcok/factor_research/data_lake")
    daily_all_fp = lake_path / "price/daily_all.parquet"
    daily_raw_all_fp = lake_path / "price/daily_raw_all.parquet"
    
    print("=" * 70)
    print("  Repairing daily_all.parquet Adjusted Prices")
    print("=" * 70)
    
    if not daily_all_fp.exists() or not daily_raw_all_fp.exists():
        print("Error: Required parquet files do not exist.")
        return
        
    print("Loading daily_all.parquet...", flush=True)
    df = pd.read_parquet(daily_all_fp)
    df["date"] = pd.to_datetime(df["date"])
    
    print("Loading daily_raw_all.parquet...", flush=True)
    df_raw = pd.read_parquet(daily_raw_all_fp)
    df_raw["date"] = pd.to_datetime(df_raw["date"])
    
    # Identify target dates to repair
    target_dates = [pd.Timestamp("2026-06-10"), pd.Timestamp("2026-06-11")]
    print(f"Target dates to repair: {[d.strftime('%Y-%m-%d') for d in target_dates]}")
    
    # Calculate adjustment factors on 2026-06-09
    ref_date = pd.Timestamp("2026-06-09")
    print(f"Using reference date for adjustment factors: {ref_date.strftime('%Y-%m-%d')}")
    
    ref_adj = df[df["date"] == ref_date][["code", "close"]]
    ref_raw = df_raw[df_raw["date"] == ref_date][["code", "raw_close"]]
    
    merged_ref = pd.merge(ref_adj, ref_raw, on="code", how="inner")
    merged_ref["adj_factor"] = merged_ref["close"] / merged_ref["raw_close"]
    
    # Map of code -> adj_factor
    adj_factors = dict(zip(merged_ref["code"], merged_ref["adj_factor"], strict=True))
    print(f"Calculated adjustment factors for {len(adj_factors)} stocks.")
    
    # Perform repair
    print("Performing price repair...", flush=True)
    repaired_count = 0
    
    # We will merge the raw prices on the target dates with the adj_factor map
    # and update the values in daily_all.parquet
    
    # Create a backup of the original daily_all.parquet first
    backup_fp = daily_all_fp.with_name("daily_all_backup_before_repair.parquet")
    if not backup_fp.exists():
        print("Creating backup daily_all_backup_before_repair.parquet...", flush=True)
        df.to_parquet(backup_fp)
        
    # Iterate over the target dates
    for target_date in target_dates:
        # Get raw prices on this date
        raw_on_date = df_raw[df_raw["date"] == target_date]
        if raw_on_date.empty:
            continue
            
        # Map codes to raw prices on this date
        raw_close_map = dict(zip(raw_on_date["code"], raw_on_date["raw_close"], strict=True))
        raw_open_map = dict(zip(raw_on_date["code"], raw_on_date["raw_open"], strict=True))
        raw_high_map = dict(zip(raw_on_date["code"], raw_on_date["raw_high"], strict=True))
        raw_low_map = dict(zip(raw_on_date["code"], raw_on_date["raw_low"], strict=True))
        
        # Get the rows in daily_all on this date
        date_mask = df["date"] == target_date
        
        # We will update rows in-place
        indices = df[date_mask].index
        for idx in indices:
            row = df.loc[idx]
            code = row["code"]
            factor = adj_factors.get(code, 1.0) # default to 1.0 if not found
            
            raw_close_val = raw_close_map.get(code)
            if raw_close_val is not None:
                # Update OHLC
                df.at[idx, "close"] = raw_close_val * factor
                df.at[idx, "open"] = raw_open_map.get(code, raw_close_val) * factor
                df.at[idx, "high"] = raw_high_map.get(code, raw_close_val) * factor
                df.at[idx, "low"] = raw_low_map.get(code, raw_close_val) * factor
                repaired_count += 1
                
    print(f"Successfully repaired {repaired_count} daily price records.")
    
    # Save the repaired daily_all.parquet
    print("Saving repaired daily_all.parquet...", flush=True)
    df.to_parquet(daily_all_fp)
    print("Database price repair complete! ✅")

if __name__ == "__main__":
    main()
