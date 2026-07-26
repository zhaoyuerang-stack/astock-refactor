"""Price momentum factor — N-day cumulative return.

Positive = stocks with higher recent returns (momentum).
For A股, short-term (<1 month) is typically reversal, not momentum.
"""
import pandas as pd

from factors.alpha.base import Factor, FactorData


class PriceMomentum(Factor):
    """N-day cumulative return.

    Parameters
    ----------
    window : int
        Lookback period in trading days.
    """

    def __init__(self, window: int = 60):
        self.window = window

    def compute(self, data: FactorData) -> pd.DataFrame:
        return data.close / data.close.shift(self.window) - 1.0
