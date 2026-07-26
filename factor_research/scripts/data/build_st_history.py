"""
ST历史推断（改进版：涨停封板特征）

原理：ST股涨跌停限±5%，普通主板±10%。
识别"5%封板"：收盘≈最高且涨幅≈+5%(涨停封板) 或 收盘≈最低且跌幅≈-5%(跌停封板)。
某段时间出现5%封板 → ST时段。比"最大涨幅"法准得多(封板是强信号)。
注：近似推断；精确ST时点需带日期的名称变更源。双创注册制±20%无±5%ST制度，跳过。
"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
import pandas as pd

from lake.load_lake import load_prices

print("加载全市场价量(含high/low)...", flush=True)
px = load_prices(start="2010-01-01", fields=("close", "high", "low"))
close, high, low = px["close"], px["high"], px["low"]
ret = close.pct_change()
print(f"  {close.shape[1]}只 × {close.shape[0]}日", flush=True)

st_panel = {}
n_main = 0
for code in close.columns:
    if code.startswith(("30", "68", "4", "8", "9")):  # 双创/北交所注册制,跳过
        continue
    n_main += 1
    r = ret[code]
    c, h, l = close[code], high[code], low[code]
    # 5%封板：收盘=最高且涨~5%(涨停) 或 收盘=最低且跌~5%(跌停)
    up5 = (c >= h * 0.999) & (r > 0.045) & (r < 0.055)
    down5 = (c <= l * 1.001) & (r < -0.045) & (r > -0.055)
    sig5 = up5 | down5
    # ST时段：滚动60日内出现过5%封板
    st = (sig5.rolling(60).sum() > 0) & r.notna()
    if st.any():
        st_panel[code] = st

st_df = pd.DataFrame(st_panel).reindex(close.index)
st_df.to_parquet("data_lake/meta/st_history.parquet")

st_stocks = (st_df.sum() > 0).sum()
total = int(st_df.sum().sum())
print(f"\nST历史推断(封板法, 主板{n_main}只):", flush=True)
print(f"  疑似ST: {st_stocks}只 ({st_stocks/n_main:.1%}主板), {total}个(股×日)标记", flush=True)
print("  保存: data_lake/meta/st_history.parquet", flush=True)
