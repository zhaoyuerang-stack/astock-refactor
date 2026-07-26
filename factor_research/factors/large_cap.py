"""Large-cap Growth + Valuation Premium Factor & Hysteresis Timing.

Calculates the factors, universe filters, and timing signals for the second mother strategy.
"""
from pathlib import Path

import numpy as np
import pandas as pd


def load_clean_panels_with_growth(data_lake_path=Path("data_lake")):
    """Load and clean financial, price, and net profit YoY growth data."""
    fund = pd.read_parquet(data_lake_path / "fundamental_batch.parquet")
    fund["report_date"] = pd.to_datetime(fund["report_date"])
    fund["avail_date"] = pd.to_datetime(fund["avail_date"])
    cal = pd.read_parquet(data_lake_path / "meta/trade_calendar.parquet")
    trade_dates = pd.to_datetime(cal["date"]).sort_values()

    # Clean ROE: winsorize to [-30, +50]
    roe = fund["roe"].copy()
    roe = roe.clip(lower=-30, upper=50)

    # Clean EPS_TTM: keep only positive
    eps = fund["eps_ttm"].copy()
    eps[eps <= 0] = np.nan

    # Clean BPS: keep only positive, clip
    bps = fund["bps"].copy()
    bps = bps.clip(lower=0.01, upper=500)

    # Clean net_profit_yoy: winsorize to [-100, 300]
    npy = fund["net_profit_yoy"].copy()
    npy = npy.clip(lower=-100, upper=300)

    # Raw close (loaded early for reindexing)
    raw_all = pd.read_parquet(data_lake_path / "price/daily_raw_all.parquet")
    raw_all["date"] = pd.to_datetime(raw_all["date"])
    raw_close = raw_all.pivot(index="date", columns="code", values="raw_close")
    raw_close = raw_close.reindex(trade_dates)

    # Data age stale check (Age <= 365 days)
    rpt_sub = fund[["code", "avail_date", "report_date"]].dropna()
    rpt_sub = rpt_sub.sort_values(["code", "avail_date"]).drop_duplicates(["code", "avail_date"], keep="last")
    report_tracker = rpt_sub.pivot(index="avail_date", columns="code", values="report_date")
    report_tracker = report_tracker.reindex(trade_dates).ffill()
    price_codes = raw_close.columns.intersection(report_tracker.columns)
    report_tracker = report_tracker[price_codes]
    td_epoch = (trade_dates.astype('int64') // 1_000_000_000 // 86400).values
    rt_epoch = (report_tracker.values.astype('int64') // 1_000_000_000 // 86400)
    data_age_arr = td_epoch[:, None] - rt_epoch
    data_age = pd.DataFrame(data_age_arr, index=trade_dates, columns=report_tracker.columns).clip(lower=0)
    stale_mask = data_age > 365

    # Pivot to avail_date × code (防未来函数)
    fields = {"eps_ttm": eps, "bps": bps, "roe": roe, "net_profit_yoy": npy}
    panels = {}
    for name, series in fields.items():
        sub = pd.DataFrame({
            "code": fund["code"], "avail_date": fund["avail_date"], name: series
        }).dropna(subset=[name])
        sub = sub.sort_values(["code", "avail_date"]).drop_duplicates(["code", "avail_date"], keep="last")
        pivot = sub.pivot(index="avail_date", columns="code", values=name)
        pivot = pivot.reindex(index=trade_dates, columns=raw_close.columns).ffill()
        panels[name] = pivot

    # Amount & Close
    daily_all = pd.read_parquet(data_lake_path / "price/daily_all.parquet", columns=["date", "code", "amount", "close"])
    daily_all["date"] = pd.to_datetime(daily_all["date"])
    amount = daily_all.pivot(index="date", columns="code", values="amount").reindex(trade_dates)
    close_adj = daily_all.pivot(index="date", columns="code", values="close").reindex(trade_dates)

    # Apply stale mask
    for name in ["eps_ttm", "bps", "roe", "net_profit_yoy"]:
        panels[name] = panels[name].where(~stale_mask)

    # PE & PB
    eps_aligned = panels["eps_ttm"].reindex(index=raw_close.index, columns=raw_close.columns)
    raw_aligned = raw_close.reindex(index=eps_aligned.index, columns=eps_aligned.columns)
    pe = raw_aligned / eps_aligned
    pe = pe.clip(lower=3, upper=200)

    bps_aligned = panels["bps"].reindex(index=raw_close.index, columns=raw_close.columns)
    pb = raw_aligned / bps_aligned
    pb = pb.clip(lower=0.1, upper=30)

    # Load total_mv for true market capitalization
    from lake.load_lake import load_daily_basic_panel
    basic_panels = load_daily_basic_panel(trade_dates, codes=raw_close.columns.tolist(), fields=["total_mv"])
    total_mv = basic_panels["total_mv"].reindex(index=raw_close.index, columns=raw_close.columns).ffill().fillna(0.0)

    return {
        "pe": pe, "pb": pb,
        "roe": panels["roe"], "npy": panels["net_profit_yoy"],
        "raw_close": raw_close, "amount": amount, "close": close_adj,
        "total_mv": total_mv,
    }

def build_universe(panels, top_n=200):
    """Select the top N largest market cap stocks."""
    if "total_mv" in panels:
        return panels["total_mv"].rank(axis=1, ascending=False, pct=False) <= top_n
    # Fallback to ADTV * price if total_mv not available
    cap = panels["amount"].rolling(20).mean() * panels["raw_close"]
    return cap.rank(axis=1, ascending=False, pct=False) <= top_n

def build_large_cap_premium_factor(panels, universe_size=200, w_cpv_max=0.0):
    """Computes the Growth + High Valuation Premium factor within the Top N universe,
    with an optional adaptive CPV penalty.
    """
    univ = build_universe(panels, universe_size)
    pe = panels["pe"]
    pb = panels["pb"]
    roe = panels["roe"]
    npy = panels["npy"]
    close = panels["close"]
    amount = panels["amount"]

    # 1. Valuation rank: higher PE/PB is more expensive
    pe_r = pe.rank(axis=1, pct=True, na_option="bottom")
    pb_r = pb.rank(axis=1, pct=True, na_option="bottom")
    valuation = (pe_r + pb_r) / 2.0

    # 2. Growth rank: higher ROE & net_profit_yoy is stronger
    roe_r = roe.rank(axis=1, pct=True, na_option="bottom")
    npy_r = npy.rank(axis=1, pct=True, na_option="bottom")
    growth = (roe_r + npy_r) / 2.0

    # 3. Optional Adaptive CPV Penalty
    if w_cpv_max > 0:
        # Calculate daily rolling Pearson correlation between close price and trade amount
        mean1 = close.rolling(20).mean()
        mean2 = amount.rolling(20).mean()
        mean_prod = (close * amount).rolling(20).mean()
        cov = mean_prod - mean1 * mean2
        std1 = close.rolling(20).std()
        std2 = amount.rolling(20).std()
        cpv = cov / (std1 * std2 + 1e-8)
        
        # Rank-product CPV: CPV_rank * M_liq_rank
        cpv_raw_r = cpv.rank(axis=1, pct=True, na_option="bottom")
        m_liq = 1.0 / (mean2 + 1.0)
        m_liq_r = m_liq.rank(axis=1, pct=True, na_option="bottom")
        cpv_r = (cpv_raw_r * m_liq_r).rank(axis=1, pct=True, na_option="bottom")

        # Compute Benchmark return for regime detection
        daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        bench_returns = pd.Series(0.0, index=daily_ret.index)
        univ_shifted = univ.shift(1)
        for dt in daily_ret.index:
            active = univ_shifted.loc[dt] if dt in univ_shifted.index else pd.Series(False, index=univ.columns)
            active = active.fillna(False).astype(bool)
            active_codes = active[active].index
            if len(active_codes) > 0:
                bench_returns.loc[dt] = daily_ret.loc[dt, active_codes].mean()

        bench_nav = (1 + bench_returns).cumprod()
        bench_ma = bench_nav.rolling(120).mean()
        is_bull = (bench_nav > bench_ma).astype(float).shift(1).fillna(0.0)

        # Broadcast is_bull to DataFrame matching close columns shape
        is_bull_df = pd.DataFrame({col: is_bull for col in close.columns}, index=close.index)
        w_cpv = is_bull_df * w_cpv_max
        comp_premium = ((valuation + growth - w_cpv * cpv_r) / (2.0 + w_cpv)).where(univ)
    else:
        # Composite factor: Growth + High Valuation Premium
        comp_premium = ((valuation + growth) / 2.0).where(univ)

    return comp_premium, univ

def large_cap_timing_hysteresis(nav, window=120, buffer=0.01):
    """Computes the hysteresis timing signal on strategy NAV.
    
    If current signal is 1: only switch to 0 if nav < ma * (1 - buffer)
    If current signal is 0: only switch to 1 if nav > ma * (1 + buffer)
    Returns a Series of 1.0 (long-short) and 0.0 (flat), lagged by 1 day.
    """
    ma = nav.rolling(window).mean()
    signal = pd.Series(0.0, index=nav.index)
    current_state = 0.0
    
    for i in range(len(nav)):
        val = nav.iloc[i]
        ma_val = ma.iloc[i]
        
        if pd.isna(ma_val):
            signal.iloc[i] = 0.0
            continue
            
        if current_state == 1.0:
            if val < ma_val * (1.0 - buffer):
                current_state = 0.0
        else:
            if val > ma_val * (1.0 + buffer):
                current_state = 1.0
        signal.iloc[i] = current_state
        
    return signal.shift(1, fill_value=0.0)
