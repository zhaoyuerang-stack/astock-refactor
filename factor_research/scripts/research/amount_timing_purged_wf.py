"""Purged walk-forward audit for amount-timing.

Fixed-formula strategy, so the "train" window is used for warmup only; the
purge gap prevents immediate pre-test observations from setting warmup state.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/research/amount_timing_purged_wf.py
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.analysis.walk_forward import walk_forward_windows
from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_timing
from strategies.small_cap import _drop_star, load_price_panels


def metrics(ret: pd.Series) -> dict[str, float]:
    r = ret.dropna()
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else float("nan")
    nav = (1.0 + r).cumprod()
    maxdd = float((nav / nav.cummax() - 1.0).min())
    return {"annual": annual, "maxdd": maxdd, "sharpe": sharpe, "n": len(r)}


def fmt_pct(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:+.1%}"


def run_window(close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame, window: dict) -> dict:
    panel_start = window["train_start"]
    panel_end = window["test_end"]
    c = close.loc[panel_start:panel_end]
    v = volume.loc[panel_start:panel_end]
    a = amount.loc[panel_start:panel_end]
    factor = a.rank(axis=1, pct=True)
    timing, _, _ = small_cap_timing(c, a, 16)
    result = BacktestEngine(
        PricePanel(close=c, volume=v, amount=a),
        BacktestConfig(start=str(window["test_start"].date()), cost=CostModel(), leverage=1.25),
    ).run(
        Signal(
            factor=factor,
            top_n=25,
            direction=-1,
            rebalance_freq="20D",
            timing=timing.astype(float),
            family="amount-timing",
            version="purged-wf",
        )
    )
    ret = result.returns.loc[: window["test_end"]]
    m = metrics(ret)
    return {
        "test_year": int(window["test_start"].year),
        "train_start": window["train_start"],
        "train_end": window["train_end"],
        "test_start": window["test_start"],
        "test_end": window["test_end"],
        **m,
        "holding": float(timing.reindex(ret.index).fillna(False).mean()),
    }


def run_set(label: str, close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame) -> None:
    dates = close.index[close.index >= pd.Timestamp("2010-01-01")]
    windows = walk_forward_windows(dates, train_years=3, test_years=1, purge_days=80, min_train_days=500)
    rows = [run_window(close, volume, amount, w) for w in windows if w["test_start"].year >= 2018]
    print(f"\n[{label}] purged WF, train=3y test=1y purge=80 trading-day proxy")
    print(f"{'year':<6}{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'holding':>10}{'n':>7}")
    print("-" * 56)
    for row in rows:
        print(
            f"{row['test_year']:<6}{fmt_pct(row['annual']):>10}{fmt_pct(row['maxdd']):>10}"
            f"{row['sharpe']:>9.2f}{row['holding']:>9.1%}{row['n']:>7}"
        )
    if rows:
        print("-" * 56)
        print(
            f"positive={sum(r['annual'] > 0 for r in rows)}/{len(rows)} "
            f"mean_annual={np.mean([r['annual'] for r in rows]):+.1%} "
            f"mean_maxdd={np.mean([r['maxdd'] for r in rows]):+.1%} "
            f"mean_sharpe={np.mean([r['sharpe'] for r in rows]):.2f}"
        )


def main() -> None:
    close, volume, amount = load_price_panels("2010-01-01")
    run_set("all", close, volume, amount)
    close_x, volume_x, amount_x = _drop_star(close, volume, amount)
    run_set("ex688", close_x, volume_x, amount_x)


if __name__ == "__main__":
    main()
