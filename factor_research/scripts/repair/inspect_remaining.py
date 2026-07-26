"""抽查剩余18只真问题的性质"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
import pandas as pd

from lake.validator import DataValidator

cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")["date"]
v = DataValidator(calendar=cal)

ohlc_bad, jump_bad = [], []
for fp in sorted(Path("data_lake/price/daily").glob("*.parquet")):
    df = pd.read_parquet(fp)
    r = v.validate(fp.stem, df)
    if r["ok"]:
        continue
    for iss in r["issues"]:
        if "OHLC" in iss:
            ohlc_bad.append((fp.stem, df, iss))
        elif "跳变" in iss:
            jump_bad.append((fp.stem, df))

print(f"=== 剩余OHLC错误 {len(ohlc_bad)}只 ===")
for code, df, iss in ohlc_bad:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    bad = df[(l > o) | (l > c) | (h < o) | (h < c) | (df[["open","high","low","close"]] < 0).any(axis=1)]
    neg = (df[["open","high","low","close"]] < 0).any(axis=1).sum()
    print(f"[{code}] {iss} | 负值行={neg} | 首异常:")
    print(bad[["date","open","high","low","close"]].head(1).to_string(index=False))

print(f"\n=== 剩余价格跳变 {len(jump_bad)}只 (看是否新股上市初期) ===")
for code, df in jump_bad[:6]:
    ret = df["close"].pct_change()
    gap = df["date"].diff().dt.days
    big = df[(ret.abs() > 0.5) & (gap <= 20)]
    first_date = df["date"].min()
    big_date = big["date"].iloc[0]
    days_since_list = (big_date - first_date).days
    print(f"[{code}] 跳变日{big_date.date()}, 距上市{days_since_list}天 {'(新股初期,不限涨跌)' if days_since_list < 30 else '(需查:除权/退市/重组)'}")
