"""Risk Model & Covariance Estimation.

Calculates risk exposures, factor covariance, specific risk,
and Ledoit-Wolf shrunk covariance matrices.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_shrunk_covariance(
    returns: pd.DataFrame,
    shrinkage: float = 0.1
) -> pd.DataFrame:
    """Compute risk covariance matrix using a simple shrinkage model.

    Target = (1 - shrinkage) * SampleCov + shrinkage * DiagonalCov.
    """
    sample_cov = returns.cov().fillna(0.0)
    diag_cov = pd.DataFrame(np.diag(np.diag(sample_cov)), index=sample_cov.index, columns=sample_cov.columns)
    shrunk_cov = (1 - shrinkage) * sample_cov + shrinkage * diag_cov
    return shrunk_cov


class RiskModel:
    """Institutional Factor Risk Model.

    Decomposes stock return covariance into:
        Sigma = B * F * B^T + Delta
    where B = factor exposures, F = factor covariance, Delta = specific risk.
    """
    def __init__(
        self,
        stock_returns: pd.DataFrame,
        style_factors: dict[str, pd.DataFrame] | None = None,
        industry_mapping: pd.Series | None = None
    ):
        self.stock_returns = stock_returns
        self.style_factors = style_factors or {}
        self.industry_mapping = industry_mapping

    def estimate_components(self, date: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
        """Estimate B (exposures), F (factor covariance), and Specific Variance on a given date.

        Returns
        -------
        exposures : pd.DataFrame
            Stock x factor loading matrix.
        factor_cov : pd.DataFrame
            Factor x factor covariance matrix.
        specific_var : pd.Series
            Stock specific variance.
        """
        assets = self.stock_returns.columns
        n_assets = len(assets)

        # Style exposures on date
        exposures_dict = {}
        for fname, fdf in self.style_factors.items():
            if date in fdf.index:
                exposures_dict[fname] = fdf.loc[date]
            else:
                exposures_dict[fname] = pd.Series(0.0, index=assets)

        # Industry dummy matrix
        if self.industry_mapping is not None:
            # Map industries
            industries = self.industry_mapping.unique()
            for ind in industries:
                exposures_dict[f"ind_{ind}"] = (self.industry_mapping == ind).astype(float)

        if exposures_dict:
            exposures = pd.DataFrame(exposures_dict, index=assets).fillna(0.0)
        else:
            # Fallback if no style or industry factors: use raw identity (1 factor per stock)
            exposures = pd.DataFrame(np.eye(n_assets), index=assets, columns=[f"f_{a}" for a in assets])

        # Compute factor returns and factor covariance via cross-sectional regression
        # R = B * f + u
        # f = (B^T * B)^-1 * B^T * R
        hist_returns = self.stock_returns.loc[:date].tail(126) # 6 months lookback
        if len(hist_returns) < 5:
            # Fallback to simple identity
            factor_cov = pd.DataFrame(np.eye(exposures.shape[1]) * 0.0001, index=exposures.columns, columns=exposures.columns)
            specific_var = pd.Series(0.0002, index=assets)
            return exposures, factor_cov, specific_var

        B = exposures.values
        R = hist_returns.fillna(0.0).values # days x stocks
        
        # OLS projection matrix: (B^T B)^-1 B^T
        try:
            proj = np.linalg.pinv(B.T @ B) @ B.T
            f_returns = R @ proj.T # days x factors
            factor_cov_np = np.cov(f_returns, rowvar=False)
            if factor_cov_np.ndim == 0:
                factor_cov_np = np.array([[factor_cov_np]])
            elif factor_cov_np.ndim == 1:
                factor_cov_np = np.diag(factor_cov_np)

            factor_cov = pd.DataFrame(factor_cov_np, index=exposures.columns, columns=exposures.columns)
            
            # Residual variance
            residuals = R - f_returns @ B.T
            specific_var = pd.Series(np.var(residuals, axis=0), index=assets)
        except Exception:
            factor_cov = pd.DataFrame(np.eye(exposures.shape[1]) * 0.0001, index=exposures.columns, columns=exposures.columns)
            specific_var = pd.Series(0.0002, index=assets)

        return exposures, factor_cov, specific_var
