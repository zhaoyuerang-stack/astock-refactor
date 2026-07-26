"""修复数据问题：OHLC精度clip修正 + 僵尸值交叉验证"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
import pandas as pd

from lake.validator import DataValidator

PRICE = Path("data_lake/price/daily")

# ── 1. OHLC clip 修正（精度错误：high=四者最大, low=四者最小）──
print("1. OHLC clip 修正...")
fixed_pts, fixed_stocks = 0, 0
for fp in PRICE.glob("*.parquet"):
    df = pd.read_parquet(fp)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    bad = ((l > o) | (l > c) | (h < o) | (h < c))
    if bad.any():
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
        df.to_parquet(fp, index=False)
        fixed_pts += int(bad.sum())
        fixed_stocks += 1
print(f"   修正 {fixed_stocks}只 {fixed_pts}个异常点")

# ── 2. 僵尸值交叉验证（通达信看那几日是否真同价）──
print("\n2. 僵尸值交叉验证(通达信)...")
cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")["date"]
v = DataValidator(calendar=cal)
zombies = []
for fp in PRICE.glob("*.parquet"):
    df = pd.read_parquet(fp)
    if "疑似僵尸值(连续5日同价)" in v.validate(fp.stem, df)["issues"]:
        zombies.append(fp.stem)
print(f"   僵尸股: {len(zombies)}只")

from mootdx.quotes import Quotes

client = Quotes.factory(market="std")
real, err = 0, 0
for code in zombies:
    try:
        df = pd.read_parquet(PRICE / f"{code}.parquet")
        same = (df["close"].diff() == 0)
        run = same.rolling(5).sum()
        idx = run[run >= 5].index[0]
        seg_dates = set(df.loc[max(0, idx-5):idx, "date"].dt.normalize())
        tdx = client.bars(symbol=code, frequency=9, offset=800)
        tdx["d"] = pd.to_datetime(tdx["datetime"]).dt.normalize()
        tdx_seg = tdx[tdx["d"].isin(seg_dates)]
        # 通达信该段是否也几乎不变
        if len(tdx_seg) >= 3:
            tdx_var = tdx_seg["close"].pct_change().abs().max()
            if tdx_var < 0.005:
                real += 1   # 通达信也同价 → 真实(一字板/特殊)
            else:
                err += 1    # 通达信有变化 → 腾讯填充错误
    except Exception:
        continue
print(f"   交叉验证: 真实(通达信也同价){real}只, 腾讯填充错误{err}只")
print(f"   → {'僵尸值多为真实特殊状态(一字板等),保留' if real >= err else '部分为数据错误,需修正'}")
