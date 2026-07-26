"""small-cap + λ·zero_ret_days(价格停滞)组合策略走 canonical workflow(R-WF-001)。

zero_ret_days 是研究线第一个过可交易性闸门的信号(size 正交 0.057,组合边际 Sharpe IS/OOS 双升)。
本脚本走 phase1-3 看组合策略过不过防未来/hit/WF(便宜闸,零台账写入);过了再建 9-Gate/DSR。

组合因子: combined = zscore(small_cap_factor) + λ·zscore(zero_ret_days),house MA16 二值择时。
λ=0.5(marginal probe 的 OOS 甜点);**λ + 流动性族 4 因子筛选都是搜索自由度,入册须计入 n_trials。**
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

import numpy as np  # noqa: E402

from factors.microstructure import zero_ret_days  # noqa: E402 —— canonical 因子
from factors.small_cap import small_cap_factor, small_cap_timing  # noqa: E402
from factors.utils import mad_clip, safe_zscore  # noqa: E402

FAMILY = "small-cap-staleness"
VERSION = "v1.0"
LAMBDA = 0.5
WIN = 60
TOP_N, REBAL = 25, 20
CONFIG = {"top_n": TOP_N, "rebalance_days": REBAL, "leverage": 1.25,
          "buy_cost": 0.00225, "sell_cost": 0.00275, "financing_rate": 0.065}
HYPOTHESIS = ("小盘规模溢价(核心)+ 价格停滞 zero-return-days(Lesmond 流动性维度,size 正交)"
              "tilt:在小盘里偏向价格停滞=被忽视/低关注的票,增量分散。")


def _z(df):
    return safe_zscore(mad_clip(df.replace([np.inf, -np.inf], np.nan)))


def factor_builder(close, volume, amount, dates):
    core = _z(small_cap_factor(amount, window=WIN))
    zr = zero_ret_days(close, n=WIN)  # canonical 因子(factors.microstructure)
    return _z(core + LAMBDA * zr)


def timing_builder(close, amount):
    return small_cap_timing(close, amount, ma_window=16)[0].astype(float)


def main():
    from engine.metrics import compute_hit
    from workflow.phase1_synthetic import Phase1Checker
    from workflow.phase2_backtest import Phase2Runner
    from workflow.phase3_wf import WF3Runner

    print(f"\n{'='*64}\n  {FAMILY} → phase1-3 (small_cap + {LAMBDA}·zero_ret)\n{'='*64}", flush=True)

    print("\n[phase1] 合成防未来审计...", flush=True)
    p1 = Phase1Checker(factor_builder, timing_builder, FAMILY, CONFIG).run_all(use_clean=True, save_lessons=False)
    fails = [r for r in p1 if getattr(r, "is_fail", False)]
    print(f"  → {'PASS' if not fails else 'FAIL ' + str([r.check_id for r in fails])}", flush=True)

    print("\n[phase2] 三段回测...", flush=True)
    p2 = Phase2Runner(factor_builder, timing_builder, FAMILY, CONFIG).run(warmup_start="2010-01-01")
    segs = p2.get("segments", {})
    for k in segs:
        s = segs[k]
        print(f"    {k:8s}: annual={s.get('annual')!s:>9} maxdd={s.get('maxdd')!s:>9} sharpe={s.get('sharpe')!s:>7}", flush=True)
    print(f"    cost_sens={p2.get('cost_sensitivity',{}).get('verdict')} "
          f"corr={p2.get('correlation',{}).get('verdict')}", flush=True)

    print("\n[phase3] walk-forward...", flush=True)
    p3 = WF3Runner(factor_builder, timing_builder, FAMILY, CONFIG).run(warmup_start="2010-01-01")
    agg = p3.get("aggregate", {})

    wf_hit = compute_hit(agg.get("annual"), agg.get("maxdd"))
    oos = segs.get("oos", {}) or next((segs[k] for k in segs if "OOS" in k or "oos" in k), {})
    oos_hit = (compute_hit(oos.get("annual"), oos.get("maxdd"))
               if oos.get("annual") is not None and oos.get("maxdd") is not None else None)

    print(f"\n{'='*64}", flush=True)
    print(f"  phase1 防未来: {'PASS' if not fails else 'FAIL'}", flush=True)
    print(f"  WF aggregate : annual={agg.get('annual')} maxdd={agg.get('maxdd')} "
          f"sharpe={agg.get('sharpe')} verdict={agg.get('verdict')}", flush=True)
    print(f"  hit(WF)={wf_hit}  hit(OOS)={oos_hit}", flush=True)
    print(f"  → {'够到 hit,值得 9-Gate/DSR' if wf_hit else '未达 hit'}", flush=True)
    print(f"{'='*64}", flush=True)


if __name__ == "__main__":
    main()
