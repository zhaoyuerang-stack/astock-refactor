"""diversifier blend 对小盘核心的**组合边际** Sharpe/IR 贡献(L0,非 alpha)。

不把 blend 当独立 long-only sleeve(holder 已证明 diversifier 裸 long-only 必败),而是测
**信号空间组合**:combined = zscore(核心 small_cap size) + λ·blend,canonical 引擎回测,
看组合后 IS/OOS 的 Sharpe 是否随 λ 改善。λ 用固定小网格 {0,0.5,1.0} 诚实报曲线(λ=0=核心
基线),不挑参过线。附收益空间:blend-only vs 核心的相关 + 边际 IR(blend 收益对核心回归)。

口径与 phase2 一致(canonical 引擎/成本/金库截断);核心用其实跑 config(lev 1.25/top25/20d)。
诚实边界:L0,非 alpha;入册走 diversifier 轨 workflow。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.engine import CostModel  # noqa: E402
from factors.capital_flow import smart_money_divergence  # noqa: E402
from factors.northbound import northbound_accumulation  # noqa: E402
from factors.shareholder import holder_count_chg  # noqa: E402
from factors.utils import mad_clip, safe_zscore  # noqa: E402
from governance.holdout import boundary  # noqa: E402
from scripts.research import signal_source_probe as P  # noqa: E402
from strategies.small_cap import (  # noqa: E402
    build_rebalance_weights,
    small_cap_factor,
    small_cap_timing,
)
from workflow.phase2_backtest import load_data, run_segment  # noqa: E402

TOP_N, REBAL, LEV = 25, 20, 1.25
LAMBDAS = [0.0, 0.5, 1.0]
COST = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
BLEND_START = "2017-01-01"  # 限 blend 计算窗口控开销(IS 2018 起)


def _zrow(df):
    return safe_zscore(mad_clip(df))


def _build_blend(close):
    """3 信号各去 size+流动性+动量残差 → 逐截面 z → 等权平均(限 BLEND_START 后)。"""
    sub = close.loc[close.index >= pd.Timestamp(BLEND_START)]
    ctl = {k: v.reindex(sub.index) for k, v in P._load_controls(close).items()}
    nctrls = [ctl["size"], ctl["liquidity"], ctl["momentum"]]
    parts = []
    for fn in (lambda c: northbound_accumulation(c, 20),
               lambda c: holder_count_chg(c, 60),
               lambda c: smart_money_divergence(c, 20)):
        f = fn(close).reindex(sub.index)
        parts.append(_zrow(P._neutralize(f, nctrls)).values)
    blend = np.nanmean(np.stack(parts, axis=0), axis=0)
    return pd.DataFrame(blend, index=sub.index, columns=sub.columns).reindex(close.index)


def _seg_run(close, volume, amount, weights, timing, lo, hi):
    m = (close.index >= pd.Timestamp(lo)) & (close.index <= pd.Timestamp(hi))
    if m.sum() < 50:
        return None
    return run_segment(close.loc[m], volume.loc[m], amount.loc[m], weights,
                       timing.loc[m] if timing is not None else None, LEV, COST)


def main():
    close, volume, amount = load_data("2010-01-01")
    b = boundary()
    close, volume, amount = (x.loc[x.index < b] for x in (close, volume, amount))
    oos_hi = str((min(pd.Timestamp("2026-12-31"), b - pd.Timedelta(days=1))).date())
    SEGS = [("IS 2018-2022", "2018-01-01", "2022-12-31"), ("OOS 2023+", "2023-01-01", oos_hi)]

    core_z = _zrow(small_cap_factor(amount, window=60))
    blend = _build_blend(close)
    timing = small_cap_timing(close, amount, ma_window=16)[0].astype(float)

    print("=" * 78)
    print("diversifier blend 对小盘核心的组合边际(信号空间 combined = core_z + λ·blend)")
    print(f"引擎=canonical | top_n={TOP_N} rebal={REBAL} lev={LEV} | 金库截断 < {b.date()}")
    print("=" * 78)
    print(f"{'λ':>5} {'段':<14} {'annual':>9} {'sharpe':>8} {'maxdd':>8} {'turn':>7}")
    seg_returns = {}  # (lambda,label)->returns
    for lam in LAMBDAS:
        combined = core_z if lam == 0.0 else _zrow(core_z + lam * blend)
        w = build_rebalance_weights(combined, close, TOP_N, REBAL)
        for label, lo, hi in SEGS:
            r = _seg_run(close, volume, amount, w, timing, lo, hi)
            if not r:
                continue
            seg_returns[(lam, label)] = r["returns"]
            print(f"{lam:>5} {label:<14} {r['annual']:>+8.1%} {r['sharpe']:>8.2f} "
                  f"{r['maxdd']:>+8.1%} {r['turnover']:>6.1f}x")
        print("-" * 60)

    # ── 收益空间:blend-only sleeve vs 核心 的相关 + 边际 IR ──
    blend_only_w = build_rebalance_weights(blend, close, TOP_N, REBAL)
    print("\n收益空间(blend-only top-25 sleeve vs 核心 λ=0):相关 + 边际 IR(blend 收益对核心回归)")
    for label, lo, hi in SEGS:
        rb = _seg_run(close, volume, amount, blend_only_w, timing, lo, hi)
        core_r = seg_returns.get((0.0, label))
        if rb is None or core_r is None:
            continue
        bo = rb["returns"].reindex(core_r.index).fillna(0.0)
        cr = core_r.fillna(0.0)
        corr = float(np.corrcoef(cr, bo)[0, 1])
        beta = float(np.cov(bo, cr)[0, 1] / np.var(cr)) if np.var(cr) > 0 else float("nan")
        resid = bo - beta * cr
        mir = float(resid.mean() / resid.std() * np.sqrt(252)) if resid.std() > 0 else float("nan")
        print(f"  {label:<14} corr={corr:+.3f}  beta={beta:+.2f}  blend-only Sharpe={rb['sharpe']:+.2f}  边际IR={mir:+.2f}")

    print("\n诚实边界:L0,非 alpha(此处含成本/换手,但无 DSR/容量/9-Gate)。λ>0 若 IS&OOS Sharpe 双升=blend 加值核心。")


if __name__ == "__main__":
    main()
