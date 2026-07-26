"""repair_fundamental_batch_ann.py — DQ-2026-001 修复 CLI:重铺存量湖数据的 PIT 知识日。

根因与真值级联规则见 `lake/fundamental_repair.py`(共享核心,R-ARCH-001 要求
lake. 不得 import scripts.,重铺逻辑必须落在 lake/ 供 build_fundamental_batch.py /
lake/update.py::update_fundamental() 一并复用,不能只修一次性 CLI 修复本文件)。

本文件只保留 CLI 特有部分:写前校验(fail-closed)、备份、写盘。

fail-closed:写前校验 R1(avail≥report)、行数/键集/数值列零漂移(ann_source 允许随
真值源扩充而改善,不计入冻结列)、季度 lag 中位 ≤180d(平台探测器),任一不过则拒绝
写湖。原表先备份 data_lake/backups/。

用法:python3 scripts/data/repair_fundamental_batch_ann.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # factor_research/
sys.path.insert(0, str(ROOT))

from lake.fundamental_repair import build_true_date_map, repair  # noqa: E402

LAKE = ROOT / "data_lake"
FB_FP = LAKE / "fundamental_batch.parquet"
FANN_FP = LAKE / "financials" / "f_ann_date_income.parquet"
BACKUP_DIR = LAKE / "backups"


def validate(new: pd.DataFrame, old: pd.DataFrame) -> list[str]:
    """写前校验,返回违规列表(空=通过)。任何一条不过 = 拒绝写湖。"""
    errs: list[str] = []
    if len(new) != len(old):
        errs.append(f"行数漂移 {len(old)} → {len(new)}")
    key = ["code", "report_date"]
    if not new[key].equals(old.assign(report_date=pd.to_datetime(old["report_date"]),
                                      code=old["code"].astype(str).str.zfill(6))[key]):
        errs.append("键集 (code, report_date) 漂移")
    # ann_source 允许随真值源扩充而改善(proxy_statutory → cross_table_min),不冻结
    value_cols = [c for c in old.columns if c not in ("ann_date", "avail_date", "ann_source")]
    for c in value_cols:
        if c in ("report_date",):
            continue
        a, b = old[c].reset_index(drop=True), new[c].reset_index(drop=True)
        if c == "code":
            if not a.astype(str).str.zfill(6).equals(b.astype(str).str.zfill(6)):
                errs.append("code 列漂移")
        elif not a.equals(b):
            errs.append(f"数值/维度列漂移: {c}")
    r1 = new[new["avail_date"] < new["report_date"]]
    if len(r1):
        errs.append(f"R1 物理违规 {len(r1)} 行(avail < report)")
    lag = (new["avail_date"] - new["report_date"]).dt.days
    q = new.assign(lag=lag).groupby(new["report_date"].dt.to_period("Q"))["lag"].median()
    bad_q = q[q > 180]
    if len(bad_q):
        errs.append(f"平台探测:{len(bad_q)} 个季度 lag 中位 >180d: {bad_q.to_dict()}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="只校验与打印,不写湖")
    args = ap.parse_args()

    if not FB_FP.exists() or not FANN_FP.exists():
        print(f"❌ 缺输入: {FB_FP} 或 {FANN_FP} 不存在(先跑 build_f_ann_sidecar.py)")
        return 1

    old = pd.read_parquet(FB_FP)
    true_map = build_true_date_map()
    new, stat = repair(old, true_map)
    print(f"重铺:{stat['total']} 行 → cross_table_min {stat['cross_table_min']} "
          f"({stat['cross_table_min']/stat['total']:.1%}) + proxy_statutory "
          f"{stat['proxy_statutory']} ({stat['proxy_statutory']/stat['total']:.1%})")

    errs = validate(new, old)
    lag = (new["avail_date"] - new["report_date"]).dt.days
    q = new.assign(lag=lag).groupby(new["report_date"].dt.to_period("Q"))["lag"].median()
    print(f"修后 lag 中位 {lag.median():.0f} 天;季度中位区间 [{q.min():.0f}, {q.max():.0f}] 天")
    if errs:
        print("❌ 写前校验失败,拒绝写湖:")
        for e in errs:
            print(f"  {e}")
        return 1
    if args.dry_run:
        print("dry-run:校验通过,未写湖。")
        return 0

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bdir = BACKUP_DIR / f"fundamental_batch_ann_repair_{ts}"
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FB_FP, bdir / "fundamental_batch.parquet")
    tmp = FB_FP.with_suffix(".parquet.tmp")
    new.to_parquet(tmp, index=False)
    tmp.replace(FB_FP)
    print(f"✅ 已重铺写湖(备份 {bdir});ann_source 分布: "
          f"{new['ann_source'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
