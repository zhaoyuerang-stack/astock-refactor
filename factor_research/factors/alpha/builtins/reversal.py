"""Short-term reversal factor.

A股 has a well-documented short-term reversal effect (1-2 weeks).
Positive = stocks that fell recently → expected to bounce.
"""
import pandas as pd

from factors.alpha.base import Factor, FactorData


class ShortReversal(Factor):
    """N-day return negated (short-term reversal).

    Parameters
    ----------
    window : int
        Lookback period (default 5).
    """

    def __init__(self, window: int = 5):
        self.window = window

    def compute(self, data: FactorData) -> pd.DataFrame:
        return -(data.close / data.close.shift(self.window) - 1.0)
