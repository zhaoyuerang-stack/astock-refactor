"""最终校验：聚焦真数据质量问题（停牌/新股等归info不计入）

校验前应用与加载层一致的确定性清洗(lake.cleaning):quarantine 隔离 + repair_ohlc,
使报告反映策略**实际看到**的视图。末尾对 severe(负价/OHLC)设硬闸:>0 即非零退出,
防 600608 类后复权崩溃被无声放过。"""
import warnings; warnings.filterwarnings("ignore")
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).parent)
import pandas as pd

from lake.cleaning import apply_quarantine, repair_ohlc
from lake.validator import DataValidator

cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")["date"]
v = DataValidator(calendar=cal)
files = sorted(Path("data_lake/price/daily").glob("*.parquet"))
results = []
for i, fp in enumerate(files):
    df = pd.read_parquet(fp)
    df["code"] = fp.stem                       # quarantine 按 code 匹配
    df = repair_ohlc(apply_quarantine(df))     # 与 load_lake 同一套清洗
    results.append(v.validate(fp.stem, df))
    if (i + 1) % 1500 == 0:
        print(f"  {i+1}/{len(files)}", flush=True)

report = v.quality_report(results, save_path="data_lake/quality_report.json")
print("\n=== 真实数据质量(聚焦真问题) ===", flush=True)
print(f"干净 {report['clean']}/{report['total']} ({report['clean_ratio']:.1%})", flush=True)
print(f"真问题分布: {report['issue_breakdown']}", flush=True)
info_cnt = sum(1 for r in results if r.get("info"))
print(f"有停牌/新股等信息标记: {info_cnt}只(A股正常现象)", flush=True)

# ── severe 硬闸:负价/OHLC 逻辑错是真问题,不允许残留 ──
severe = sum(c for k, c in report["issue_breakdown"].items() if "负价" in k or "OHLC" in k)
if severe:
    print(f"\n❌ severe(负价/OHLC)残留 {severe} —— 真数据问题,需 repair_ohlc 或加入 quarantine", flush=True)
    sys.exit(1)
print("✅ severe(负价/OHLC)=0,真问题闸门通过(跳变多为除权/涨跌停,属正常现象)", flush=True)
