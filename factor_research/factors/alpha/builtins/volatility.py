"""Volatility factor — rolling standard deviation of returns.

Positive = higher volatility (risk premium / attention proxy in A股).
Use .neg() to get low-volatility factor.
"""
import pandas as pd

from factors.alpha.base import Factor, FactorData


class Volatility(Factor):
    """N-day rolling return volatility.

    Parameters
    ----------
    window : int
        Lookback period (default 20).
    """

    def __init__(self, window: int = 20):
        self.window = window

    def compute(self, data: FactorData) -> pd.DataFrame:
        # Phase-2: when FactorData.tradable is set, exclude limit/suspend closes
        # from the rolling window (upstream contamination hygiene).
        if data.tradable is not None:
            from factors.tradable_mask import masked_pct_change

            ret = masked_pct_change(data.close, data.tradable)
            mp = max(5, self.window // 2)
            return ret.rolling(self.window, min_periods=mp).std()
        ret = data.close.pct_change(fill_method=None)
        return ret.rolling(self.window).std()
