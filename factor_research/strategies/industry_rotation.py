"""M3 Mother Strategy: Industry Neglect & Huaxi Volume-Price Rotation (SW L2 sector rotation).

Supports ETF rotation (v1.0), slow stock selection (v1.1), CPV-penalized stock selection (v1.2),
Huaxi 11-factor ETF rotation (v1.3), and Huaxi 11-factor stock selection rotation (v1.4).
"""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.large_cap import load_clean_panels_with_growth
from factors.small_cap import small_cap_timing


@dataclass(frozen=True)
class StrategyConfig:
    family: str = "industry-neglect-rotation"
    version: str = "v1.2"  # "v1.0" (ETF), "v1.1" (Slow Stock), "v1.2" (CPV Stock), "v1.3" (Huaxi ETF), "v1.4" (Huaxi Stock)
    start: str = "2012-01-01"
    rebalance_days: int = 20
    top_k_industries: int = 10
    top_n_stocks: int = 2
    w_cpv: float = 0.5  # CPV penalty weight (0.0 for v1.0/v1.1, 0.5 for v1.2/v1.4)
    cost_mode: str = "stock"  # "stock" (0.47% friction), "etf" (0.05% friction)
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# B008 修复:frozen dataclass 不可变,模块级单例做缺省,与旧内联缺省语义等价。
_DEFAULT_CONFIG = StrategyConfig()


def vectorized_rolling_corr(df1: pd.DataFrame, df2: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    mean1 = df1.rolling(window).mean()
    mean2 = df2.rolling(window).mean()
    mean_prod = (df1 * df2).rolling(window).mean()
    cov = mean_prod - mean1 * mean2
    std1 = df1.rolling(window).std()
    std2 = df2.rolling(window).std()
    return cov / (std1 * std2 + 1e-8)

def load_industry_groups() -> dict[str, str]:
    """Load latest industry mapping from fundamental parquet."""
    fund = pd.read_parquet("data_lake/fundamental_batch.parquet", columns=["code", "avail_date", "industry"])
    mapping = fund.dropna(subset=["industry"]).sort_values("avail_date").drop_duplicates("code", keep="last")
    stock_to_ind = dict(zip(mapping["code"], mapping["industry"], strict=True))
    return stock_to_ind

def load_all_daily_price_fields(data_lake_path: Path = Path("data_lake")) -> dict[str, pd.DataFrame]:
    """Load all daily price fields from daily_all.parquet."""
    cal = pd.read_parquet(data_lake_path / "meta/trade_calendar.parquet")
    trade_dates = pd.to_datetime(cal["date"]).sort_values()
    
    daily_all = pd.read_parquet(data_lake_path / "price/daily_all.parquet")
    daily_all["date"] = pd.to_datetime(daily_all["date"])
    
    # Pivot each field
    close = daily_all.pivot(index="date", columns="code", values="close").reindex(trade_dates)
    open_ = daily_all.pivot(index="date", columns="code", values="open").reindex(trade_dates)
    high = daily_all.pivot(index="date", columns="code", values="high").reindex(trade_dates)
    low = daily_all.pivot(index="date", columns="code", values="low").reindex(trade_dates)
    volume = daily_all.pivot(index="date", columns="code", values="volume").reindex(trade_dates)
    amount = daily_all.pivot(index="date", columns="code", values="amount").reindex(trade_dates)
    
    # Load raw_close for execution
    raw_all = pd.read_parquet(data_lake_path / "price/daily_raw_all.parquet")
    raw_all["date"] = pd.to_datetime(raw_all["date"])
    raw_close = raw_all.pivot(index="date", columns="code", values="raw_close").reindex(trade_dates)
    
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "raw_close": raw_close
    }

