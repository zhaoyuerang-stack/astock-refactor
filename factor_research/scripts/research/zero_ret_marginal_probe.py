"""zero_ret_days(Lesmond 零收益/价格停滞天数)可交易性 + 组合边际(L0,非 alpha)。

L0 体检里 zero_ret_days 是第一个 size 正交(corr 0.057)+ 增量于 Amihud 核心(残差 0.018,
OOS ICIR 0.57)的信号。纪律:IC≠可交易(holder/blend 已坑两次),必须检验:
  ① 信号空间:combined = core_z + λ·zero_ret_z,小盘核心 IS/OOS Sharpe 是否改善(size 正交→应比 blend 强);
  ② 收益空间:zero_ret-only top-25 sleeve 对所有策略的相关 + 边际 IR + 组合 Sharpe 改善。

口径同 diversifier_marginal_probe(canonical 引擎/成本/金库截断)。诚实边界:L0,非 alpha。
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.engine import CostModel  # noqa: E402
from factors.utils import mad_clip, safe_zscore  # noqa: E402
from governance.holdout import boundary  # noqa: E402
from strategies.small_cap import (  # noqa: E402
    build_rebalance_weights,
    small_cap_factor,
    small_cap_timing,
)
from workflow.phase2_backtest import load_data, run_segment  # noqa: E402

TOP_N, REBAL = 25, 20
COST = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
LAMBDAS = [0.0, 0.5, 1.0]


def _z(df):
    return safe_zscore(mad_clip(df.replace([np.inf, -np.inf], np.nan)))


def _seg(close, volume, amount, w, timing, lo, hi, lev):
    m = (close.index >= pd.Timestamp(lo)) & (close.index <= pd.Timestamp(hi))
    if m.sum() < 50:
        return None
    return run_segment(close.loc[m], volume.loc[m], amount.loc[m], w,
                       timing.loc[m] if timing is not None else None, lev, COST)


def main():
    close, volume, amount = load_data("2010-01-01")
    b = boundary()
    close, volume, amount = (x.loc[x.index < b] for x in (close, volume, amount))
    hi = str((b - pd.Timedelta(days=1)).date())
    oos_hi = str((min(pd.Timestamp("2026-12-31"), b - pd.Timedelta(days=1))).date())
    SEGS = [("IS 2018-2022", "2018-01-01", "2022-12-31"), ("OOS 2023+", "2023-01-01", oos_hi)]
    timing = small_cap_timing(close, amount, ma_window=16)[0].astype(float)

    listed = close.notna()
    ret = close.pct_change(fill_method=None)
    zero_ret = _z((ret.abs().lt(1e-6) & listed).rolling(60).sum())   # 高=更停滞=更illiq
    core_z = _z(small_cap_factor(amount, window=60))

    print("=" * 80)
    print("① 信号空间:小盘核心 combined = core_z + λ·zero_ret_z(lev=1.25)")
    print("=" * 80)
    print(f"{'λ':>5} {'段':<14} {'annual':>9} {'sharpe':>8} {'maxdd':>8}")
    for lam in LAMBDAS:
        comb = core_z if lam == 0 else _z(core_z + lam * zero_ret)
        w = build_rebalance_weights(comb, close, TOP_N, REBAL)
        for label, lo, hh in SEGS:
            r = _seg(close, volume, amount, w, timing, lo, hh, 1.25)
            if r:
                print(f"{lam:>5} {label:<14} {r['annual']:>+8.1%} {r['sharpe']:>8.2f} {r['maxdd']:>+8.1%}")
        print("-" * 50)

    # ② 收益空间:zero_ret-only sleeve 对所有策略
    sl_r, sl_sh = (lambda r: (r["returns"], r["sharpe"]))(
        _seg(close, volume, amount, build_rebalance_weights(zero_ret, close, TOP_N, REBAL),
             timing, "2018-01-01", hi, 1.0))
    print(f"\n② 收益空间:zero_ret-only top-25 sleeve 自身 Sharpe = {sl_sh:+.2f}")
    print(f"{'策略':28s} {'n':>5} {'corr':>7} {'边际IR':>8} {'base_Sh':>8} {'组合_Sh':>8} {'w*':>5}")
    sh = lambda x: float(x.mean()/x.std()*np.sqrt(252)) if x.std() > 0 else np.nan
    files = {}
    for f in glob.glob("data_lake/version_returns/*.csv"):
        fam = Path(f).stem.split("__")[0]
        if fam != "mock-family":
            files.setdefault(fam, []).append(f)
    for fam in sorted(files):
        f = next((x for x in files[fam] if "-full" in x), sorted(files[fam])[0])
        sr = pd.read_csv(f, index_col=0); sr.index = pd.to_datetime(sr.index)
        df = pd.concat([sr["ret"].rename("s"), sl_r.rename("d")], axis=1).dropna()
        if len(df) < 60:
            continue
        s, d = df["s"], df["d"]
        corr = float(np.corrcoef(s, d)[0, 1])
        beta = float(np.cov(d, s)[0, 1]/np.var(s)) if np.var(s) > 0 else np.nan
        mir = sh(d - beta * s)
        base = sh(s); best, bw = base, 0.0
        for w in [0.1, 0.25, 0.5, 1.0]:
            c = sh(s + w * d)
            if c > best:
                best, bw = c, w
        flag = " ⬆" if bw > 0 else ""
        print(f"{fam:28s} {len(df):>5} {corr:>+7.2f} {mir:>+8.2f} {base:>+8.2f} {best:>+8.2f} {bw:>5}{flag}")

    print("\n诚实边界:L0,非 alpha。λ>0 IS&OOS Sharpe 双升 / 对能赚钱策略组合 Sharpe ⬆ = zero_ret 真可交易增量。")


if __name__ == "__main__":
    main()
