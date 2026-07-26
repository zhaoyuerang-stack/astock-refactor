"""Unit tests for MovingAverageOverlay and its parameter audit logic."""
from __future__ import annotations

import unittest

import pandas as pd

from core.overlays.moving_average_overlay import MovingAverageOverlay


class TestMovingAverageOverlay(unittest.TestCase):

    def setUp(self):
        # Create a mock panel of 5 days, 4 stocks
        dates = pd.date_range("2023-01-01", periods=10)
        self.close = pd.DataFrame(
            {
                "S1": [10.0, 10.1, 10.2, 10.1, 10.3, 10.4, 10.2, 10.5, 10.6, 10.7],
                "S2": [20.0, 19.9, 20.1, 20.2, 20.0, 20.3, 20.1, 20.4, 20.5, 20.6],
                "S3": [30.0, 30.2, 29.8, 30.1, 30.3, 30.5, 30.2, 30.6, 30.8, 31.0],
                "S4": [40.0, 39.8, 40.2, 40.1, 40.4, 40.5, 40.1, 40.7, 40.9, 41.2],
            },
            index=dates,
        )
        self.amount = pd.DataFrame(
            {
                # S1 and S2 will have small average amount
                "S1": [1000, 1100, 1050, 1020, 1080, 1120, 1090, 1150, 1160, 1170],
                "S2": [2000, 2100, 2050, 2020, 2080, 2120, 2090, 2150, 2160, 2170],
                "S3": [9000, 9100, 9050, 9020, 9080, 9120, 9090, 9150, 9160, 9170],
                "S4": [8000, 8100, 8050, 8020, 8080, 8120, 8090, 8150, 8160, 8170],
            },
            index=dates,
        )

    def test_nav_and_exposure_calculation(self):
        overlay = MovingAverageOverlay(ma_window=3, rolling_rank_window=2)
        nav = overlay.build_index_nav(self.close, self.amount)

        # NAV should start at 1.0 (fill_value 0.0 cumulative product)
        self.assertEqual(nav.iloc[0], 1.0)
        self.assertEqual(len(nav), 10)

        binary_exp = overlay.exposure_series(self.close, self.amount)
        self.assertEqual(len(binary_exp), 10)
        self.assertTrue(set(binary_exp.unique()).issubset({0.0, 1.0}))

        band_exp = overlay.band_exposure_series(self.close, self.amount, multiplier=5.0, max_exposure=1.2)
        self.assertEqual(len(band_exp), 10)
        self.assertTrue((band_exp >= 0.0).all())
        self.assertTrue((band_exp <= 1.2).all())

    def test_signal_generation(self):
        overlay = MovingAverageOverlay(ma_window=3, rolling_rank_window=2)
        target_date = self.close.index[-2]

        # Test binary signal
        sig_bin = overlay.signal(target_date, self.close, self.amount, mode="binary")
        self.assertIn(sig_bin, (0.0, 1.0))

        # Test band signal
        sig_band = overlay.signal(target_date, self.close, self.amount, mode="band", multiplier=6.0, max_exposure=1.5)
        self.assertTrue(0.0 <= sig_band <= 1.5)

    def test_parameter_audit(self):
        overlay = MovingAverageOverlay(ma_window=3, rolling_rank_window=2)
        report = overlay.audit_parameters(self.close, self.amount, windows=[2, 3, 4])

        self.assertEqual(report["chosen_window"], 3)
        self.assertEqual(report["n_trials"], 3)
        self.assertIn("dsr_p_value", report)
        self.assertIn("dsr_significant", report)
        self.assertEqual(len(report["results"]), 3)


if __name__ == "__main__":
    unittest.main()
