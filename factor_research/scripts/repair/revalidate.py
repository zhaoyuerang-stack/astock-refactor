"""重新校验全市场（修复停牌误报后）+ 构建元数据"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
import pandas as pd

from lake import meta
from lake.validator import DataValidator

# 构建交易日历 + 上市日（本地，从价量推导）
cal = meta.build_calendar()
meta.build_list_dates()

# 重新校验全市场
v = DataValidator(calendar=cal)
files = sorted(Path("data_lake/price/daily").glob("*.parquet"))
results = []
for i, fp in enumerate(files):
    results.append(v.validate(fp.stem, pd.read_parquet(fp)))
    if (i + 1) % 1500 == 0:
        print(f"  校验 {i+1}/{len(files)}", flush=True)

report = v.quality_report(results, save_path="data_lake/quality_report.json")
triage = v.issue_triage_report(results, save_path="reports/data/data_issue_triage.json")
print("\n=== 真实质量(修复停牌误报后) ===", flush=True)
print(f"干净 {report['clean']}/{report['total']} ({report['clean_ratio']:.1%})", flush=True)
print(f"真问题分布: {report['issue_breakdown']}", flush=True)
print(f"数据分诊: {triage['summary']['counts_by_category']}", flush=True)
