"""用超级大盘股高频交集构建准确交易日历 + 重新校验"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
from collections import Counter

import pandas as pd

from lake.validator import DataValidator

# 6只超级大盘股(几乎不停牌)，出现在>=5只的日期=真交易日(排除多余日期,容忍个别停牌)
ANCHORS = ["600519", "601398", "000001", "600036", "600000", "601988"]
cnt = Counter()
for c in ANCHORS:
    fp = Path(f"data_lake/price/daily/{c}.parquet")
    if fp.exists():
        cnt.update(pd.read_parquet(fp, columns=["date"])["date"].tolist())
cal = pd.DatetimeIndex(sorted(d for d, n in cnt.items() if n >= 5))
print(f"交易日历(高频交集法): {len(cal)}天 {cal.min().date()}~{cal.max().date()}", flush=True)
pd.DataFrame({"date": cal}).to_parquet("data_lake/meta/trade_calendar.parquet", index=False)

# 重新校验
v = DataValidator(calendar=cal)
files = sorted(Path("data_lake/price/daily").glob("*.parquet"))
results = []
for i, fp in enumerate(files):
    results.append(v.validate(fp.stem, pd.read_parquet(fp)))
    if (i + 1) % 1500 == 0:
        print(f"  校验 {i+1}/{len(files)}", flush=True)
report = v.quality_report(results, save_path="data_lake/quality_report.json")
print("\n=== 真实质量(准确日历) ===", flush=True)
print(f"干净 {report['clean']}/{report['total']} ({report['clean_ratio']:.1%})", flush=True)
print(f"真问题分布: {report['issue_breakdown']}", flush=True)
