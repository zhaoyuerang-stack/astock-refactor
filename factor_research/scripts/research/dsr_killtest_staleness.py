"""small-cap-staleness 的 DSR kill-test(advisor:standalone 唯一裁决量,先拿到再决定投不投入)。

诚实:observed_sr 用前瞻口径(全现代 2018-2024 / OOS 段),不用 IS 的 1.12(挑最好窗口=修记分牌)。
n_trials 取 12(族内:流动性族 4 因子 × λ 网格 3)与 30(保守:含更广正交源搜索)两档看敏感性。
dsr_p≥0.05 → standalone 当前证据不通,停在已入册 diversifier。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.analysis.walk_forward import deflated_sharpe  # noqa: E402
from core.engine import CostModel  # noqa: E402
from governance.holdout import boundary  # noqa: E402
from scripts.research.promote_smallcap_staleness import factor_builder, timing_builder  # noqa: E402
from strategies.small_cap import build_rebalance_weights  # noqa: E402
from workflow.phase2_backtest import load_data, run_segment  # noqa: E402

COST = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)


def _ret(close, volume, amount, lo, hi):
    fac = factor_builder(close, volume, amount, close.index)
    w = build_rebalance_weights(fac, close, 25, 20)
    t = timing_builder(close, amount)
    m = (close.index >= pd.Timestamp(lo)) & (close.index <= pd.Timestamp(hi))
    r = run_segment(close.loc[m], volume.loc[m], amount.loc[m], w, t.loc[m], 1.25, COST)
    return r["returns"]


def _dsr(returns, label):
    r = returns.dropna()
    n = len(r)
    sr_ann = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else float("nan")
    sk = float(r.skew()); ku = float(r.kurtosis() + 3.0)
    print(f"\n[{label}] n={n} 日, 年化Sharpe={sr_ann:.3f}, skew={sk:.2f}, kurt={ku:.2f}")
    for nt in (12, 30):
        d = deflated_sharpe(sr_ann, n_trials=nt, n_periods=n, skew=sk, kurt=ku)
        print(f"   n_trials={nt:>2}: DSR={d['dsr']:+.3f}  dsr_p={d['p_value']:.4f}  "
              f"{'✅显著(<0.05)' if d['p_value'] < 0.05 else '❌不显著(≥0.05)'}")


def main():
    close, volume, amount = load_data("2010-01-01")
    b = boundary()
    close, volume, amount = (x.loc[x.index < b] for x in (close, volume, amount))
    oos_hi = str((b - pd.Timedelta(days=1)).date())

    print("=" * 70)
    print("small-cap-staleness DSR kill-test(前瞻口径,非 IS)")
    print("=" * 70)
    _dsr(_ret(close, volume, amount, "2018-01-01", oos_hi), "全现代 2018-2024(IS+OOS)")
    _dsr(_ret(close, volume, amount, "2023-01-01", oos_hi), "OOS 2023-2024(纯样本外)")

    print("\n判读:standalone = hit AND dsr_p<0.05。dsr_p≥0.05 → 当前证据 standalone 不通,"
          "停在已入册 diversifier。诚实边界:DSR 用前瞻 Sharpe,n_trials 含搜索自由度。")


if __name__ == "__main__":
    main()
