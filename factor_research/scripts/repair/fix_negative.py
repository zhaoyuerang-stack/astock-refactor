"""剔除负价格行(后复权错误) + 最终校验"""
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

# 剔除负价格行
fixed_rows, fixed_stocks = 0, 0
for fp in PRICE.glob("*.parquet"):
    df = pd.read_parquet(fp)
    neg = (df[["open", "high", "low", "close"]] < 0).any(axis=1)
    if neg.any():
        df[~neg].reset_index(drop=True).to_parquet(fp, index=False)
        fixed_rows += int(neg.sum())
        fixed_stocks += 1
print(f"剔除负价格: {fixed_stocks}只 {fixed_rows}行")

# 最终校验
cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")["date"]
v = DataValidator(calendar=cal)
results = [v.validate(fp.stem, pd.read_parquet(fp))
           for fp in sorted(PRICE.glob("*.parquet"))]
report = v.quality_report(results, save_path="data_lake/quality_report.json")
print("\n=== 最终数据质量 ===")
print(f"干净 {report['clean']}/{report['total']} ({report['clean_ratio']:.1%})")
print(f"剩余真问题: {report['issue_breakdown']}")
print(f"剩余问题股(个案,供人工复核): {[f['code'] for f in report['flagged']]}")
