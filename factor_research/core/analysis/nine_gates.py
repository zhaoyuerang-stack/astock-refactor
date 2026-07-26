"""Nine-Gate Research-to-Production Factor Evaluation Framework.

Implement the 9 gates of institutional-grade factor validation:
Gate 0: Data Audit (Missing/Inf/Outlier audit & Look-ahead perturbation test)
Gate 1: Factor Hypothesis (Economic justification & falsifiability check)
Gate 2: Single Factor Verification (Rank IC, NW-ICIR, Decay, Monotonicity)
Gate 3: Neutralization Verification (Cross-sectional Size & Industry neutralization)
Gate 4: Multiple Testing Penalty (Deflated Sharpe Ratio (DSR) & Probabilistic Sharpe Ratio (PSR))
Gate 5: Portfolio Backtesting (Unified backtest engine under standard cost model)
Gate 6: Cost & Capacity Modeling (Slippage sensitivity & square-root/ADV participation capacity curves)
Gate 7: Out-of-Sample & Stress Testing (Walk-Forward, Regime split, Delayed execution)
Gate 8: Live Monitoring (Live IC bounds, style drift, decay monitoring parameters)
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from core.analysis.walk_forward import deflated_sharpe, walk_forward_windows, wf_metrics
from core.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    CostModel,
    PricePanel,
    Signal,
)

logger = logging.getLogger("nine_gates")  # Task 17:控制路径异常须可见,不得静默 except:pass


# ---------------------------------------------------------------------------
# Helpers & Math Utilities
# ---------------------------------------------------------------------------

def calculate_newey_west_icir(daily_ic: pd.Series, lag: int = 20) -> float:
    """Compute Newey-West corrected ICIR using Bartlett kernel."""
    ic = daily_ic.dropna().values
    n = len(ic)
    if n < 10:
        return 0.0
    mean = ic.mean()
    var = ic.var()
    if var <= 1e-12:
        return 0.0
    
    lr_var = var
    for l in range(1, lag + 1):
        w = 1.0 - l / (lag + 1)
        # correlation at lag l
        ac = np.corrcoef(ic[:-l], ic[l:])[0, 1]
        if not np.isnan(ac):
            lr_var += 2 * w * ac * var
            
    return abs(mean) / np.sqrt(max(lr_var, 1e-12))


def probabilistic_sharpe_ratio(
    observed_sr: float,  # Annualized Sharpe
    benchmark_sr: float = 0.0,  # Annualized Benchmark Sharpe (usually 0)
    n_periods: int = 252,  # Number of return observations
    skewness: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Compute Probabilistic Sharpe Ratio (PSR).
    
    Formula from López de Prado (2018), Chapter 10.
    """
    sr_daily = observed_sr / np.sqrt(252)
    sr_star_daily = benchmark_sr / np.sqrt(252)
    
    # Standard deviation of daily Sharpe estimate
    # var(sr_daily) = (1 - skew * sr_daily + (kurt - 1) / 4 * sr_daily^2) / (T - 1)
    std_sr = np.sqrt(
        (1.0 - skewness * sr_daily + (kurt - 1.0) / 4.0 * (sr_daily ** 2))
        / (n_periods - 1.0)
    )
    
    if std_sr <= 1e-12:
        return 0.0
        
    z = (sr_daily - sr_star_daily) / std_sr
    return float(stats.norm.cdf(z))


def _shift_decision_weights(
    weights: pd.DataFrame,
    trade_index: pd.DatetimeIndex,
    delay: int,
) -> pd.DataFrame:
    """把稀疏决策权重的每一行沿交易日历向后平移 delay 个交易日。

    直接 ``weights.shift(delay)`` 是按**行**移——决策面板每 ~20 天才一行,
    等于延迟了一个调仓周期而不是宣称的 1-2 天(Gate7 延迟执行测试因此失真,
    2026-07-11 review)。本函数按 trade_index 精确移 delay 个交易日;
    越界行丢弃;同一目标日冲突保留最后一行。
    """
    if delay <= 0 or weights.empty:
        return weights
    rows: dict = {}
    for d, row in weights.iterrows():
        pos = trade_index.searchsorted(pd.Timestamp(d)) + delay
        if pos < len(trade_index):
            rows[trade_index[pos]] = row
    if not rows:
        return pd.DataFrame(columns=weights.columns, index=pd.DatetimeIndex([]))
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index = pd.DatetimeIndex(out.index)
    return out.sort_index()


# ---------------------------------------------------------------------------
# 9-Gate Classes
# ---------------------------------------------------------------------------

@dataclass
class GateReport:
    gate_id: int | str
    name: str
    passed: bool
    verdict: str  # PASS, WARN, FAIL
    metrics: dict[str, Any]
    details: str
    reasons: list[str] = field(default_factory=list)


