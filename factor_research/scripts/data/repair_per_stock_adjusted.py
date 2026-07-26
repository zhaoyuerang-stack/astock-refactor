"""治愈逐只后复权 parquet 中被错口径污染的行(2026-06-10/11 事故)。

根因:腾讯源 hfqday 缺失静默回退不复权 day(已在 lake/sources/tencent.py 根治),
不复权价被增量 append 进逐只后复权文件;此前的修复只治了 daily_all 大表,
逐只文件仍带毒 → 每次日更 compact 都会把大表重新写坏。

方法:对每只股票,取目标日前最后一个好行的 adj_close/raw_close 计算复权因子,
目标日 stored_close 与 raw_close×factor 偏差 >5% 判定为毒行,用 raw OHLC×factor
重建(amount = volume×close,与湖内既有口径一致)。受影响原始行先备份。

用法: python3 scripts/data/repair_per_stock_adjusted.py [--dates 2026-06-10 2026-06-11]
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

LAKE = Path("data_lake")
PRICE_COLS = ["open", "high", "low", "close"]


def main(target_dates: list[str]) -> int:
    targets = [pd.Timestamp(d) for d in target_dates]
    daily = LAKE / "price/daily"

    print("加载 daily_raw_all (因子参考 + 重建源) ...", flush=True)
    raw = pd.read_parquet(LAKE / "price/daily_raw_all.parquet")
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw[raw["date"] <= max(targets)]
    raw_by_code = {c: g.set_index("date").sort_index() for c, g in raw.groupby("code")}

    fixed_rows, backups = 0, []
    files = sorted(daily.glob("*.parquet"))
    fixed_files = 0
    for i, fp in enumerate(files):
        code = fp.stem
        rc = raw_by_code.get(code)
        if rc is None:
            continue
        df = pd.read_parquet(fp)
        df["date"] = pd.to_datetime(df["date"])
        mask_t = df["date"].isin(targets)
        if not mask_t.any():
            continue

        # 因子参考 = 目标日前最后一行(未被本次事故污染)
        before = df[df["date"] < min(targets)]
        if before.empty:
            continue
        ref = before.iloc[-1]
        if ref["date"] not in rc.index:
            continue
        ref_raw = float(rc.loc[ref["date"], "raw_close"])
        if not ref_raw or pd.isna(ref_raw):
            continue
        factor = float(ref["close"]) / ref_raw

        changed = False
        for idx in df.index[mask_t]:
            d = df.at[idx, "date"]
            if d not in rc.index:
                continue
            expected = float(rc.loc[d, "raw_close"]) * factor
            stored = float(df.at[idx, "close"])
            if expected <= 0 or abs(stored / expected - 1) <= 0.05:
                continue  # 未污染(含因子≈1 的新股)
            backups.append(df.loc[[idx]].assign(code=code))
            for col, rcol in zip(PRICE_COLS, ["raw_open", "raw_high", "raw_low", "raw_close"], strict=True):
                df.at[idx, col] = float(rc.loc[d, rcol]) * factor
            df.at[idx, "amount"] = float(df.at[idx, "volume"]) * float(df.at[idx, "close"])
            fixed_rows += 1
            changed = True
        if changed:
            df.to_parquet(fp, index=False)
            fixed_files += 1
        if (i + 1) % 1000 == 0:
            print(f"  {i+1}/{len(files)} (已治愈 {fixed_files} 只 / {fixed_rows} 行)", flush=True)

    if backups:
        bfp = LAKE / f"price/_repair_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.parquet"
        pd.concat(backups, ignore_index=True).to_parquet(bfp, index=False)
        print(f"受影响原始行已备份: {bfp}", flush=True)
    print(f"完成: 治愈 {fixed_files} 只 / {fixed_rows} 行", flush=True)
    return fixed_files


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=["2026-06-10", "2026-06-11"])
    args = ap.parse_args()
    main(args.dates)
