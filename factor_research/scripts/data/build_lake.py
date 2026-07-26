"""
阶段3主脚本：全市场日线下载(腾讯源,回溯2010) + 全量校验 + 质量报告
"""
import warnings; warnings.filterwarnings("ignore")
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import akshare as ak
import pandas as pd

from lake.sources.registry import resolve_source
from lake.validator import DataValidator


def get_codes():
    existing = sorted(fp.stem for fp in Path("data_lake/price/daily").glob("*.parquet"))
    if existing:
        return existing
    spot = ak.stock_zh_a_spot_em()
    return sorted(spot["代码"].astype(str).str.zfill(6).tolist())


def main():
    # 全市场代码：优先复用 data_lake 已有 universe；首次构建则从 AkShare 获取。
    codes = get_codes()
    print(f"全市场 {len(codes)} 只 | 腾讯后复权日线 | 回溯2010", flush=True)

    # 1. 下载（断点续传，已下的跳过）
    fetcher = resolve_source("price_hfq", out_dir="data_lake/price/daily",
                             start="2010-01-01", max_workers=6)
    stats = fetcher.run(codes, skip_existing=True, progress_every=300)
    if stats["failures"]:
        print(f"\n重试 {len(stats['failures'])} 个失败...", flush=True)
        fetcher.retry_failures(stats["failures"])

    # 2. 全量校验
    print("\n校验全市场数据...", flush=True)
    files = sorted(Path("data_lake/price/daily").glob("*.parquet"))
    cal = pd.read_parquet("data_lake/price/daily/600519.parquet")["date"]
    v = DataValidator(calendar=cal)
    results = []
    for i, fp in enumerate(files):
        df = pd.read_parquet(fp)
        results.append(v.validate(fp.stem, df))
        if (i + 1) % 1000 == 0:
            print(f"  校验 {i+1}/{len(files)}", flush=True)

    # 3. 合并大表
    print("\n合并 daily 大表...", flush=True)
    from lake.compact import compact_prices, compact_raw_prices
    compact_prices("data_lake/price/daily", "data_lake/price/daily_all.parquet")
    compact_raw_prices("data_lake/price/daily_raw", "data_lake/price/daily_raw_all.parquet")

    report = v.quality_report(results, save_path="data_lake/quality_report.json")
    print("\n=== 质量报告 ===", flush=True)
    print(f"总数={report['total']} 干净={report['clean']} ({report['clean_ratio']:.1%})", flush=True)
    print(f"问题分布: {report['issue_breakdown']}", flush=True)
    print("详见 data_lake/quality_report.json", flush=True)


if __name__ == "__main__":
    main()