def load_bond_returns(code: str = "511010", data_lake_path: Path = Path("data_lake")) -> pd.Series:
    """Loads Gov Bond ETF daily returns."""
    df = pd.read_parquet(data_lake_path / f"cross_asset/etf/{code}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    return df["close"].pct_change(fill_method=None).dropna()

def aggregate_industry_all_fields(prices: dict[str, pd.DataFrame], stock_to_ind: dict[str, str]) -> dict[str, Any]:
    """Aggregates all price fields to Shenwan L2 industry level."""
    close = prices["close"]
    open_ = prices["open"]
    high = prices["high"]
    low = prices["low"]
    volume = prices["volume"]
    amount = prices["amount"]
    
    common_codes = close.columns.intersection(amount.columns)
    ind_groups: dict[str, list[str]] = {}
    for code in common_codes:
        ind = stock_to_ind.get(code)
        if ind:
            ind_groups.setdefault(ind, []).append(code)
            
    trade_dates = close.index
    industries = list(ind_groups.keys())
    
    ind_close = pd.DataFrame(1.0, index=trade_dates, columns=industries)
    ind_open = pd.DataFrame(1.0, index=trade_dates, columns=industries)
    ind_high = pd.DataFrame(1.0, index=trade_dates, columns=industries)
    ind_low = pd.DataFrame(1.0, index=trade_dates, columns=industries)
    ind_volume = pd.DataFrame(0.0, index=trade_dates, columns=industries)
    ind_amount = pd.DataFrame(0.0, index=trade_dates, columns=industries)
    
    # Calculate stock returns and ratio indicators
    stock_returns = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    co_ratio = close / open_.replace(0.0, np.nan)
    hc_ratio = high / close.replace(0.0, np.nan)
    lc_ratio = low / close.replace(0.0, np.nan)
    
    for ind, codes in ind_groups.items():
        ind_ret = stock_returns[codes].mean(axis=1)
        ind_close[ind] = (1.0 + ind_ret).cumprod()
        
        mean_co = co_ratio[codes].mean(axis=1).fillna(1.0)
        mean_hc = hc_ratio[codes].mean(axis=1).fillna(1.0)
        mean_lc = lc_ratio[codes].mean(axis=1).fillna(1.0)
        
        ind_open[ind] = ind_close[ind] / mean_co
        ind_high[ind] = ind_close[ind] * mean_hc
        ind_low[ind] = ind_close[ind] * mean_lc
        
        ind_volume[ind] = volume[codes].sum(axis=1)
        ind_amount[ind] = amount[codes].sum(axis=1)
        
    return {
        "open": ind_open,
        "high": ind_high,
        "low": ind_low,
        "close": ind_close,
        "volume": ind_volume,
        "amount": ind_amount,
        "ind_groups": ind_groups
    }

def compute_huaxi_industry_factors(ind_prices: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Computes the 11 Huaxi volume-price factors at the industry level."""
    ind_open = ind_prices["open"]
    ind_high = ind_prices["high"]
    ind_low = ind_prices["low"]
    ind_close = ind_prices["close"]
    ind_volume = ind_prices["volume"]
    ind_amount = ind_prices["amount"]
    
    def ewma(df: pd.DataFrame, span: int) -> pd.DataFrame:
        return df.ewm(span=span, adjust=False).mean()
        
    # 1. SecondMomentum: w=20, w1=60, w2=20
    mean_close = ind_close.rolling(60).mean()
    ret_mean = (ind_close - mean_close) / (mean_close + 1e-8)
    diff_ret = ret_mean - ret_mean.shift(20)
    f_second_mom = ewma(diff_ret, 20)
    
    # 2. MomentumTermSpread: w1=60, w2=20
    mom_long = ind_close.pct_change(60, fill_method=None)
    mom_short = ind_close.pct_change(20, fill_method=None)
    f_mom_spread = mom_long - mom_short
    
    # 3. AmountVolatility: w=20
    f_amt_vol = -ind_amount.rolling(20).std()
    
    # 4. VolumeVolatility: w=20
    f_vol_vol = -ind_volume.rolling(20).std()
    
    # 5. TurnoverChange: w1=120, w2=20
    f_turnover_chg = ind_volume.rolling(120).mean() / (ind_volume.rolling(20).mean() + 1e-8)
    
    # 6. NetPosition: w=20
    ratio_pos = (ind_close - ind_low) / (ind_high - ind_close + 1e-8)
    f_net_pos = -ratio_pos.rolling(20).sum()
    
    # 7. PositionChange: w1=60, w2=20
    val_pos_chg = ind_volume * ((ind_close - ind_low) - (ind_high - ind_close)) / (ind_high - ind_low + 1e-8)
    f_pos_chg = ewma(val_pos_chg, 60) - ewma(val_pos_chg, 20)
    
    # 8. VolumePriceRankCorr: w=20 (using cross-sectional ranks)
    close_r = ind_close.rank(axis=1, pct=True)
    vol_r = ind_volume.rank(axis=1, pct=True)
    f_vp_rank_corr = -vectorized_rolling_corr(close_r, vol_r, window=20)
    
    # 9. VolumePriceCorr: w=20
    f_vp_corr = -vectorized_rolling_corr(ind_close, ind_volume, window=20)
    
    # 10. FirstOrderDivergence: w=20
    vol_growth = ind_volume / ind_volume.shift(1) - 1.0
    intraday_ret = ind_close / ind_open - 1.0
    vol_growth_r = vol_growth.rank(axis=1, pct=True)
    intraday_ret_r = intraday_ret.rank(axis=1, pct=True)
    f_fod_corr = -vectorized_rolling_corr(vol_growth_r, intraday_ret_r, window=20)
    
    # 11. VolumeAmplitudeCoMovement: w=20
    amplitude = ind_high / ind_low - 1.0
    amplitude_r = amplitude.rank(axis=1, pct=True)
    f_vac_corr = vectorized_rolling_corr(vol_growth_r, amplitude_r, window=20)
    
    return {
        "second_mom": f_second_mom,
        "mom_spread": f_mom_spread,
        "amt_vol": f_amt_vol,
        "vol_vol": f_vol_vol,
        "turnover_chg": f_turnover_chg,
        "net_pos": f_net_pos,
        "pos_chg": f_pos_chg,
        "vp_rank_corr": f_vp_rank_corr,
        "vp_corr": f_vp_corr,
        "fod_corr": f_fod_corr,
        "vac_corr": f_vac_corr
    }

def aggregate_industry_data(close: pd.DataFrame, amount: pd.DataFrame, stock_to_ind: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[str]]]:
    """Aggregates stock-level returns and trade amounts to Shenwan L2 industry level (v1.0-v1.2 fallback)."""
    stock_returns = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    common_codes = stock_returns.columns.intersection(amount.columns)
    stock_returns = stock_returns[common_codes]
    amount_aligned = amount[common_codes]
    
    ind_groups: dict[str, list[str]] = {}
    for code in common_codes:
        ind = stock_to_ind.get(code)
        if ind:
            ind_groups.setdefault(ind, []).append(code)

    trade_dates = stock_returns.index
    ind_returns = pd.DataFrame(0.0, index=trade_dates, columns=list(ind_groups.keys()))
    ind_amounts = pd.DataFrame(0.0, index=trade_dates, columns=list(ind_groups.keys()))
    
    for ind, codes in ind_groups.items():
        ind_returns[ind] = stock_returns[codes].mean(axis=1)
        ind_amounts[ind] = amount_aligned[codes].sum(axis=1)
        
    return ind_returns, ind_amounts, ind_groups

def run_industry_rotation_strategy(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Runs the complete SW L2 Industry Rotation strategy (supports v1.0 - v1.4)."""
    # 1. Load data
    if config.version in ["v1.3", "v1.4"]:
        prices = load_all_daily_price_fields()
        close = prices["close"]
        amount = prices["amount"]
        raw_close = prices["raw_close"]
    else:
        panels = load_clean_panels_with_growth()
        close = panels["close"]
        amount = panels["amount"]
        raw_close = panels["raw_close"]
    
    # Clean corrupted dates
    clean_dates = close.index[close.index < "2026-06-10"]
    if config.version in ["v1.3", "v1.4"]:
        for k in ["open", "high", "low", "close", "volume", "amount", "raw_close"]:
            prices[k] = prices[k].reindex(clean_dates)
        close = prices["close"]
        amount = prices["amount"]
        raw_close = prices["raw_close"]
    else:
        close = close.reindex(clean_dates)
        amount = amount.reindex(clean_dates)
        raw_close = raw_close.reindex(clean_dates)
        
    price_panel = PricePanel(close=close, volume=amount*0, amount=amount, raw_close=raw_close)
    
    # Load quality fields for stock selection
    panels_growth = load_clean_panels_with_growth()
    roe = panels_growth["roe"].reindex(clean_dates)
    npy = panels_growth["npy"].reindex(clean_dates)
    
    # Compute CPV Factor
    cpv = vectorized_rolling_corr(close, amount, window=20)
    cpv_raw_r = cpv.rank(axis=1, pct=True, na_option="bottom")
    m_liq = 1.0 / (amount.rolling(20).mean() + 1.0)
    m_liq_r = m_liq.rank(axis=1, pct=True, na_option="bottom")
    cpv_r = (cpv_raw_r * m_liq_r).rank(axis=1, pct=True, na_option="bottom")
    
    # 2. Industry Mapping & Aggregation
    stock_to_ind = load_industry_groups()
    
    if config.version in ["v1.3", "v1.4"]:
        ind_prices = aggregate_industry_all_fields(prices, stock_to_ind)
        ind_groups = ind_prices["ind_groups"]
        factors = compute_huaxi_industry_factors(ind_prices)
    else:
        ind_returns, ind_amounts, ind_groups = aggregate_industry_data(close, amount, stock_to_ind)
        # 3. Industry Factors for Neglect version
        ind_mom = np.exp(np.log1p(ind_returns).rolling(20).sum()) - 1.0
        ind_vol = ind_returns.rolling(20).std()
        ind_amt_growth = ind_amounts.rolling(20).mean() / (ind_amounts.rolling(120).mean() + 1e-5)
    
    # 4. Generate Scheduled Weights
    start_idx = 120
    rebal_dates = clean_dates[start_idx::config.rebalance_days]
    scheduled_weights = {}
    
    for i in range(len(rebal_dates)):
        dt_current = rebal_dates[i]
        pos = close.index.get_loc(dt_current)
        if pos + 1 >= len(close.index):
            continue
        effective_date = close.index[pos + 1]
        
        selected_inds = []
        if config.version in ["v1.3", "v1.4"]:
            valid_inds = None
            for _, factor_df in factors.items():
                val_df = factor_df.loc[dt_current].dropna()
                if valid_inds is None:
                    valid_inds = val_df.index
                else:
                    valid_inds = valid_inds.intersection(val_df.index)
            if valid_inds is not None and len(valid_inds) > 0:
                factor_ranks = []
                for _, factor_df in factors.items():
                    factor_val = factor_df.loc[dt_current, valid_inds]
                    rank_val = factor_val.rank(pct=True)
                    factor_ranks.append(rank_val)
                score = sum(factor_ranks)
                selected_inds = score.nlargest(config.top_k_industries).index.tolist()
        else:
            mom = ind_mom.loc[dt_current]
            vol = ind_vol.loc[dt_current]
            amt_grow = ind_amt_growth.loc[dt_current]
            
            valid_inds = mom.dropna().index.intersection(vol.dropna().index).intersection(amt_grow.dropna().index)
            if len(valid_inds) > 0:
                mom_rank = mom.loc[valid_inds].rank(pct=True)
                vol_rank = vol.loc[valid_inds].rank(pct=True)
                amt_rank = amt_grow.loc[valid_inds].rank(pct=True)
                # Contrarian Score = Reversal + Low Vol + Volume Neglect
                score = -mom_rank - vol_rank - amt_rank
                selected_inds = score.nlargest(config.top_k_industries).index.tolist()
        
        if len(selected_inds) == 0:
            continue
            
        selected_stocks = []
        for ind in selected_inds:
            codes = ind_groups.get(ind, [])
            active_codes = [c for c in codes if c in close.columns and not pd.isna(close.loc[dt_current, c])]
            if len(active_codes) == 0:
                continue
            
            if config.version in ["v1.0", "v1.3"]:
                # ETF: Hold all active stocks in the industry equally
                selected_stocks.extend(active_codes)
            else:
                # Stock version: select Top N stocks by quality + CPV
                r_val = roe.loc[dt_current, active_codes].fillna(-100.0)
                n_val = npy.loc[dt_current, active_codes].fillna(-100.0)
                stock_score = r_val.rank(pct=True) + n_val.rank(pct=True)
                
                if config.w_cpv > 0:
                    c_val = cpv_r.loc[dt_current, active_codes].fillna(0.5)
                    stock_score = stock_score - config.w_cpv * c_val.rank(pct=True)
                    
                top_stocks = stock_score.nlargest(config.top_n_stocks).index.tolist()
                selected_stocks.extend(top_stocks)
                
        if len(selected_stocks) > 0:
            weight_val = 1.0 / len(selected_stocks)
            scheduled_weights[effective_date] = pd.Series(weight_val, index=selected_stocks)
            
    # 5. Cost Model
    if config.cost_mode == "etf":
        cost_model = CostModel(buy_cost=0.0005, sell_cost=0.0005, financing_rate=0.0)
    else:
        cost_model = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.0)
        
    # 6. Run Engine
    engine_config = BacktestConfig(
        start=config.start,
        cost=cost_model,
        leverage=1.0
    )
    engine = BacktestEngine(prices=price_panel, config=engine_config)
    
    timing_signal = None
    if config.version in ["v1.3", "v1.4"]:
        timing_raw, _, timing_dist = small_cap_timing(close, amount, ma_window=16)
        timing_signal = timing_raw
        
    signal = Signal(weights=scheduled_weights, timing=timing_signal, family=config.family, version=config.version)
    result = engine.run(signal)
    
    r_final = result.returns
    if config.version in ["v1.3", "v1.4"] and timing_signal is not None:
        # Load bonds and apply rotation
        bond_ret = load_bond_returns("511010")
        common = r_final.index.intersection(bond_ret.index).intersection(timing_signal.index)
        r_stock_aligned = r_final.reindex(common).fillna(0.0)
        bond_ret_aligned = bond_ret.reindex(common).fillna(0.0)
        bull_mask = timing_signal.reindex(common).fillna(False)
        r_final = pd.Series(np.where(bull_mask, r_stock_aligned, bond_ret_aligned), index=common)
        
        # In-place update of result object
        result.returns = r_final
        result.__post_init__()
        
    return {
        "returns": r_final,
        "close": close,
        "scheduled_weights": scheduled_weights,
        "engine_result": result,
        "timing": timing_signal
    }

def latest_signal(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Backward-compatible wrapper for :func:`latest_decision`."""
    return latest_decision(config)


def latest_decision(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Returns the latest signal and holdings for live trading."""
    result = run_industry_rotation_strategy(config)
    close = result["close"]
    
    last = close.index[-1]
    weight_dates = sorted(list(result["scheduled_weights"].keys()))
    latest_holdings = []
    if len(weight_dates) > 0:
        latest_rebal_dt = next((d for d in reversed(weight_dates) if d <= last), None)
        if latest_rebal_dt:
            latest_holdings = result["scheduled_weights"][latest_rebal_dt].index.tolist()
            
    in_market = True
    if config.version in ["v1.3", "v1.4"]:
        timing_sig = result.get("timing")
        if timing_sig is not None and last in timing_sig.index:
            in_market = bool(timing_sig.loc[last])
            
    return {
        "date": last,
        "in_market": in_market,
        "holdings": latest_holdings if in_market else [],
        "result": result,
    }
