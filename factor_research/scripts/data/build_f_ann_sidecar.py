"""build_f_ann_sidecar.py — 拉取 tushare f_ann_date(首次公告日)侧车表(DQ-2026-001 修复③)。

为什么:fundamental_batch 的 ann_date 误用了东财"最新公告日期"(行级最近更新日期,
会随次年披露季刷新,+365d 平台)。东财 API 无首次公告列,唯一可得的首次公告口径是
tushare f_ann_date。本脚本按股票全历史拉 income/balancesheet/cashflow 三表的
(ts_code, end_date, ann_date, f_ann_date),落侧车 parquet,供:
  · repair_fundamental_batch_ann.py 重铺 fb 的 ann_date/avail_date;
  · check_fundamental_batch_pit.py 守卫换真值源。

限速:经 lake.sources.tushare.call(0.18s 节流 + 重试)。断点续拉:已拉股票跳过。
只读源 API,写湖仅限 financials/f_ann_date_<api>.parquet(scripts/data/ 白名单内)。

用法:
  python3 scripts/data/build_f_ann_sidecar.py --api income --limit 500   # 批次拉取
  python3 scripts/data/build_f_ann_sidecar.py --api income --status      # 看进度
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # factor_research/
sys.path.insert(0, str(ROOT))

from lake.sources.tushare import call  # noqa: E402

LAKE = ROOT / "data_lake"
STATEMENTS = ["income", "balancesheet", "cashflow"]
FIELDS = "ts_code,end_date,ann_date,f_ann_date"


def sidecar_fp(api: str) -> Path:
    return LAKE / "financials" / f"f_ann_date_{api}.parquet"


def done_fp(api: str) -> Path:
    """进度文件:已处理 ts_code(含空响应),防跨批次空转重试。"""
    return LAKE / "financials" / f"f_ann_date_{api}.done.json"


def infer_ts_code(code: str) -> str:
    """本仓 6 位 code → tushare ts_code(带交易所后缀)。"""
    if code.startswith(("60", "68", "90")):
        return f"{code}.SH"
    if code.startswith(("43", "83", "87", "88", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"  # 000/001/002/003/300/301/200


def stock_universe() -> list[str]:
    """全集 = 三大报表 ts_code ∪ fb code(推断后缀),排序保证断点稳定。"""
    codes: set[str] = set()
    for t in STATEMENTS:
        fp = LAKE / "financials" / f"{t}_all.parquet"
        if fp.exists():
            codes.update(pd.read_parquet(fp, columns=["ts_code"])["ts_code"].unique())
    fb_fp = LAKE / "fundamental_batch.parquet"
    if fb_fp.exists():
        fb_codes = pd.read_parquet(fb_fp, columns=["code"])["code"].astype(str).str.zfill(6).unique()
        codes.update(infer_ts_code(c) for c in fb_codes)
    return sorted(codes)


def _load_done(api: str, fp: Path) -> set[str]:
    """已完成集合 = parquet 中的 ts_code ∪ 进度文件(含空响应标记)。"""
    done: set[str] = set()
    if fp.exists():
        done.update(pd.read_parquet(fp, columns=["ts_code"])["ts_code"].unique())
    dp = done_fp(api)
    if dp.exists():
        import json
        done.update(json.loads(dp.read_text()))
    return done


def status(api: str, universe: list[str]) -> None:
    fp = sidecar_fp(api)
    rows = len(pd.read_parquet(fp)) if fp.exists() else 0
    done = len(_load_done(api, fp))
    print(f"[{api}] 已处理 {done}/{len(universe)} 股 (有数据 {rows} 行),进度 {done/len(universe):.1%}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="income", choices=STATEMENTS)
    ap.add_argument("--limit", type=int, default=500, help="本次最多拉多少只(节流 0.18s/次)")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    universe = stock_universe()
    if args.status:
        for a in STATEMENTS:
            status(a, universe)
        return 0

    fp = sidecar_fp(args.api)
    done_codes = _load_done(args.api, fp)
    frames: list[pd.DataFrame] = []
    if fp.exists():
        frames.append(pd.read_parquet(fp))

    todo = [c for c in universe if c not in done_codes][: args.limit]
    print(f"[{args.api}] 全集 {len(universe)} 股,已处理 {len(done_codes)},本批 {len(todo)} 只(全新股票)")

    def _flush() -> None:
        out = pd.concat(frames, ignore_index=True).drop_duplicates(
            ["ts_code", "end_date", "ann_date"], keep="last")
        out.to_parquet(fp, index=False)
        import json
        done_fp(args.api).write_text(json.dumps(sorted(done_codes)))

    n_new = 0
    for i, ts in enumerate(todo):
        try:
            df = call(args.api, {"ts_code": ts}, fields=FIELDS)
        except RuntimeError as e:
            print(f"  ⚠️ {ts}: {e}")
            continue
        if df is not None and not df.empty:
            frames.append(df)
            n_new += 1
        done_codes.add(ts)  # 空响应也记为已处理(该股票无此表数据)
        if (i + 1) % 200 == 0:
            _flush()  # 检查点:超时断档损失 <200 只
            print(f"  {i+1}/{len(todo)} (新增行 {n_new},已落盘)", flush=True)

    _flush()
    print(f"✅ [{args.api}] 本批完成,已处理累计 {len(done_codes)}/{len(universe)} 股")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
