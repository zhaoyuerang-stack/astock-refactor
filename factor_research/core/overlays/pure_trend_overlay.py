"""Pure trend market timing overlay.

tw=2 walk-forward validated: 12/12 years select tw=2, IS Sharpe 4.25-4.96.
Equal-weight mkt_ret aligns with small-cap strategy perspective (Insight 4).
"""
import pandas as pd


class PureTrendOverlay:
    """Exit when equal-weight market return rolling(tw).sum() < 0.

    Used as an AND condition on top of MA16 small-cap timing.
    """

    def __init__(self, trend_window: int = 2):
        self.trend_window = trend_window

    def exposure_series(self, close: pd.DataFrame) -> pd.Series:
        """Vectorized exposure for backtesting (shift(1): T trend → T+1 exposure)."""
        mkt_ret = close.pct_change(fill_method=None).replace(
            [float("inf"), float("-inf")], float("nan")
        ).mean(axis=1).fillna(0.0)
        trend = mkt_ret.rolling(self.trend_window).sum()
        return (trend >= 0).astype(float).shift(1, fill_value=1.0)

    def signal(self, target_date, close: pd.DataFrame) -> float:
        """Return 1.0 (stay in) or 0.0 (exit) for next-day exposure.

        trend on target_date < 0 → block next day.
        """
        mkt_ret = close.pct_change(fill_method=None).replace(
            [float("inf"), float("-inf")], float("nan")
        ).mean(axis=1).fillna(0.0)
        trend = mkt_ret.rolling(self.trend_window).sum()
        dt = pd.Timestamp(target_date).normalize()
        if dt not in trend.index:
            return 1.0
        return 0.0 if float(trend.loc[dt]) < 0 else 1.0
