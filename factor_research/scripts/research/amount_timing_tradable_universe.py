"""Tradable-universe validation for amount-rank + MA16 timing.

This script tests whether the amount strategy survives practical filters:
- exclude ST
- exclude stocks listed for less than 60 calendar days
- exclude suspended names on signal date
- exclude names closing at up/down limit on signal date
- require selected names to satisfy ADV20 floor or AUM participation cap

It is read-only research code. It does not register or promote a strategy.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/research/amount_timing_tradable_universe.py
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
from lake.load_lake import load_raw_close, load_tushare_panel
from scripts.research.amount_timing_priority_audit import (
    audit_universe,
    capacity_table,
    fmt_pct,
    load_list_dates,
    load_st_history,
    print_universe_summary,
)
from services.actions.run_backtest import run_production_engine_backtest
from strategies.small_cap import (
    StrategyConfig,
    _drop_star,
    load_price_panels,
    run_small_cap_strategy,
)

START = "2018-01-01"
WARMUP = "2010-01-01"
TOP_N = 25
LEVERAGE = 1.25
MAX_PARTICIPATION = 0.10


def metrics(ret: pd.Series) -> dict[str, float]:
    r = ret.dropna()
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else float("nan")
    nav = (1.0 + r).cumprod()
    maxdd = float((nav / nav.cummax() - 1.0).min())
    return {"annual": annual, "maxdd": maxdd, "sharpe": sharpe, "n": len(r)}


def fmt_num(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:.2f}"


def amount_factor(amount: pd.DataFrame) -> pd.DataFrame:
    return amount.rank(axis=1, pct=True)


def tradable_mask(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    min_adv20: float,
    exclude_688: bool = False,
    min_age_days: int = 60,
) -> pd.DataFrame:
    idx, cols = close.index, close.columns
    raw_close = load_raw_close(start=WARMUP).reindex(index=idx, columns=cols)
    adv20 = amount.rolling(20).mean()
    st = load_st_history(idx, cols)
    list_dates = load_list_dates()
    suspend = load_tushare_panel("suspend", idx, fields=["suspend_type"], codes=list(cols))["suspend_type"]
    limits = load_tushare_panel("stk_limit", idx, fields=["up_limit", "down_limit"], codes=list(cols))
    up = limits["up_limit"].reindex(index=idx, columns=cols)
    down = limits["down_limit"].reindex(index=idx, columns=cols)

    age = pd.DataFrame(False, index=idx, columns=cols)
    for code in cols:
        first = list_dates.get(code, pd.NaT)
        if pd.isna(first):
            continue
        age[code] = (idx - first).days >= min_age_days

    not_limit = ~(
        np.isclose(raw_close, up, rtol=0, atol=1e-4)
        | np.isclose(raw_close, down, rtol=0, atol=1e-4)
    )
    mask = (
        close.notna()
        & amount.notna()
        & (amount > 0)
        & (adv20 >= min_adv20)
        & age
        & ~st.reindex(index=idx, columns=cols).fillna(False).astype(bool)
        & suspend.reindex(index=idx, columns=cols).isna()
        & not_limit
    )
    if exclude_688:
        star_cols = [c for c in cols if str(c).startswith("688")]
        if star_cols:
            mask.loc[:, star_cols] = False
    return mask


def build_masked_weights(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    mask: pd.DataFrame,
    *,
    top_n: int = TOP_N,
    rebalance_days: int = 20,
) -> pd.DataFrame:
    rows = []
    fdates = factor.dropna(how="all").index.intersection(close.index)
    for rd in list(fdates[::rebalance_days]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        effective = close.index[pos + 1]
        eligible = mask.loc[rd]
        names = eligible[eligible].index
        f = factor.loc[rd].reindex(names).dropna()
        if len(f) < top_n:
            continue
        selected = f.nsmallest(top_n).index
        rows.append(pd.Series(1.0 / top_n, index=selected, name=effective))
    if not rows:
        return pd.DataFrame(index=pd.DatetimeIndex([], dtype="datetime64[ns]"))
    out = pd.DataFrame(rows).fillna(0.0)
    out.index = pd.DatetimeIndex(out.index)
    return out


def run_weights(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    weights: pd.DataFrame,
    label: str,
    start: str = START,
) -> tuple[dict, pd.Series, pd.Series]:
    timing, _, _ = small_cap_timing(close, amount, 16)
    result = BacktestEngine(
        PricePanel(close=close, volume=volume, amount=amount),
        BacktestConfig(start=start, cost=CostModel(), leverage=LEVERAGE),
    ).run(
        Signal(
            weights=weights,
            timing=timing.astype(float),
            family="amount-timing-tradable",
            version=label,
        )
    )
    return {"case": label, **metrics(result.returns), "n_rebalances": len(weights)}, result.returns, timing


def scenario(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    label: str,
    min_adv20: float,
    exclude_688: bool = False,
) -> tuple[dict, pd.DataFrame, pd.Series]:
    mask = tradable_mask(close, amount, min_adv20=min_adv20, exclude_688=exclude_688)
    weights = build_masked_weights(amount_factor(amount), close, mask)
    row, ret, _ = run_weights(close, volume, amount, weights, label)
    return row, weights, ret


def print_rows(rows: list[dict]) -> None:
    print(f"{'case':<22}{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'rebal':>7}{'n':>7}")
    print("-" * 72)
    for row in rows:
        print(
            f"{row['case']:<22}{fmt_pct(row['annual']):>10}{fmt_pct(row['maxdd']):>10}"
            f"{fmt_num(row['sharpe']):>9}{row['n_rebalances']:>7}{row['n']:>7}"
        )


def equal_weight(returns: dict[str, pd.Series]) -> pd.Series:
    return pd.DataFrame(returns).dropna().mean(axis=1)


def compare_add(candidate_name: str, candidate: pd.Series, base: dict[str, pd.Series]) -> str:
    m0 = metrics(equal_weight(base))
    m1 = metrics(equal_weight({**base, candidate_name: candidate}))
    return (
        f"{candidate_name:<22}"
        f"delta_ann={m1['annual'] - m0['annual']:+.1%} "
        f"delta_dd={m1['maxdd'] - m0['maxdd']:+.1%} "
        f"delta_sharpe={m1['sharpe'] - m0['sharpe']:+.2f}"
    )


def purged_wf(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    label: str,
    min_adv20: float,
    exclude_688: bool,
) -> list[dict]:
    dates = close.index
    windows = walk_forward_windows(dates, train_years=3, test_years=1, purge_days=80, min_train_days=500)
    rows = []
    for w in windows:
        if w["test_start"].year < 2018:
            continue
        panel_start = w["train_start"]
        panel_end = w["test_end"]
        c = close.loc[panel_start:panel_end]
        v = volume.loc[panel_start:panel_end]
        a = amount.loc[panel_start:panel_end]
        mask = tradable_mask(c, a, min_adv20=min_adv20, exclude_688=exclude_688)
        weights = build_masked_weights(amount_factor(a), c, mask)
        row, ret, timing = run_weights(c, v, a, weights, f"{label}_{w['test_start'].year}", start=str(w["test_start"].date()))
        ret = ret.loc[: w["test_end"]]
        row = {**row, **metrics(ret)}
        row["year"] = int(w["test_start"].year)
        row["holding"] = float(timing.reindex(ret.index).fillna(False).mean())
        rows.append(row)
    return rows


def main() -> None:
    close, volume, amount = load_price_panels(WARMUP)
    print("=" * 86)
    print("amount-timing tradable universe validation")
    print("=" * 86)
    print(f"data: {close.shape[1]} stocks x {close.shape[0]} days [{close.index[0].date()} ~ {close.index[-1].date()}]")

    rows = []
    runs: dict[str, tuple[pd.DataFrame, pd.Series]] = {}
    for adv_m in [2, 5, 10, 20, 50]:
        label = f"tradable_adv{adv_m}M"
        row, weights, ret = scenario(close, volume, amount, label=label, min_adv20=adv_m * 1_000_000)
        rows.append(row)
        runs[label] = (weights, ret)
    for adv_m in [10, 20]:
        label = f"ex688_adv{adv_m}M"
        c, v, a = _drop_star(close, volume, amount)
        row, weights, ret = scenario(c, v, a, label=label, min_adv20=adv_m * 1_000_000, exclude_688=True)
        rows.append(row)
        runs[label] = (weights, ret)

    print("\n[1] Tradable universe scenario sweep")
    print_rows(rows)

    chosen = "tradable_adv10M"
    chosen_weights, chosen_ret = runs[chosen]
    audit = audit_universe(close, volume, amount, chosen_weights)
    print_universe_summary(chosen, audit)

    print("\n[2] Capacity proxy after tradable filter")
    print(f"{'aum':>12}{'med_part':>12}{'p95_part':>12}{'med_worst':>12}{'p95_worst':>12}")
    print("-" * 60)
    for row in capacity_table(audit, aums=(5_000_000, 50_000_000, 100_000_000, 500_000_000)):
        print(
            f"{row['aum']/1e6:>10.0f}M"
            f"{row['median_participation']:>12.1%}"
            f"{row['p95_participation']:>12.1%}"
            f"{row['median_worst_name']:>12.1%}"
            f"{row['p95_worst_name']:>12.1%}"
        )

    print("\n[3] Latest holdings for chosen scenario")
    latest = close.index[-1]
    wd = chosen_weights.index[chosen_weights.index <= latest][-1]
    names = chosen_weights.loc[wd]
    names = names[names > 0].index.astype(str)
    print(f"{chosen}: weight_date={wd.date()} star={sum(c.startswith('688') for c in names)}/{len(names)}")
    print(", ".join(names))

    print("\n[4] Marginal contribution of chosen scenario")
    small = run_small_cap_strategy(StrategyConfig(start=START))["returns"]
    illiq, _ = run_production_engine_backtest(start=START)
    illiq_ret = illiq.returns
    print(compare_add(chosen, chosen_ret, {"small_cap_v2": small}))
    print(compare_add(chosen, chosen_ret, {"illiq_v3.1": illiq_ret}))
    print(compare_add(chosen, chosen_ret, {"small_cap_v2": small, "illiq_v3.1": illiq_ret}))
    corr = pd.DataFrame(
        {
            chosen: chosen_ret,
            "small_cap_v2": small,
            "illiq_v3.1": illiq_ret,
        }
    ).dropna().corr()
    print(corr.round(3).to_string())

    print("\n[5] Purged WF for chosen scenario")
    wf_rows = purged_wf(close, volume, amount, label=chosen, min_adv20=10_000_000, exclude_688=False)
    print(f"{'year':<6}{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'holding':>10}{'rebal':>7}")
    print("-" * 60)
    for row in wf_rows:
        print(
            f"{row['year']:<6}{fmt_pct(row['annual']):>10}{fmt_pct(row['maxdd']):>10}"
            f"{fmt_num(row['sharpe']):>9}{row['holding']:>9.1%}{row['n_rebalances']:>7}"
        )
    if wf_rows:
        print(
            f"positive={sum(r['annual'] > 0 for r in wf_rows)}/{len(wf_rows)} "
            f"mean_annual={np.mean([r['annual'] for r in wf_rows]):+.1%} "
            f"mean_maxdd={np.mean([r['maxdd'] for r in wf_rows]):+.1%} "
            f"mean_sharpe={np.mean([r['sharpe'] for r in wf_rows]):.2f}"
        )


if __name__ == "__main__":
    main()
