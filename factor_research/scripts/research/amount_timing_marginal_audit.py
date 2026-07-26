"""Marginal contribution audit for amount-timing.

Compares amount-timing against existing small-cap-size and production
illiquidity v3.1 return streams.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/research/amount_timing_marginal_audit.py
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

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_timing
from services.actions.run_backtest import run_production_engine_backtest
from strategies.small_cap import (
    StrategyConfig,
    _drop_star,
    load_price_panels,
    run_small_cap_strategy,
)

START = "2018-01-01"
WARMUP = "2010-01-01"


def amount_returns(close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame, label: str) -> pd.Series:
    factor = amount.rank(axis=1, pct=True)
    timing, _, _ = small_cap_timing(close, amount, 16)
    result = BacktestEngine(
        PricePanel(close=close, volume=volume, amount=amount),
        BacktestConfig(start=START, cost=CostModel(), leverage=1.25),
    ).run(
        Signal(
            factor=factor,
            top_n=25,
            direction=-1,
            rebalance_freq="20D",
            timing=timing.astype(float),
            family="amount-timing",
            version=label,
        )
    )
    return result.returns


def metrics(ret: pd.Series) -> dict[str, float]:
    r = ret.dropna()
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else float("nan")
    nav = (1 + r).cumprod()
    maxdd = float((nav / nav.cummax() - 1).min())
    return {"annual": annual, "maxdd": maxdd, "sharpe": sharpe, "n": len(r)}


def fmt_pct(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:+.1%}"


def print_metrics(label: str, ret: pd.Series) -> None:
    m = metrics(ret)
    print(f"{label:<24}{fmt_pct(m['annual']):>10}{fmt_pct(m['maxdd']):>10}{m['sharpe']:>9.2f}{m['n']:>7}")


def equal_weight(returns: dict[str, pd.Series]) -> pd.Series:
    return pd.DataFrame(returns).dropna().mean(axis=1)


def compare_add(candidate_name: str, candidate: pd.Series, base: dict[str, pd.Series]) -> None:
    base_ret = equal_weight(base)
    combined_ret = equal_weight({**base, candidate_name: candidate})
    m0 = metrics(base_ret)
    m1 = metrics(combined_ret)
    print(
        f"{candidate_name:<18}"
        f"delta_ann={m1['annual'] - m0['annual']:+.1%} "
        f"delta_dd={m1['maxdd'] - m0['maxdd']:+.1%} "
        f"delta_sharpe={m1['sharpe'] - m0['sharpe']:+.2f}"
    )


def main() -> None:
    close, volume, amount = load_price_panels(WARMUP)
    close_x, volume_x, amount_x = _drop_star(close, volume, amount)

    amount_all = amount_returns(close, volume, amount, "all")
    amount_ex688 = amount_returns(close_x, volume_x, amount_x, "ex688")
    small = run_small_cap_strategy(StrategyConfig(start=START))["returns"]
    illiq, _ = run_production_engine_backtest(start=START)
    illiq_ret = illiq.returns

    streams = {
        "amount_all": amount_all,
        "amount_ex688": amount_ex688,
        "small_cap_v2": small,
        "illiq_v3.1": illiq_ret,
    }

    print("=" * 78)
    print("amount-timing marginal audit")
    print("=" * 78)

    print("\n[1] Standalone return streams")
    print(f"{'name':<24}{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'n':>7}")
    print("-" * 60)
    for name, ret in streams.items():
        print_metrics(name, ret)

    print("\n[2] Correlation matrix")
    corr = pd.DataFrame(streams).dropna().corr()
    print(corr.round(3).to_string())

    print("\n[3] Equal-weight marginal contribution")
    base_small = {"small_cap_v2": small}
    base_illiq = {"illiq_v3.1": illiq_ret}
    base_book = {"small_cap_v2": small, "illiq_v3.1": illiq_ret}

    print("against small_cap_v2:")
    compare_add("amount_all", amount_all, base_small)
    compare_add("amount_ex688", amount_ex688, base_small)

    print("against illiq_v3.1:")
    compare_add("amount_all", amount_all, base_illiq)
    compare_add("amount_ex688", amount_ex688, base_illiq)

    print("against small_cap_v2 + illiq_v3.1:")
    compare_add("amount_all", amount_all, base_book)
    compare_add("amount_ex688", amount_ex688, base_book)


if __name__ == "__main__":
    main()
