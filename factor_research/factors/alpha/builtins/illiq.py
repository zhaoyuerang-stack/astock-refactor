"""Amihud (2002) illiquidity factor and size proxy.

AmihudIlliq  Illiq = mean(|ret| / amount) over window.  Classic definition.
              Positive = more illiquid → higher expected return (liquidity premium).

SizeProxy     -ln(avg_amount + 1).  Simplified version used in the report.
              Correlated ~0.9 with market cap.  Positive = smaller stocks.

Both use amount in CNY from FactorData (canonical: volume shares × raw_close).
"""
import numpy as np
import pandas as pd

from factors.alpha.base import Factor, FactorData


class AmihudIlliq(Factor):
    """Classic Amihud (2002) illiquidity: mean(|daily return| / dollar volume).

    Parameters
    ----------
    window : int
        Rolling window for averaging (default 20).
    """

    def __init__(self, window: int = 20):
        self.window = window

    def compute(self, data: FactorData) -> pd.DataFrame:
        ret = data.close.pct_change(fill_method=None).abs()
        # amount is already volume × 100 × raw_close
        illiq_daily = ret / (data.amount.replace(0, np.nan) + 1.0)
        return illiq_daily.rolling(self.window).mean()


class SizeProxy(Factor):
    """Size proxy via negative log of average dollar volume.

    This is the factor used in the illiquidity strategy report (v2.1).
    -ln(avg_amount_window + 1), then cross-sectionally standardized.

    Positive = smaller / lower-turnover stocks (the "small-cap" leg).

    Parameters
    ----------
    window : int
        Rolling window for average amount (default 60).
    """

    def __init__(self, window: int = 60):
        self.window = window

    def compute(self, data: FactorData) -> pd.DataFrame:
        avg_amount = data.amount.rolling(self.window).mean()
        return -np.log1p(avg_amount)
