"""
批量财务下载（东财业绩报表 yjbb，按报告期）
一次拿全市场一个季度，约64次请求几分钟下完，彻底避开逐只封禁。
含 ROE/EPS/营收/净利润/毛利率/现金流、所处行业。

DQ-2026-001:东财"最新公告日期"(→ em_last_touch_date)是公司级、随最近一次披露
滚动刷新的字段，不是本报告期专属公告日，禁止直接当 ann_date/avail_date 用（根因
与现场复现见 lake/fundamental_repair.py）。本脚本抓完原始数据后调用
lake.fundamental_repair 按 income/balancesheet/cashflow 三表 ann_date 交叉核对
重铺真正的 ann_date/avail_date，不再用"报告期+45天"粗糙兜底当主口径。
"""
import warnings; warnings.filterwarnings("ignore")
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
import akshare as ak
import pandas as pd

from lake.fundamental_repair import build_true_date_map, repair
from lake.schema import YJBB_RENAME

# 报告期列表（2010Q1 ~ 2026Q1）
periods = [f"{y}{md}" for y in range(2010, 2027) for md in ["0331","0630","0930","1231"]]
periods = [p for p in periods if p <= "20260331"]

RENAME = YJBB_RENAME
KEEP = ["code","report_date","em_last_touch_date","eps","revenue","revenue_yoy",
        "net_profit","net_profit_yoy","bps","roe","cfo_ps","gross_margin","industry"]

print(f"批量财务下载: {len(periods)}个报告期", flush=True)
frames = []
for i, p in enumerate(periods):
    for _ in range(3):
        try:
            df = ak.stock_yjbb_em(date=p)
            if df is not None and not df.empty:
                df = df.rename(columns=RENAME)
                df["report_date"] = pd.to_datetime(p)
                frames.append(df[[c for c in KEEP if c in df.columns]])
            break
        except Exception:
            time.sleep(1)
    if (i+1) % 16 == 0:
        print(f"  {i+1}/{len(periods)} 报告期", flush=True)
    time.sleep(0.6)   # 温和限流(64次<5分钟,远低于东财阈值)

allf = pd.concat(frames, ignore_index=True)
allf["code"] = allf["code"].astype(str).str.zfill(6)

print("重铺 ann_date/avail_date(income/balancesheet/cashflow 三表交叉核对)...", flush=True)
true_map = build_true_date_map()
allf, stat = repair(allf, true_map)
print(f"  cross_table_min {stat['cross_table_min']} ({stat['cross_table_min']/stat['total']:.1%}) "
      f"+ proxy_statutory {stat['proxy_statutory']} ({stat['proxy_statutory']/stat['total']:.1%})", flush=True)

allf.to_parquet("data_lake/fundamental_batch.parquet", index=False)
print(f"✅ 完成: {len(allf)}行 ({allf['code'].nunique()}只 × {allf['report_date'].nunique()}季度)", flush=True)
print(f"   字段: {[c for c in allf.columns]}", flush=True)
