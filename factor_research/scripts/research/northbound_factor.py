"""Northbound (北向资金) accumulation factor.

Factor: 20d change in northbound hold percentage, smoothed 5d.
Universe: stocks with positive northbound holdings.
Backtest: quarterly rebalance, top 30, equal weight.

Key question: is northbound accumulation orthogonal to small-cap v2.0?
Expected correlation < 0.3 (northbound focuses on large/mid-cap, different driver).

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/northbound_factor.py
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    run_small_cap_strategy,
)

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    print("=== Northbound Accumulation Factor ===")

    # ── Load northbound data ──
    print("\n[1] Loading northbound data...", flush=True)
    nb = pd.read_parquet("data_lake/capital/northbound_all.parquet",
                        columns=["date","code","northbound_hold_pct","northbound_hold_shares_chg_1d"])
    nb["date"] = pd.to_datetime(nb["date"])
    print(f"  Range: {nb['date'].min().date()} ~ {nb['date'].max().date()}")
    print(f"  Codes: {nb['code'].nunique():,}")
    print(f"  Rows: {len(nb):,}", flush=True)

    # ── Build factor ──
    print("[2] Building northbound accumulation factor...", flush=True)
    hold_pct = nb.pivot(index="date", columns="code", values="northbound_hold_pct")
    # Daily change in hold pct
    change = hold_pct.diff()
    # 20-day rolling accumulation score
    nb_factor = change.rolling(20).sum().rolling(5).mean()
    # Universe: stocks that have northbound holdings (not NaN hold_pct)
    has_nb = hold_pct.notna()
    # Cross-sectional rank (pct)
    nb_rank = nb_factor.rank(axis=1, pct=True, na_option="bottom")
    nb_rank = nb_rank.where(has_nb)
    print(f"  Factor shape: {nb_factor.shape}", flush=True)

    # ── IC test ──
    print("[3] IC analysis...", flush=True)
    cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")
    trade_dates = pd.to_datetime(cal["date"]).sort_values()
    daily = pd.read_parquet("data_lake/price/daily_all.parquet",
                            columns=["date","code","close"])
    daily["date"] = pd.to_datetime(daily["date"])
    close = daily.pivot(index="date", columns="code", values="close").reindex(trade_dates)

    from scipy.stats import spearmanr
    fwd20 = close.pct_change(20).shift(-20)
    fwd40 = close.pct_change(40).shift(-40)
    ics_20, ics_40 = [], []
    for dt in nb_rank.index[::30]:
        if dt not in fwd20.index: continue
        f = nb_rank.loc[dt].dropna()
        for fwd, lst in [(fwd20, ics_20), (fwd40, ics_40)]:
            r = fwd.loc[dt].dropna()
            common = f.index.intersection(r.index)
            if len(common) < 30: continue
            ic, _ = spearmanr(f[common].values, r[common].values)
            if not np.isnan(ic): lst.append(ic)
    print(f"  IC20d: mean={np.mean(ics_20):+.3f}  pos={(np.array(ics_20)>0).mean():.0%}  ICIR={np.mean(ics_20)/np.std(ics_20):.2f}", flush=True)
    print(f"  IC40d: mean={np.mean(ics_40):+.3f}  pos={(np.array(ics_40)>0).mean():.0%}  ICIR={np.mean(ics_40)/np.std(ics_40):.2f}", flush=True)

    # ── Backtest ──
    print("[4] Backtest (quarterly, top 30)...", flush=True)
    cfg = StrategyConfig(start="2017-01-01")
    base = run_small_cap_strategy(cfg)
    close_v20 = base["close"]
    v20_ret = base["returns"]

    # Build scheduled weights (quarterly)
    dates = sorted(nb_rank.dropna(how="all").index)
    rebal_dates = [d for i, d in enumerate(dates) if i % 63 == 0]
    sched_nb = {}
    for rd in rebal_dates:
        if rd not in close_v20.index: continue
        pos = close_v20.index.get_loc(rd)
        eff = close_v20.index[min(pos + 1, len(close_v20.index) - 1)]
        f = nb_rank.loc[rd].dropna()
        if len(f) < 30: continue
        sched_nb[eff] = pd.Series(1.0 / 30, index=f.nlargest(30).index)

    ones = pd.Series(1.0, index=close_v20.index, dtype="float64")
    ret_nb, _ = backtest_weights(close_v20, sched_nb, ones, cfg)

    # ── v2.0 small-cap baseline ──
    from factors.small_cap import small_cap_factor, small_cap_timing
    from strategies.small_cap import load_price_panels
    close_all, vol_all, amount_all = load_price_panels("2017-01-01")
    factor_sc = small_cap_factor(amount_all, 60)
    timing_sc, _, _ = small_cap_timing(close_all, amount_all, 16)
    sched_sc = build_rebalance_weights(factor_sc, close_all, 25, 20)
    ret_sc, _ = backtest_weights(close_all, sched_sc, timing_sc.astype(float), cfg)

    # ── Metrics ──
    def m(ret):
        r = ret.fillna(0); n = max(len(r) / 252, 1)
        a = (1 + r).cumprod().iloc[-1] ** (1 / n) - 1
        s = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
        d = float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())
        return {"annual": a, "sharpe": s, "maxdd": d}

    mnb = m(ret_nb[ret_nb.index.year >= 2019])
    msc = m(ret_sc[ret_sc.index.year >= 2019])
    print("\n[5] Results (2019+, post warmup):")
    print(f'  {"":<22} {"Ann":>8} {"Sharpe":>7} {"MaxDD":>8}')
    print(f'  {"Northbound":<22} {mnb["annual"]:>+7.1%}  {mnb["sharpe"]:>5.2f}  {mnb["maxdd"]:>+7.1%}')
    print(f'  {"v2.0 small-cap":<22} {msc["annual"]:>+7.1%}  {msc["sharpe"]:>5.2f}  {msc["maxdd"]:>+7.1%}')

    # Orthogonality
    common_nb = ret_nb.index.intersection(v20_ret.index)
    common_sc = ret_sc.index.intersection(v20_ret.index)
    print(f'\n  Corr_NB_v20:  {ret_nb.loc[common_nb].corr(v20_ret.loc[common_nb]):.3f}')
    print(f'  Corr_SC_v20:  {ret_sc.loc[common_sc].corr(v20_ret.loc[common_sc]):.3f}')
    common_nb_sc = ret_nb.index.intersection(ret_sc.index)
    print(f'  Corr_NB_SC:   {ret_nb.loc[common_nb_sc].corr(ret_sc.loc[common_nb_sc]):.3f}')

    # Yearly
    print('\n  Yearly returns:')
    print(f'  {"Year":>6} {"Northbound":>10} {"v2.0":>10}')
    for year in range(2019, 2027):
        rn = ret_nb[ret_nb.index.year == year]
        rs = ret_sc[ret_sc.index.year == year]
        if len(rn) < 50: continue
        an = (1 + rn.fillna(0)).prod() ** (252 / len(rn)) - 1
        as_ = (1 + rs.fillna(0)).prod() ** (252 / len(rs)) - 1
        print(f'  {year:>6} {an:>+9.1%} {as_:>+9.1%}')

    ret_nb.to_csv(OUT / "northbound_daily.csv")
    print(f"\nWrote: {OUT / 'northbound_daily.csv'}")


if __name__ == "__main__":
    main()
