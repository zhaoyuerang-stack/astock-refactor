"""blend / 北向单独 sleeve 对**所有** canonical 策略的组合价值(L0,非 alpha)。

回答"不能和其它策略组合吗":对每个策略的收益序列(data_lake/version_returns),量
sleeve 的 ① 相关 ② 边际 IR(去 base beta 后 α 的 IR) ③ 组合 Sharpe 改善(portfolio:
找 w 最大化 Sharpe(strat + w·sleeve),与 w=0 比)。后者才是"组合分散"的真问——
低相关 + 正收益的 sleeve 即便自身 Sharpe 低也能提组合;负 Sharpe sleeve 一般提不动。

sleeve = blend(北向+holder+smart_div 中性化等权)/ northbound-only,top-25 long,house MA16,lev=1.0。
诚实边界:L0,非 alpha。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

import glob

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.engine import CostModel  # noqa: E402
from factors.northbound import northbound_accumulation  # noqa: E402
from governance.holdout import boundary  # noqa: E402
from scripts.research.diversifier_marginal_probe import _build_blend, _zrow  # noqa: E402
from strategies.small_cap import build_rebalance_weights, small_cap_timing  # noqa: E402
from workflow.phase2_backtest import load_data, run_segment  # noqa: E402

TOP_N, REBAL = 25, 20
COST = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
LO, HI = "2018-01-01", None  # HI=金库前


def _sleeve_returns(close, volume, amount, factor, timing, lo, hi):
    w = build_rebalance_weights(factor, close, TOP_N, REBAL)
    m = (close.index >= pd.Timestamp(lo)) & (close.index <= pd.Timestamp(hi))
    r = run_segment(close.loc[m], volume.loc[m], amount.loc[m], w,
                    timing.loc[m] if timing is not None else None, 1.0, COST)
    return r["returns"], r["sharpe"]


def _combine(strat_r, sleeve_r):
    """对齐 + 相关/边际IR/组合Sharpe改善。"""
    df = pd.concat([strat_r.rename("s"), sleeve_r.rename("d")], axis=1).dropna()
    if len(df) < 60:
        return None
    s, d = df["s"].values, df["d"].values
    corr = float(np.corrcoef(s, d)[0, 1])
    beta = float(np.cov(d, s)[0, 1] / np.var(s)) if np.var(s) > 0 else np.nan
    resid = d - beta * s
    mir = float(resid.mean() / resid.std() * np.sqrt(252)) if resid.std() > 0 else np.nan
    sh = lambda x: float(x.mean() / x.std() * np.sqrt(252)) if x.std() > 0 else np.nan
    base = sh(df["s"])
    best_w, best = 0.0, base
    for w in [0.1, 0.25, 0.5, 1.0]:
        c = sh(df["s"] + w * df["d"])
        if c > best:
            best, best_w = c, w
    return dict(n=len(df), corr=corr, mir=mir, base_sharpe=base,
                best_combined=best, best_w=best_w, d_sharpe=sh(df["d"]))


def main():
    close, volume, amount = load_data("2010-01-01")
    b = boundary()
    close, volume, amount = (x.loc[x.index < b] for x in (close, volume, amount))
    hi = str((b - pd.Timedelta(days=1)).date())
    timing = small_cap_timing(close, amount, ma_window=16)[0].astype(float)

    blend = _build_blend(close)
    nb = _zrow(northbound_accumulation(close, 20))

    print("=" * 92)
    print("blend / 北向单独 对所有 canonical 策略的组合价值(2018+,sleeve=top25 long house-MA16 lev1.0)")
    print("=" * 92)
    sleeves = {}
    for name, fac in [("blend", blend), ("northbound", nb)]:
        r, sh = _sleeve_returns(close, volume, amount, fac, timing, LO, hi)
        sleeves[name] = r
        print(f"  sleeve {name:10s} 自身 Sharpe = {sh:+.2f}")

    files = {}
    for f in glob.glob("data_lake/version_returns/*.csv"):
        fam = Path(f).stem.split("__")[0]
        if fam == "mock-family":
            continue
        files.setdefault(fam, []).append(f)
    # 每族取 -full 优先,否则第一个
    chosen = {fam: next((f for f in fs if "-full" in f), sorted(fs)[0]) for fam, fs in files.items()}

    for sl_name, sl_r in sleeves.items():
        print(f"\n── sleeve = {sl_name} ──")
        print(f"{'策略':28s} {'n':>4} {'corr':>7} {'边际IR':>8} {'base_Sh':>8} {'组合_Sh':>8} {'w*':>5}")
        for fam, f in sorted(chosen.items()):
            sr = pd.read_csv(f, index_col=0)
            sr.index = pd.to_datetime(sr.index)
            res = _combine(sr["ret"], sl_r)
            if not res:
                continue
            flag = " ⬆" if res["best_combined"] > res["base_sharpe"] + 1e-9 and res["best_w"] > 0 else ""
            print(f"{fam:28s} {res['n']:>4} {res['corr']:>+7.2f} {res['mir']:>+8.2f} "
                  f"{res['base_sharpe']:>+8.2f} {res['best_combined']:>+8.2f} {res['best_w']:>5}{flag}")

    print("\n诚实边界:L0,非 alpha。⬆=该策略加该 sleeve 后 Sharpe 提升(w*>0);边际IR>0=sleeve 有去 base 后的 α。")


if __name__ == "__main__":
    main()
