"""Band-timing scan for max annual return under max drawdown < 15%.

Focused search:
- band timing only
- exclude STAR board 688
- ADV20 floor
- top_n
- rebalance_days
- leverage step 0.1

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/research/amount_timing_band_dd_scan.py
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
from lake.load_lake import load_raw_close, load_tushare_panel
from scripts.research.amount_timing_priority_audit import load_list_dates, load_st_history
from strategies.small_cap import load_price_panels

START = "2018-01-01"
WARMUP = "2010-01-01"
MIN_AGE_DAYS = 60
TOP_NS = [5, 10, 15, 20, 25]
REBAL_DAYS = [20, 40]
LEVERAGES = [round(x, 2) for x in np.arange(0.1, 1.01, 0.1)]
ADV_FLOORS = [5_000_000, 10_000_000, 20_000_000]


def amount_factor(amount: pd.DataFrame) -> pd.DataFrame:
    return amount.rank(axis=1, pct=True)


def metrics(ret: pd.Series) -> dict[str, float]:
    r = ret.dropna()
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else float("nan")
    nav = (1.0 + r).cumprod()
    maxdd = float((nav / nav.cummax() - 1.0).min())
    return {"annual": annual, "maxdd": maxdd, "sharpe": sharpe, "n": len(r)}


def load_extras():
    close, volume, amount = load_price_panels(WARMUP)
    idx = close.index
    cols = close.columns
    raw_close = load_raw_close(start=WARMUP).reindex(index=idx, columns=cols)
    adv20 = amount.rolling(20).mean()
    st = load_st_history(idx, cols)
    list_dates = load_list_dates()
    suspend = load_tushare_panel("suspend", idx, fields=["suspend_type"], codes=list(cols))["suspend_type"]
    limits = load_tushare_panel("stk_limit", idx, fields=["up_limit", "down_limit"], codes=list(cols))
    up = limits["up_limit"].reindex(index=idx, columns=cols)
    down = limits["down_limit"].reindex(index=idx, columns=cols)
    return close, volume, amount, raw_close, adv20, st, list_dates, suspend, up, down


def tradable_mask(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    raw_close: pd.DataFrame,
    adv20: pd.DataFrame,
    st: pd.DataFrame,
    list_dates: pd.Series,
    suspend: pd.DataFrame,
    up: pd.DataFrame,
    down: pd.DataFrame,
    *,
    min_adv20: float,
) -> pd.DataFrame:
    idx, cols = close.index, close.columns
    raw_close = raw_close.reindex(index=idx, columns=cols)
    adv20 = adv20.reindex(index=idx, columns=cols)
    st = st.reindex(index=idx, columns=cols).fillna(False).astype(bool)
    suspend = suspend.reindex(index=idx, columns=cols)
    up = up.reindex(index=idx, columns=cols)
    down = down.reindex(index=idx, columns=cols)

    age = pd.DataFrame(False, index=idx, columns=cols)
    for code in cols:
        first = list_dates.get(code, pd.NaT)
        if pd.isna(first):
            continue
        age[code] = (idx - first).days >= MIN_AGE_DAYS

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
        & ~st
        & suspend.isna()
        & not_limit
    )
    star_cols = [c for c in cols if str(c).startswith("688")]
    if star_cols:
        mask.loc[:, star_cols] = False
    return mask


def build_weights(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    mask: pd.DataFrame,
    *,
    top_n: int,
    rebalance_days: int,
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


def run_case(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    dist: pd.Series,
    weights: pd.DataFrame,
    *,
    leverage: float,
    label: str,
) -> dict[str, float]:
    band = ((1.0 + dist.shift(1).clip(-0.5, 0.5) * 8.0).clip(0.0, 1.5) * (dist.shift(1) > 0).astype(float)).fillna(0.0)
    result = BacktestEngine(
        PricePanel(close=close, volume=volume, amount=amount),
        BacktestConfig(start=START, cost=CostModel(), leverage=leverage),
    ).run(
        Signal(
            weights=weights,
            timing=band,
            exposure_cap=1.5,
            family="amount-timing",
            version=label,
        )
    )
    m = metrics(result.returns)
    m["leverage"] = leverage
    m["n_rebalances"] = len(weights)
    return m


def main() -> None:
    close, volume, amount, raw_close, adv20, st, list_dates, suspend, up, down = load_extras()
    factor = amount_factor(amount)
    _, _, dist = small_cap_timing(close, amount, 16)

    print("=" * 96)
    print("amount-timing band low-drawdown search")
    print("=" * 96)
    print(f"data: {close.shape[1]} stocks x {close.shape[0]} days [{close.index[0].date()} ~ {close.index[-1].date()}]")

    rows = []
    for min_adv20 in ADV_FLOORS:
        mask = tradable_mask(
            close, amount, raw_close, adv20, st, list_dates, suspend, up, down, min_adv20=min_adv20
        )
        for top_n in TOP_NS:
            for rebalance_days in REBAL_DAYS:
                weights = build_weights(factor, close, mask, top_n=top_n, rebalance_days=rebalance_days)
                if weights.empty:
                    continue
                for leverage in LEVERAGES:
                    row = run_case(
                        close,
                        volume,
                        amount,
                        dist,
                        weights,
                        leverage=leverage,
                        label=f"band_adv{int(min_adv20/1e6)}M_n{top_n}_r{rebalance_days}_lev{leverage}",
                    )
                    row.update(
                        {
                            "min_adv20": min_adv20,
                            "top_n": top_n,
                            "rebalance_days": rebalance_days,
                        }
                    )
                    rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        print("No valid combinations found.")
        return

    feasible = df[df["maxdd"].abs() <= 0.15].copy().sort_values(["annual", "sharpe"], ascending=[False, False])
    print("\n[1] Top feasible combos (maxdd <= 15%)")
    if feasible.empty:
        print("No combination met max drawdown <= 15%.")
    else:
        print(f"{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'lev':>6}{'adv':>8}{'top_n':>7}{'rebal':>7}{'n':>7}")
        print("-" * 76)
        for _, r in feasible.head(20).iterrows():
            print(
                f"{r['annual']:+10.1%}{r['maxdd']:+10.1%}{r['sharpe']:>9.2f}"
                f"{r['leverage']:>6.2f}{int(r['min_adv20']/1e6):>8}M{int(r['top_n']):>7}{int(r['rebalance_days']):>7}{int(r['n']):>7}"
            )

    print("\n[2] Best overall band combos")
    print(f"{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'lev':>6}{'adv':>8}{'top_n':>7}{'rebal':>7}{'n':>7}")
    print("-" * 76)
    for _, r in df.sort_values(["maxdd", "annual"], ascending=[False, False]).head(10).iterrows():
        print(
            f"{r['annual']:+10.1%}{r['maxdd']:+10.1%}{r['sharpe']:>9.2f}"
            f"{r['leverage']:>6.2f}{int(r['min_adv20']/1e6):>8}M{int(r['top_n']):>7}{int(r['rebalance_days']):>7}{int(r['n']):>7}"
        )


if __name__ == "__main__":
    main()
