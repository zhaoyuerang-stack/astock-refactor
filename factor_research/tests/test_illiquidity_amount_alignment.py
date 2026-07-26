"""illiquidity DSL 口径必须与 OO AmihudIlliq 对齐(amount 分母,非 volume)。

回归:曾用 |ret|/volume,等价于 Amihud×价格水平,污染搜索语义。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.alpha.base import FactorData
from factors.alpha.builtins.illiq import AmihudIlliq
from factors.momentum import illiquidity as dsl_illiq


def _panels(n_dates: int = 60, n_codes: int = 30):
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2020-01-01", periods=n_dates)
    cols = [f"{i:06d}.SZ" for i in range(n_codes)]
    # heterogeneous price levels to expose volume-vs-amount divergence
    close = pd.DataFrame(
        rng.uniform(5, 80, size=(n_dates, n_codes)).cumsum(axis=0) + 10.0,
        index=idx,
        columns=cols,
    )
    volume = pd.DataFrame(
        rng.uniform(1e4, 5e5, size=(n_dates, n_codes)),
        index=idx,
        columns=cols,
    )
    amount = volume * close
    return close, volume, amount


def test_dsl_illiquidity_matches_amihud_illiq_rank():
    close, volume, amount = _panels()
    dsl = dsl_illiq(close, volume, n=20)
    oo = AmihudIlliq(window=20).compute(FactorData(close=close, amount=amount, volume=volume))
    # row-wise rank correlation should be near 1 (same cross-sectional order)
    corrs = []
    for i in range(len(dsl)):
        a, b = dsl.iloc[i], oo.iloc[i]
        m = a.notna() & b.notna()
        if m.sum() < 10:
            continue
        ra, rb = a[m].rank(), b[m].rank()
        if ra.std() < 1e-12 or rb.std() < 1e-12:
            continue
        corrs.append(float(ra.corr(rb)))
    assert corrs, "no overlapping rows"
    assert float(np.nanmean(corrs)) > 0.99, f"mean rank-corr={np.nanmean(corrs):.4f}"


def test_volume_only_denominator_diverges_from_amount():
    """Sanity: old |ret|/volume is NOT the same as Amihud when prices differ."""
    close, volume, amount = _panels()
    ret = close.pct_change(fill_method=None).abs()
    old = (ret / (volume + 1)).rolling(20).mean()
    new = dsl_illiq(close, volume, n=20)
    corrs = []
    for i in range(len(new)):
        a, b = old.iloc[i], new.iloc[i]
        m = a.notna() & b.notna()
        if m.sum() < 10:
            continue
        ra, rb = a[m].rank(), b[m].rank()
        if ra.std() < 1e-12 or rb.std() < 1e-12:
            continue
        corrs.append(float(ra.corr(rb)))
    assert corrs
    # with heterogeneous prices, volume-only and amount-based ranks must diverge
    assert float(np.nanmean(corrs)) < 0.95


if __name__ == "__main__":
    test_dsl_illiquidity_matches_amihud_illiq_rank()
    test_volume_only_denominator_diverges_from_amount()
    print("ok")