class NineGatesEvaluator:
    """Suite to evaluate factors/strategies through the 9-Gate Research-to-Production pipeline."""

    def __init__(
        self,
        prices: PricePanel,
        factor_df: pd.DataFrame,
        factor_builder: Callable[[PricePanel], pd.DataFrame] | None = None,
        thesis: dict[str, str] | Any | None = None,
        n_trials: int = 1,
        forward_days: int = 20,
    ):
        self.prices = prices
        self.factor_df = factor_df
        self.factor_builder = factor_builder
        self.thesis = thesis
        self.n_trials = n_trials
        self.forward_days = forward_days
        
        # Precompute forward returns for validation
        self.forward_returns = prices.close.pct_change(forward_days).shift(-forward_days)
        self.daily_returns = prices.close.pct_change(fill_method=None).fillna(0.0)

    def _get_weights(self, signal: Signal) -> pd.DataFrame:
        weights = signal._resolve_weights(self.prices)
        if isinstance(weights, dict):
            from core.engine import _dict_weights_to_df
            weights = _dict_weights_to_df(weights, self.prices.close.index)
        return weights

    # ───────────────────────────────────────────────────────────────────────
    # Gate 0: Data Audit (数据审计)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate0_data_audit(self) -> GateReport:
        """Data Audit: check for look-ahead leaks, NaNs, infs, and outliers."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        # NaN / missing checks
        total_vals = self.factor_df.size
        nan_count = self.factor_df.isna().sum().sum()
        nan_pct = nan_count / total_vals if total_vals > 0 else 1.0
        metrics["nan_pct"] = float(nan_pct)
        
        if nan_pct > 0.90:
            reasons.append(f"Extremely high missing data: {nan_pct:.1%} of factor panel is NaN")
            verdict = "FAIL"
        elif nan_pct > 0.50:
            reasons.append(f"High missing data: {nan_pct:.1%} of factor panel is NaN")
            verdict = "WARN"

        # Infinite value checks
        inf_count = np.isinf(self.factor_df.values).sum()
        metrics["inf_count"] = int(inf_count)
        if inf_count > 0:
            reasons.append(f"Found {inf_count} infinite values in factor panel")
            verdict = "FAIL"

        # Extreme outliers check (values outside 10 standard deviations from row mean)
        row_mean = self.factor_df.mean(axis=1)
        row_std = self.factor_df.std(axis=1)
        # Z-score matrix
        z_scores = self.factor_df.sub(row_mean, axis=0).div(row_std + 1e-8, axis=0).abs()
        outliers_10std = (z_scores > 10.0).sum().sum()
        metrics["outliers_10std"] = int(outliers_10std)
        if outliers_10std > 0:
            reasons.append(f"Found {outliers_10std} extreme outliers (>10 std deviations)")
            if verdict != "FAIL":
                verdict = "WARN"

        # Look-ahead Bias Leakage perturbation test
        if self.factor_builder is not None:
            try:
                # 1. Choose a date near the middle of index
                t_idx = len(self.prices.close) // 2
                pert_date = self.prices.close.index[t_idx]
                
                # 2. Perturb price data at t_idx
                perturbed_close = self.prices.close.copy()
                # Increase close price on pert_date by 50%
                perturbed_close.loc[pert_date] *= 1.50
                
                pert_prices = PricePanel(
                    close=perturbed_close,
                    volume=self.prices.volume,
                    amount=self.prices.amount,
                    raw_close=self.prices.raw_close
                )
                
                # 3. Generate factor with perturbed prices
                pert_factor = self.factor_builder(pert_prices)
                
                # 4. Check if factor at dates strictly before pert_date has changed
                before_idx = self.prices.close.index[self.prices.close.index < pert_date]
                diff = (pert_factor.loc[before_idx] - self.factor_df.loc[before_idx]).abs().max().max()
                metrics["lookahead_pert_diff"] = float(diff) if not pd.isna(diff) else 0.0
                
                if diff > 1e-7:
                    reasons.append(f"Future leak detected: perturbing price on {pert_date.date()} changed historical factor values (diff={diff:.6f})")
                    verdict = "FAIL"
            except Exception as e:
                reasons.append(f"Look-ahead perturbation test failed to run: {str(e)}")
                verdict = "WARN"
        else:
            metrics["lookahead_pert_diff"] = 0.0
            reasons.append("Factor builder function not provided; look-ahead numerical perturbation test skipped")
            verdict = "WARN"

        passed = (verdict != "FAIL")
        details = (
            f"Gate 0: Data audit complete. NaN={nan_pct:.1%}, Infs={inf_count}, Outliers={outliers_10std}. "
            f"Perturbation diff={metrics.get('lookahead_pert_diff', 0.0):.6f}"
        )
        return GateReport(0, "Data Audit", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 1: Factor Hypothesis (经济假设)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate1_hypothesis(self) -> GateReport:
        """Hypothesis verification: check if there's a valid economic explanation."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        if self.thesis is None:
            reasons.append("Economic thesis is completely missing. Quantitative factor must have an explanation")
            verdict = "FAIL"
            metrics["has_thesis"] = False
            metrics["thesis_len"] = 0
        else:
            metrics["has_thesis"] = True
            mechanism = ""
            if isinstance(self.thesis, dict):
                mechanism = self.thesis.get("mechanism", "")
                citation = self.thesis.get("citation", "")
            else:
                mechanism = getattr(self.thesis, "mechanism", "")
                citation = getattr(self.thesis, "citation", "")
                
            metrics["thesis_len"] = len(mechanism)
            metrics["citation_len"] = len(str(citation))
            
            if len(mechanism) < 15:
                reasons.append("Economic mechanism explanation is too short/generic (needs to be >= 15 chars)")
                verdict = "FAIL"
            
            # Keywords matching
            keywords = ["溢价", "偏离", "拥挤", "流动性", "风险补偿", "行为偏差", "套利", "反转", "动量", "价值", "成长", "质量"]
            has_keywords = any(kw in mechanism for kw in keywords)
            metrics["has_keywords"] = has_keywords
            if not has_keywords:
                reasons.append("Mechanism lacks standard financial/behavioral economic terms (e.g. risk premium, behavioral bias, etc.)")
                if verdict != "FAIL":
                    verdict = "WARN"

        passed = (verdict != "FAIL")
        details = f"Gate 1: Thesis verification complete. Mechanism length={metrics.get('thesis_len', 0)}."
        return GateReport(1, "Economic Hypothesis", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 2: Single Factor Verification (单因子验证)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate2_single_factor(self) -> GateReport:
        """Calculate Rank IC, NW-ICIR, Decay, and Monotonicity."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        # Calculate Rank IC daily
        ic_series = pd.Series(index=self.factor_df.index, dtype=float)
        for dt in self.factor_df.index:
            f_row = self.factor_df.loc[dt]
            r_row = self.forward_returns.loc[dt]
            mask = f_row.notna() & r_row.notna()
            if mask.sum() >= 20:
                ic, _ = stats.spearmanr(f_row[mask], r_row[mask])
                if not np.isnan(ic):
                    ic_series.loc[dt] = ic
        
        ic_series = ic_series.dropna()
        if len(ic_series) < 60:
            reasons.append(f"Insufficient trade dates for IC calculation: {len(ic_series)} dates")
            return GateReport(2, "Single Factor", False, "FAIL", {"ic_count": len(ic_series)}, "Gate 2 failed: Insufficient dates", reasons)
            
        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std())
        raw_icir = ic_mean / ic_std if ic_std > 0 else 0.0
        nw_icir = calculate_newey_west_icir(ic_series, lag=self.forward_days)
        ic_win_rate = float((ic_series > 0).mean()) if ic_mean > 0 else float((ic_series < 0).mean())
        
        metrics["ic_mean"] = ic_mean
        metrics["raw_icir"] = raw_icir
        metrics["nw_icir"] = nw_icir
        metrics["ic_win_rate"] = ic_win_rate
        metrics["ic_count"] = len(ic_series)

        if abs(nw_icir) < 0.03:
            reasons.append(f"NW-ICIR is below minimum threshold (0.03): observed={nw_icir:.4f}")
            verdict = "FAIL"
            
        # Group Monotonicity check
        # Group into 5 portfolios daily, calculate average next-period returns
        quantile_returns = []
        for dt in self.factor_df.index:
            f_row = self.factor_df.loc[dt].dropna()
            r_row = self.forward_returns.loc[dt].dropna()
            common = f_row.index.intersection(r_row.index)
            if len(common) < 30:
                continue
            
            f_row = f_row.loc[common]
            r_row = r_row.loc[common]
            
            # Rank factor into 5 bins
            try:
                bins = pd.qcut(f_row, 5, labels=False, duplicates="drop")
                bin_rets = r_row.groupby(bins).mean()
                if len(bin_rets) == 5:
                    quantile_returns.append(bin_rets.values)
            except Exception as _e:
                logger.debug("gate per-item computation skipped: %s: %s", type(_e).__name__, _e)
                
        if len(quantile_returns) > 20:
            mean_q_rets = np.mean(quantile_returns, axis=0)
            metrics["quantile_returns"] = list(mean_q_rets)
            # Correlation of group index with return
            mono_corr, _ = stats.spearmanr(np.arange(5), mean_q_rets)
            metrics["monotonicity_corr"] = float(mono_corr)
            
            if abs(mono_corr) < 0.8:
                reasons.append(f"Weak monotonicity across 5 factor quantiles: Spearman corr={mono_corr:.2f}")
                if verdict != "FAIL":
                    verdict = "WARN"
        else:
            metrics["monotonicity_corr"] = 0.0
            reasons.append("Insufficient data to compute group monotonicity")
            if verdict != "FAIL":
                verdict = "WARN"

        # IC Decay check (1d, 5d, 10d, 20d)
        decay = {}
        for p in [1, 5, 10, 20]:
            fwd_p = self.prices.close.pct_change(p).shift(-p)
            ic_p = []
            for dt in self.factor_df.index[::5]:  # sample dates for speed
                f_row = self.factor_df.loc[dt]
                r_row = fwd_p.loc[dt]
                mask = f_row.notna() & r_row.notna()
                if mask.sum() >= 30:
                    c, _ = stats.spearmanr(f_row[mask], r_row[mask])
                    if not np.isnan(c):
                        ic_p.append(c)
            decay[p] = float(np.mean(ic_p)) if ic_p else 0.0
            
        metrics["ic_decay"] = decay

        passed = (verdict != "FAIL")
        details = f"Gate 2: Rank IC={ic_mean:+.4f}, NW-ICIR={nw_icir:+.4f}, WinRate={ic_win_rate:.1%}, MonoCorr={metrics.get('monotonicity_corr', 0.0):.2f}"
        return GateReport(2, "Single Factor Verification", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 3: Neutralization Verification (中性化验证)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate3_neutralization(self) -> GateReport:
        """Cross-sectionally neutralize against Size and Industry."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        # Load Size (total_mv) and Industry
        from lake.load_lake import load_daily_basic_panel, load_fundamental_panel
        
        dates = self.factor_df.index
        codes = self.factor_df.columns
        
        # Load total_mv
        db_basic = load_daily_basic_panel(dates, fields=["total_mv"])
        total_mv = db_basic.get("total_mv", pd.DataFrame())
        
        # Load industry classification
        db_fund = load_fundamental_panel(dates, fields=["industry"])
        industry = db_fund.get("industry", pd.DataFrame())
        
        if total_mv.empty:
            # Fall back to rolling amount as size proxy
            total_mv = self.prices.amount.rolling(60).mean()
            reasons.append("total_mv missing from basic panels; fell back to rolling 60d amount as size proxy")
            
        # Standardize size (log mv)
        log_size = np.log(total_mv.replace(0, np.nan))
        
        # Perform cross-sectional regression to get residuals
        neutral_factor = pd.DataFrame(index=dates, columns=codes, dtype=float)
        
        # We sample dates to run this regression efficiently (e.g. every 5 trading days)
        sample_dates = dates[::5]
        for dt in sample_dates:
            y = self.factor_df.loc[dt].dropna()
            if len(y) < 30:
                continue
            
            # Align independent variables
            sz = log_size.loc[dt].reindex(y.index)
            
            # Create industry dummies
            ind = pd.Series(dtype=object)
            if not industry.empty and dt in industry.index:
                ind = industry.loc[dt].reindex(y.index).fillna("Unknown")
            else:
                ind = pd.Series("Unknown", index=y.index)
                
            ind_dummies = pd.get_dummies(ind, drop_first=True)
            
            # Combine X
            X = pd.DataFrame({"constant": 1.0, "size": sz})
            X = pd.concat([X, ind_dummies], axis=1).dropna()
            
            # Align y and X
            common = y.index.intersection(X.index)
            if len(common) < 30:
                continue
                
            y_clean = y.loc[common].values.astype(float)
            X_clean = X.loc[common].values.astype(float)
            
            try:
                # Solve OLS: b = (X'X)^(-1) X'y
                b, _, _, _ = np.linalg.lstsq(X_clean, y_clean, rcond=None)
                residuals = y_clean - X_clean @ b
                neutral_factor.loc[dt, common] = residuals
            except Exception as _e:
                logger.debug("gate per-item computation skipped: %s: %s", type(_e).__name__, _e)
                
        # Fill non-sampled dates with forward fill for simplified comparison
        # 只 ffill:bfill 会把未来残差回填到更早日期(未来函数隐患);
        # IC 只在 sample_dates 上算,早期 NaN 由 dropna 自然跳过。
        neutral_factor = neutral_factor.reindex(dates).ffill()
        
        # Calculate Rank IC of neutralized factor
        ic_series = pd.Series(index=dates, dtype=float)
        for dt in sample_dates:
            f_row = neutral_factor.loc[dt].dropna()
            r_row = self.forward_returns.loc[dt].dropna()
            common = f_row.index.intersection(r_row.index)
            if len(common) >= 30:
                ic, _ = stats.spearmanr(f_row.loc[common], r_row.loc[common])
                if not np.isnan(ic):
                    ic_series.loc[dt] = ic
                    
        ic_series = ic_series.dropna()
        neut_ic_mean = float(ic_series.mean()) if not ic_series.empty else 0.0
        neut_std = float(ic_series.std()) if not ic_series.empty else 1.0
        neut_raw_icir = neut_ic_mean / neut_std if neut_std > 0 else 0.0
        neut_nw_icir = calculate_newey_west_icir(ic_series, lag=self.forward_days)
        
        metrics["neut_ic_mean"] = neut_ic_mean
        metrics["neut_raw_icir"] = neut_raw_icir
        metrics["neut_nw_icir"] = neut_nw_icir
        
        # Compare neutralized vs raw
        # Retrieve raw NW-ICIR from Gate 2 if available, else recalculate
        raw_ic_series = pd.Series(index=dates, dtype=float)
        for dt in sample_dates:
            f_row = self.factor_df.loc[dt].dropna()
            r_row = self.forward_returns.loc[dt].dropna()
            common = f_row.index.intersection(r_row.index)
            if len(common) >= 30:
                ic, _ = stats.spearmanr(f_row.loc[common], r_row.loc[common])
                if not np.isnan(ic):
                    raw_ic_series.loc[dt] = ic
        raw_nw_icir = calculate_newey_west_icir(raw_ic_series.dropna(), lag=self.forward_days)
        
        icir_retention = abs(neut_nw_icir) / abs(raw_nw_icir) if raw_nw_icir > 0 else 0.0
        metrics["icir_retention"] = icir_retention
        
        if abs(neut_nw_icir) < 0.02:
            reasons.append(f"Neutralized NW-ICIR is too low (<0.02): observed={neut_nw_icir:.4f}")
            verdict = "FAIL"
        elif icir_retention < 0.50:
            reasons.append(f"Factor loses >50% of predictive power after Size & Industry neutralization. Retention={icir_retention:.1%}")
            verdict = "FAIL"
        elif icir_retention < 0.70:
            reasons.append(f"Significant decay after neutralization: Retention={icir_retention:.1%}")
            if verdict != "FAIL":
                verdict = "WARN"

        passed = (verdict != "FAIL")
        details = f"Gate 3: Neut NW-ICIR={neut_nw_icir:+.4f}, Retention={icir_retention:.1%} (Raw NW-ICIR={raw_nw_icir:+.4f})"
        return GateReport(3, "Neutralization Verification", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 4: Multiple Testing Penalty (多重检验)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate4_multiple_testing(self, observed_sr: float, returns_series: pd.Series) -> GateReport:
        """Compute Deflated Sharpe Ratio (DSR) and Probabilistic Sharpe Ratio (PSR)."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        # Calculate skewness and excess kurtosis of daily returns
        rets = returns_series.dropna()
        if len(rets) < 60:
            return GateReport(4, "Multiple Testing", False, "FAIL", {}, "Gate 4 failed: Insufficient return series", ["Returns series is too short"])
            
        skew = float(stats.skew(rets.values))
        kurt = float(stats.kurtosis(rets.values, fisher=False))  # Pearson definition where normal=3
        
        metrics["skewness"] = skew
        metrics["kurtosis"] = kurt
        metrics["n_periods"] = len(rets)
        
        # DSR
        dsr_report = deflated_sharpe(
            observed_sr=observed_sr,
            n_trials=self.n_trials,
            n_periods=len(rets),
            skew=skew,
            kurt=kurt,
            annualized=True
        )
        
        dsr = dsr_report["dsr"]
        dsr_p = dsr_report["p_value"]
        
        metrics["dsr"] = dsr
        metrics["dsr_p_value"] = dsr_p
        metrics["e_max_sr"] = dsr_report["e_max_sr"]
        metrics["dsr_significant"] = dsr_report["significant_05"]
        
        # PSR (testing benchmark Sharpe = 0.0)
        psr_val = probabilistic_sharpe_ratio(
            observed_sr=observed_sr,
            benchmark_sr=0.0,
            n_periods=len(rets),
            skewness=skew,
            kurt=kurt
        )
        metrics["psr"] = psr_val
        metrics["n_trials"] = self.n_trials

        # Decision rules
        if dsr_p > 0.05:
            reasons.append(f"Deflated Sharpe p-value is not significant (>0.05): p={dsr_p:.4f} after {self.n_trials} trials")
            verdict = "FAIL"
        if psr_val < 0.95:
            reasons.append(f"Probabilistic Sharpe Ratio (Sharpe>0) is too low: PSR={psr_val:.1%} (target >= 95.0%)")
            if verdict != "FAIL":
                verdict = "WARN"

        passed = (verdict != "FAIL")
        details = f"Gate 4: DSR p-val={dsr_p:.4f} (trials={self.n_trials}), PSR={psr_val:.1%}, Skew={skew:+.2f}, Kurt={kurt:.2f}"
        return GateReport(4, "Multiple Testing Penalty", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 5: Portfolio Backtesting (组合回测)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate5_backtest(self, signal: Signal, start: str = "2018-01-01") -> tuple[GateReport, BacktestResult]:
        """Run backtest on standard parameters and extract core metrics."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        # Initialize backtest config
        config = BacktestConfig(
            start=start,
            cost=CostModel(),
            leverage=1.25
        )
        
        # Run BacktestEngine
        engine = BacktestEngine(prices=self.prices, config=config)
        result = engine.run(signal)
        
        # Extract metrics
        m = result.metrics
        metrics["annual"] = m["annual"]
        metrics["maxdd"] = m["maxdd"]
        metrics["sharpe"] = m["sharpe"]
        metrics["calmar"] = m["calmar"]
        metrics["turnover_annual"] = float(result.detail["turnover"].mean() * 252)
        metrics["cost_annual"] = float(result.detail["cost"].mean() * 252)
        # 机构级风险/分布指标(随 BacktestResult.metrics 一并产出,落库供治理页风险卡)
        for k in ("sortino", "var_95", "cvar_95", "skew", "kurtosis_excess", "tail_ratio"):
            if k in m:
                metrics[k] = m[k]

        # Decision rules
        if m["annual"] < 0.15:
            reasons.append(f"Annualized return is below target (15.0%): observed={m['annual']:.2%}")
            verdict = "FAIL"
        if abs(m["maxdd"]) > 0.20:
            reasons.append(f"Maximum drawdown exceeds threshold (20.0%): observed={m['maxdd']:.2%}")
            verdict = "FAIL"
        if m["sharpe"] < 1.0:
            reasons.append(f"Sharpe ratio is below target (1.0): observed={m['sharpe']:.2f}")
            if verdict != "FAIL":
                verdict = "WARN"

        passed = (verdict != "FAIL")
        details = f"Gate 5: Annualized Return={m['annual']:.2%}, MaxDD={m['maxdd']:.2%}, Sharpe={m['sharpe']:.2f}, Calmar={m['calmar']:.2f}"
        return GateReport(5, "Portfolio Backtesting", passed, verdict, metrics, details, reasons), result

    # ───────────────────────────────────────────────────────────────────────
    # Gate 6: Cost & Capacity Modeling (成本容量)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate6_cost_capacity(self, signal: Signal, start: str = "2018-01-01") -> GateReport:
        """Run cost sensitivity and construct capacity curve with market impact."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        # 1. Cost Sensitivity: 1x, 2x, 3x costs
        # 压力费率从 CostModel 默认值派生(唯一权威 R-COST-001);融资利率不随倍数缩放(维持原口径)
        base_cost = CostModel()
        engine_1x = BacktestEngine(prices=self.prices, config=BacktestConfig(
            start=start, cost=base_cost, leverage=1.25
        ))
        res_1x = engine_1x.run(signal)

        engine_2x = BacktestEngine(prices=self.prices, config=BacktestConfig(
            start=start, cost=CostModel(buy_cost=base_cost.buy_cost * 2, sell_cost=base_cost.sell_cost * 2,
                                        financing_rate=base_cost.financing_rate), leverage=1.25
        ))
        res_2x = engine_2x.run(signal)

        engine_3x = BacktestEngine(prices=self.prices, config=BacktestConfig(
            start=start, cost=CostModel(buy_cost=base_cost.buy_cost * 3, sell_cost=base_cost.sell_cost * 3,
                                        financing_rate=base_cost.financing_rate), leverage=1.25
        ))
        res_3x = engine_3x.run(signal)
        
        metrics["annual_1x"] = res_1x.annual
        metrics["annual_2x"] = res_2x.annual
        metrics["annual_3x"] = res_3x.annual
        
        decay_rate = (res_1x.annual - res_3x.annual) / res_1x.annual if res_1x.annual > 0 else 1.0
        metrics["cost_decay_rate"] = float(decay_rate)
        
        if decay_rate > 0.50:
            reasons.append(f"High transaction cost sensitivity: 3x costs degrade returns by {decay_rate:.1%}")
            verdict = "FAIL"

        # 2. Capacity Curve Modeling
        # We model market impact cost as a linear-quadratic participation function of ADV:
        # Extra Slippage = 0.05 * (Trade Size / ADV)
        # Scales: 5M, 50M, 500M, 2B
        aum_scales = [5_000_000, 50_000_000, 500_000_000, 2_000_000_000]
        capacity_results = {}
        
        # Precompute 20-day rolling Average Daily Volume (ADV) in CNY
        adv = self.prices.amount.rolling(20).mean()
        
        # Precompute 20-day rolling daily volatility of each stock
        daily_ret = self.prices.close.pct_change(fill_method=None)
        vol_20d = daily_ret.rolling(20).std().fillna(0.02) # default to 2% daily vol if NaN
        
        for scale in aum_scales:
            # Re-run standard backtest but manually apply AUM-dependent market impact cost
            # Extract weight differences to estimate trade size
            weights = self._get_weights(signal)
            w_diff = weights.diff().abs().fillna(0.0)
            
            # Estimate daily trading size in CNY
            trade_cny = w_diff * scale
            
            # Align with ADV
            adv_aligned = adv.reindex_like(trade_cny)
            
            # Participation rate
            participation = trade_cny / (adv_aligned + 1.0)
            participation = participation.clip(lower=0.0, upper=0.5)
            
            # 1. Square-Root Market Impact Law:
            # Impact = Y * Vol * sqrt(Participation)  where Y = 1.0 (standard buy-side multiplier)
            single_day_slippage = 1.0 * vol_20d.reindex_like(participation) * np.sqrt(participation)
            
            # 2. Multi-Day Order Splitting Optimization:
            # Cost(N) = single_day_slippage / sqrt(N) + (N - 1) * alpha_decay
            # We assume a daily alpha decay of 0.001 (10 bps/day) for delayed execution
            alpha_decay = 0.001
            costs = []
            for n in range(1, 6):
                c = single_day_slippage / np.sqrt(n) + (n - 1) * alpha_decay
                costs.append(c)
            stacked = np.stack([c.values for c in costs], axis=0)
            min_cost = np.min(stacked, axis=0)
            impact_slippage = pd.DataFrame(min_cost, index=single_day_slippage.index, columns=single_day_slippage.columns)
            
            # Aggregated daily portfolio impact cost
            # Sum(stock_slippage * stock_weight)
            daily_impact = (impact_slippage * weights).sum(axis=1)
            
            # Adjust original returns
            net_rets = res_1x.returns - daily_impact.reindex(res_1x.returns.index).fillna(0.0)
            
            ann = net_rets.mean() * 252
            vol = net_rets.std() * np.sqrt(252)
            sr = ann / vol if vol > 0 else 0.0
            dd = ((1 + net_rets).cumprod() / (1 + net_rets).cumprod().cummax() - 1).min()
            
            capacity_results[str(scale)] = {
                "annual": float(ann),
                "sharpe": float(sr),
                "maxdd": float(dd)
            }
            
        metrics["capacity_curve"] = capacity_results
        
        # Decision: AUM capacity threshold (AUM where Sharpe falls below 0.5)
        capacity_limit_reached = False
        for scale in sorted(aum_scales):
            perf = capacity_results[str(scale)]
            if perf["sharpe"] < 0.5 or perf["annual"] < 0.05:
                capacity_limit_reached = True
                metrics["capacity_limit_aum"] = scale
                reasons.append(f"Capacity limit reached at {scale/1e6:.1f}M: Net Sharpe={perf['sharpe']:.2f}, Return={perf['annual']:.1%}")
                break
                
        if not capacity_limit_reached:
            metrics["capacity_limit_aum"] = aum_scales[-1]

        passed = (verdict != "FAIL")
        details = (
            f"Gate 6: Cost Decay={decay_rate:.1%}. Net Sharpe @ 5M={capacity_results['5000000']['sharpe']:.2f}, "
            f"@ 50M={capacity_results['50000000']['sharpe']:.2f}, @ 500M={capacity_results['500000000']['sharpe']:.2f}"
        )
        return GateReport(6, "Cost & Capacity Modeling", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 7: Out-of-Sample & Stress Testing (样本外与压力测试)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate7_stress_testing(self, signal: Signal, start: str = "2018-01-01") -> GateReport:
        """Execute walk-forward cross-validation, regime split, and execution delay."""
        reasons = []
        metrics = {}
        verdict = "PASS"
        
        # 1. Purged Walk-Forward
        dates = self.prices.close.index
        # Generate windows (3y train, 1y test, 20d purge)
        windows = walk_forward_windows(dates, train_years=3, test_years=1, purge_days=20)
        
        oos_rets_list = []
        for _, win in enumerate(windows):
            t_start = win["test_start"]
            t_end = win["test_end"]
            
            # Simple simulation for test window
            config = BacktestConfig(
                start=str(t_start.date()),
                cost=CostModel(),
                leverage=1.25
            )
            try:
                engine = BacktestEngine(prices=self.prices, config=config)
                # Subset weights for test window
                weights = self._get_weights(signal)
                test_weights = weights.loc[t_start:t_end]
                if len(test_weights) > 10:
                    res = engine._run_weight_backtest(test_weights, signal.timing, signal)
                    oos_rets_list.append(res.returns)
            except Exception as _e:
                logger.debug("gate per-item computation skipped: %s: %s", type(_e).__name__, _e)
                
        if oos_rets_list:
            wfm = wf_metrics(oos_rets_list)
            metrics["wf_annual"] = wfm.annual
            metrics["wf_sharpe"] = wfm.sharpe
            metrics["wf_maxdd"] = wfm.maxdd
            metrics["wf_positive_ratio"] = wfm.win_rate
            
            if wfm.win_rate < 0.50:
                reasons.append(f"OOS Walk-Forward positive window ratio is below 50.0%: win_rate={wfm.win_rate:.1%}")
                verdict = "FAIL"
            elif wfm.sharpe < 0.50:
                reasons.append(f"OOS Walk-Forward aggregate Sharpe is weak (<0.50): observed={wfm.sharpe:.2f}")
                if verdict != "FAIL":
                    verdict = "WARN"
        else:
            metrics["wf_annual"] = 0.0
            metrics["wf_sharpe"] = 0.0
            metrics["wf_maxdd"] = 0.0
            metrics["wf_positive_ratio"] = 0.0
            reasons.append("Walk-Forward validation failed to run due to lack of windows")
            verdict = "FAIL"

        # 2. Delayed Execution (T+1 vs T+2)
        # Shift target weights by 1 day and 2 days to test decay sensitivity
        try:
            weights = self._get_weights(signal)
            for delay in [1, 2]:
                shifted_weights = _shift_decision_weights(
                    weights, self.prices.close.index, delay)
                config = BacktestConfig(
                    start=start,
                    cost=CostModel(),
                    leverage=1.25
                )
                engine = BacktestEngine(prices=self.prices, config=config)
                res_delayed = engine._run_weight_backtest(shifted_weights, signal.timing, signal)
                metrics[f"annual_delay_{delay}d"] = res_delayed.annual
                metrics[f"sharpe_delay_{delay}d"] = res_delayed.sharpe
                
        except Exception as e:
            reasons.append(f"Delayed execution test failed: {str(e)}")
            if verdict != "FAIL":
                verdict = "WARN"

        # 3. Regime Split Analysis (Bull vs Bear Market states)
        mkt_ret = self.daily_returns.mean(axis=1)
        mkt_idx = (1 + mkt_ret).cumprod()
        mkt_ma = mkt_idx.rolling(16).mean()
        bull_mask = mkt_idx > mkt_ma
        
        # Standard backtest returns
        try:
            standard_engine = BacktestEngine(prices=self.prices, config=BacktestConfig(
                start=start, cost=CostModel(), leverage=1.25
            ))
            res_std = standard_engine.run(signal)
            rets_std = res_std.returns
            
            # Align masks
            common_idx = rets_std.index.intersection(bull_mask.index)
            bull_rets = rets_std.loc[common_idx][bull_mask.loc[common_idx]]
            bear_rets = rets_std.loc[common_idx][~bull_mask.loc[common_idx]]
            
            metrics["bull_annual"] = float(bull_rets.mean() * 252) if len(bull_rets) > 0 else 0.0
            metrics["bull_sharpe"] = float(bull_rets.mean() / (bull_rets.std() + 1e-8) * np.sqrt(252)) if len(bull_rets) > 0 else 0.0
            metrics["bear_annual"] = float(bear_rets.mean() * 252) if len(bear_rets) > 0 else 0.0
            metrics["bear_sharpe"] = float(bear_rets.mean() / (bear_rets.std() + 1e-8) * np.sqrt(252)) if len(bear_rets) > 0 else 0.0
            
            # If strategy only makes money in bull markets and goes bankrupt in bear markets
            if metrics["bear_annual"] < -0.15 and metrics["bear_sharpe"] < -0.5:
                reasons.append(f"Extreme regime dependency: Bear market return is severely negative: {metrics['bear_annual']:.2%}")
                if verdict != "FAIL":
                    verdict = "WARN"
        except Exception as _e:
            logger.warning("Gate7 regime-dependency block skipped: %s: %s", type(_e).__name__, _e)

        passed = (verdict != "FAIL")
        details = (
            f"Gate 7: WF Sharpe={metrics['wf_sharpe']:.2f} (win={metrics['wf_positive_ratio']:.1%}). "
            f"Delay 1d Sharpe={metrics.get('sharpe_delay_1d', 0.0):.2f}. Bull Sharpe={metrics.get('bull_sharpe', 0.0):.2f}, Bear Sharpe={metrics.get('bear_sharpe', 0.0):.2f}"
        )
        return GateReport(7, "Out-of-Sample & Stress Testing", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 7A: Purged + Embargoed Cross-Validation (防信息泄露交叉验证)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate7a_purged_embargoed_cv(self, signal: Signal, start: str = "2018-01-01") -> GateReport:
        """Execute cross-validation using purged and embargoed sample splits to prevent overlap contamination."""
        reasons = []
        metrics = {}
        passed = True
        verdict = "PASS"

        # Apply purging/embargoing conditions if forward days is greater than 1
        rebalance_period = 20
        if hasattr(signal, "rebalance_freq") and isinstance(signal.rebalance_freq, str):
            import re
            m = re.search(r"\d+", signal.rebalance_freq)
            if m:
                rebalance_period = int(m.group())
        elif hasattr(signal, "rebalance_days") and isinstance(signal.rebalance_days, int):
            rebalance_period = signal.rebalance_days

        horizon = self.forward_days
        if horizon > 1:
            purge_window = horizon
            embargo_window = max(horizon, rebalance_period)
        else:
            purge_window = 0
            embargo_window = 0

        metrics["purge_window"] = purge_window
        metrics["embargo_window"] = embargo_window
        metrics["forward_horizon"] = horizon
        # Task 14 诚实命名:本策略是固定公式(无可训练参数/模型选择),因此这里做的是
        # rolling-origin 稳定性(每窗口因果重放),而非「净化模型选择 CV」。不得声称后者。
        # 真正的 purged model-selection CV 仅当提供 fit callback 时适用(见 rolling_origin.py)。
        metrics["method"] = "rolling_origin_stability"
        metrics["model_selection_cv"] = False

        dates = self.prices.close.index
        total_purge_days = purge_window + embargo_window
        
        from core.analysis.walk_forward import walk_forward_windows, wf_metrics
        windows = walk_forward_windows(dates, train_years=3, test_years=1, purge_days=total_purge_days)
        
        oos_rets_list = []
        for win in windows:
            t_start = win["test_start"]
            t_end = win["test_end"]
            
            config = BacktestConfig(
                start=str(t_start.date()),
                cost=CostModel(),
                leverage=1.25
            )
            try:
                engine = BacktestEngine(prices=self.prices, config=config)
                weights = self._get_weights(signal)
                test_weights = weights.loc[t_start:t_end]
                if len(test_weights) > 10:
                    res = engine._run_weight_backtest(test_weights, signal.timing, signal)
                    oos_rets_list.append(res.returns)
            except Exception as _e:
                logger.debug("gate per-item computation skipped: %s: %s", type(_e).__name__, _e)
                
        if oos_rets_list:
            wfm = wf_metrics(oos_rets_list)
            metrics["cv_sharpe"] = wfm.sharpe
            metrics["cv_annual"] = wfm.annual
            metrics["cv_maxdd"] = wfm.maxdd
            metrics["cv_win_rate"] = wfm.win_rate
            
            if wfm.sharpe < 0.40:
                reasons.append(f"Purged + Embargoed CV Sharpe is too weak: observed={wfm.sharpe:.2f}")
                passed = False
                verdict = "FAIL"
        else:
            metrics["cv_sharpe"] = 0.0
            metrics["cv_annual"] = 0.0
            metrics["cv_maxdd"] = 0.0
            metrics["cv_win_rate"] = 0.0
            reasons.append("Walk-Forward CV failed: no windows generated")
            passed = False
            verdict = "FAIL"

        details = (
            f"Gate 7A: CV Sharpe={metrics['cv_sharpe']:.2f} (win={metrics['cv_win_rate']:.1%}), "
            f"Purge={purge_window}d, Embargo={embargo_window}d"
        )
        return GateReport("7A", "Purged + Embargoed CV", passed, verdict, metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Gate 8: Live Monitoring (实盘监控)
    # ───────────────────────────────────────────────────────────────────────
    def run_gate8_live_monitoring(
        self,
        backtest_report: dict[str, Any]
    ) -> GateReport:
        """Produce concrete thresholds and boundaries for live tracking."""
        metrics = {}
        reasons = []
        
        # 1. Expected daily mean and standard deviation
        bt_annual = backtest_report.get("annual", 0.15)
        bt_vol = backtest_report.get("vol", 0.15)
        
        daily_mean_expected = bt_annual / 252
        daily_vol_expected = bt_vol / np.sqrt(252)
        
        metrics["daily_mean_expected"] = daily_mean_expected
        metrics["daily_vol_expected"] = daily_vol_expected
        
        # 2. Live IC confidence boundary (95% standard error boundary for 20-day roll)
        metrics["live_ic_se_20d"] = 1.0 / np.sqrt(20)
        metrics["live_ic_lower_limit"] = float(backtest_report.get("ic_mean", 0.05) - 2.0 * (1.0 / np.sqrt(252)))
        
        # 3. Model stop-loss curve (cumulative underperformance boundary)
        metrics["monitoring_stop_loss_trigger"] = "performance falls below E[R] - 2 * std_dev * sqrt(days_live)"
        metrics["max_live_drawdown_limit"] = float(1.5 * backtest_report.get("maxdd", -0.20))
        
        # 4. Exposure tracking thresholds
        metrics["max_style_drift_tracking_error"] = 0.05  # 5% tracking error
        metrics["max_sector_deviation"] = 0.15  # 15% active weight in any sector
        
        details = (
            f"Gate 8: Live tracking profile constructed. Daily expected mean={daily_mean_expected:.4%}, "
            f"Expected Vol={daily_vol_expected:.4%}. Max Live Drawdown Limit={metrics['max_live_drawdown_limit']:.1%}"
        )
        return GateReport(8, "Live Monitoring", True, "PASS", metrics, details, reasons)

    # ───────────────────────────────────────────────────────────────────────
    # Unified Executive Runner
    # ───────────────────────────────────────────────────────────────────────
    def evaluate_all(self, signal: Signal, start: str = "2018-01-01") -> list[GateReport]:
        """Run all 9 gates in sequence and return a list of GateReports."""
        reports = []
        
        # Gate 0: Data Audit
        reports.append(self.run_gate0_data_audit())
        
        # Gate 1: Economic Hypothesis
        reports.append(self.run_gate1_hypothesis())
        
        # Gate 2: Single Factor Verification
        g2_rep = self.run_gate2_single_factor()
        reports.append(g2_rep)
        
        # Gate 3: Neutralization Verification
        reports.append(self.run_gate3_neutralization())
        
        # Gate 5: Portfolio Backtesting (Run first to generate return series for Gate 4 & Gate 6)
        g5_rep, bt_res = self.run_gate5_backtest(signal, start)
        self.gate5_returns = bt_res.returns   # 留存给 lineage/PBO(2B/2C)复用,避免二次回测

        # Gate 4: Multiple Testing Penalty
        observed_sr = bt_res.sharpe
        reports.append(self.run_gate4_multiple_testing(observed_sr, bt_res.returns))
        
        # Add Gate 5 report to the list
        reports.append(g5_rep)
        
        # Gate 6: Cost & Capacity Modeling
        reports.append(self.run_gate6_cost_capacity(signal, start))
        
        # Gate 7: Out-of-Sample & Stress Testing
        reports.append(self.run_gate7_stress_testing(signal, start))

        # Gate 7A: Purged + Embargoed CV
        reports.append(self.run_gate7a_purged_embargoed_cv(signal, start))
        
        # Gate 8: Live Monitoring
        bt_summary = {
            "annual": float(bt_res.annual),
            "vol": float(bt_res.vol),
            "maxdd": float(bt_res.maxdd),
            "ic_mean": float(g2_rep.metrics.get("ic_mean", 0.05))
        }
        reports.append(self.run_gate8_live_monitoring(bt_summary))
        
        return reports


@dataclass
class NineGatesReport:
    """Consolidated report for the 9-Gate Evaluation."""
    factor_name: str
    run_date: str
    passed_all: bool
    reports: list[GateReport]

    def summarize(self) -> dict:
        """抽取台账级审计摘要（DSR/PSR/多重检验 + WF/CV + 容量），写入 strategy_versions 的 nine_gate 字段。

        机构级要求：每个在册版本须留存多重检验惩罚证据（DSR p-value / n_trials / PSR）与 OOS 稳健性，
        而非仅样本内绩效。键缺失安全跳过。
        """
        by_id = {r.gate_id: (r.metrics or {}) for r in self.reports}
        verdict_by_id = {r.gate_id: r.verdict for r in self.reports}
        g2, g3 = by_id.get(2, {}), by_id.get(3, {})
        g4, g5, g6 = by_id.get(4, {}), by_id.get(5, {}), by_id.get(6, {})
        g7, g7a, g8 = by_id.get(7, {}), by_id.get("7A", by_id.get("7a", {})), by_id.get(8, {})
        out = {
            "run_date": self.run_date,
            "passed_all": self.passed_all,
            "dsr": g4.get("dsr"),
            "dsr_p": g4.get("dsr_p_value"),
            "dsr_significant": g4.get("dsr_significant"),
            "psr": g4.get("psr"),
            "n_trials": g4.get("n_trials"),
            "skew": g4.get("skew"),
            "kurtosis": g4.get("kurt"),
            "wf_sharpe": g7.get("wf_sharpe"),
            "wf_positive_ratio": g7.get("wf_positive_ratio"),
            "cv_sharpe": g7a.get("cv_sharpe"),
            "cv_win_rate": g7a.get("cv_win_rate"),
            "capacity_decay": g6.get("decay_rate") if isinstance(g6, dict) else None,
            # 机构级风险/分布指标(来自 Gate5 回测的 BacktestResult.metrics)
            "sortino": g5.get("sortino"),
            "var_95": g5.get("var_95"),
            "cvar_95": g5.get("cvar_95"),
            "tail_ratio": g5.get("tail_ratio"),
            # 因子有效性(Gate2 单因子)—— Rank ICIR(NW)/单调性/IC衰减,而非只看组合收益
            "ic_mean": g2.get("ic_mean"),
            "nw_icir": g2.get("nw_icir"),
            "ic_win_rate": g2.get("ic_win_rate"),
            "monotonicity_corr": g2.get("monotonicity_corr"),
            "ic_decay": g2.get("ic_decay"),
            # 中性化后残差/增量(Gate3)—— 判断是不是风格伪装;icir_retention=中性化后 alpha 留存
            "neut_nw_icir": g3.get("neut_nw_icir"),
            "icir_retention": g3.get("icir_retention"),
            # 成本/容量(Gate6)—— gross→net 成本衰减、容量上限 AUM
            "cost_decay_rate": g6.get("cost_decay_rate"),
            "capacity_limit_aum": g6.get("capacity_limit_aum"),
            "annual_1x": g6.get("annual_1x"),
            "annual_2x": g6.get("annual_2x"),
            "annual_3x": g6.get("annual_3x"),
            # Regime 拆分(Gate7)—— 牛/熊夏普,识别 regime 依赖
            "bull_sharpe": g7.get("bull_sharpe"),
            "bear_sharpe": g7.get("bear_sharpe"),
            "bull_annual": g7.get("bull_annual"),
            "bear_annual": g7.get("bear_annual"),
            # 实盘监控风控硬边界(Gate 8)
            "daily_mean_expected": g8.get("daily_mean_expected"),
            "daily_vol_expected": g8.get("daily_vol_expected"),
            "max_live_drawdown_limit": g8.get("max_live_drawdown_limit"),
            "gate4_verdict": verdict_by_id.get(4),
            "gate7_verdict": verdict_by_id.get(7),
        }
        return {k: v for k, v in out.items() if v is not None}

    def to_markdown(self) -> str:
        """Format the 9-Gate report as a clean GitHub-style Markdown document."""
        passed_str = "✅ APPROVED (ALL GATES PASSED)" if self.passed_all else "❌ REJECTED (GATES FAILED)"
        
        md = []
        md.append(f"# Research-to-Production Risk Report: {self.factor_name}")
        md.append(f"**Run Date**: {self.run_date} | **Overall Verdict**: {passed_str}\n")
        
        md.append("## Executive Summary of Gates")
        md.append("| Gate | Name | Status | Verdict | Details |")
        md.append("| --- | --- | --- | --- | --- |")
        
        for r in self.reports:
            status_emoji = "✅ PASS" if r.verdict == "PASS" else "⚠️ WARN" if r.verdict == "WARN" else "❌ FAIL"
            md.append(f"| {r.gate_id} | {r.name} | {status_emoji} | {r.verdict} | {r.details} |")
        
        md.append("\n## Detailed Gate Findings & Failures")
        for r in self.reports:
            status_emoji = "✅ PASS" if r.verdict == "PASS" else "⚠️ WARN" if r.verdict == "WARN" else "❌ FAIL"
            md.append(f"### Gate {r.gate_id}: {r.name} ({status_emoji})")
            if r.reasons:
                md.append("**Reasons/Warnings:**")
                for reason in r.reasons:
                    md.append(f"- {reason}")
            else:
                md.append("No errors or warnings detected.")
                
            md.append("\n**Key Metrics:**")
            md.append("```json")
            clean_metrics = {}
            for k, v in r.metrics.items():
                if isinstance(v, float):
                    clean_metrics[k] = round(v, 4)
                elif isinstance(v, list) and all(isinstance(x, float) for x in v):
                    clean_metrics[k] = [round(x, 4) for x in v]
                else:
                    clean_metrics[k] = v
            md.append(json.dumps(clean_metrics, indent=2, ensure_ascii=False))
            md.append("```\n")
            
        return "\n".join(md)
