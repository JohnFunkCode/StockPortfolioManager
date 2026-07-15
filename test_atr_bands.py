"""Tests for ATR analytics (true_range_series / atr_series) and
PricesService.get_atr_bands (issue #93, Phase 1).

Analytics tests are exact-value on hand-computed series; the service test uses
the mocked-collaborator pattern from test_prices_history_policy.py — no
network, no DB.
"""
import unittest
from unittest.mock import Mock

import pandas as pd

from quantcore.analytics.indicators import atr_series, true_range_series
from quantcore.services.prices import PricesService


def make_service():
    return PricesService(
        ohlcv_repository=Mock(),
        yfinance_gateway=Mock(),
        options_repository=Mock(),
        sentiment_repository=Mock(),
    )


def make_ohlcv(bars):
    """bars = list of (high, low, close) tuples → OHLCV DataFrame on business days."""
    idx = pd.bdate_range("2026-01-02", periods=len(bars))
    return pd.DataFrame(
        {
            "Open":   [c for _, _, c in bars],
            "High":   [h for h, _, _ in bars],
            "Low":    [l for _, l, _ in bars],
            "Close":  [c for _, _, c in bars],
            "Volume": [1_000] * len(bars),
        },
        index=idx,
    )


# Hand-computed case, period=3 (alpha=1/3):
#   TR = [2, 2, 2, 5, 2]  (bar 3 gaps: |H−prev C| = |18−13| = 5)
#   ATR (seed = first TR, then ATR_t = (1−α)·ATR_{t−1} + α·TR_t):
#   [2, 2, 2, 3, 8/3]
HAND_BARS = [
    (12, 10, 11),
    (13, 11, 12),
    (14, 12, 13),
    (18, 15, 16),
    (17, 15, 16),
]


class TestTrueRangeAndATR(unittest.TestCase):
    def setUp(self):
        self.df = make_ohlcv(HAND_BARS)

    def test_true_range_exact(self):
        tr = true_range_series(self.df["High"], self.df["Low"], self.df["Close"])
        expected = [2.0, 2.0, 2.0, 5.0, 2.0]
        for i, exp in enumerate(expected):
            self.assertAlmostEqual(float(tr.iloc[i]), exp, places=10)

    def test_true_range_first_bar_is_high_minus_low(self):
        tr = true_range_series(self.df["High"], self.df["Low"], self.df["Close"])
        self.assertAlmostEqual(float(tr.iloc[0]), 12 - 10, places=10)

    def test_wilder_atr_exact(self):
        atr = atr_series(self.df["High"], self.df["Low"], self.df["Close"], period=3)
        expected = [2.0, 2.0, 2.0, 3.0, 8.0 / 3.0]
        for i, exp in enumerate(expected):
            self.assertAlmostEqual(float(atr.iloc[i]), exp, places=10)

    def test_gap_dominates_true_range(self):
        # Bar 3's intrabar range is only 3, but the gap from prior close makes TR 5.
        tr = true_range_series(self.df["High"], self.df["Low"], self.df["Close"])
        self.assertAlmostEqual(float(tr.iloc[3]), 5.0, places=10)


class TestGetATRBands(unittest.TestCase):
    def setUp(self):
        self.service = make_service()
        # 60 quiet bars around 100: H=101, L=99, C=100 → TR converges to 2.
        self.quiet = make_ohlcv([(101, 99, 100)] * 60)

    def test_invalid_interval_raises(self):
        with self.assertRaises(ValueError):
            self.service.get_atr_bands("INTC", interval="13m")

    def test_not_enough_data_raises(self):
        self.service.get_history = Mock(return_value=make_ohlcv([(101, 99, 100)] * 10))
        with self.assertRaises(ValueError):
            self.service.get_atr_bands("INTC")

    def test_band_and_stop_arithmetic(self):
        self.service.get_history = Mock(return_value=self.quiet)
        result = self.service.get_atr_bands("intc", period=14, band_mult=2.0, stop_mult=3.0)

        self.assertEqual(result["symbol"], "INTC")
        atr = result["atr"]
        self.assertAlmostEqual(atr, 2.0, places=6)
        self.assertAlmostEqual(result["last_close"], 100.0, places=6)
        self.assertAlmostEqual(result["upper_band"], 100 + 2.0 * atr, places=4)
        self.assertAlmostEqual(result["lower_band"], 100 - 2.0 * atr, places=4)
        # Chandelier: highest high of last 22 bars (101) − 3×ATR.
        self.assertAlmostEqual(result["chandelier_stop"], 101 - 3.0 * atr, places=4)
        self.assertAlmostEqual(
            result["stop_distance_pct"],
            (100 - result["chandelier_stop"]) / 100 * 100,
            places=3,
        )
        self.assertEqual(result["atr_trend"], "stable")
        self.assertEqual(len(result["bands_history"]), 20)
        self.assertIn("date", result["bands_history"][0])

    def test_stop_stays_bounded_after_gap(self):
        # 40 quiet bars at 100, a −15% gap-down bar, then 19 quiet bars at 85.
        bars = [(101, 99, 100)] * 40
        bars.append((86, 84, 85))          # gaps down from 100 → TR = |84−100| = 16
        bars += [(86, 84, 85)] * 19
        self.service.get_history = Mock(return_value=make_ohlcv(bars))

        result = self.service.get_atr_bands("INTC", period=14)

        # 19 quiet bars after the gap: Wilder ATR has decayed most of the gap
        # spike back toward the intrabar range, so the stop is not blown out.
        self.assertLess(result["atr"], 6.0)
        self.assertGreater(result["atr"], 2.0)
        self.assertGreater(result["chandelier_stop"], 0)
        self.assertLess(result["stop_distance_pct"], 30)

    def test_lookback_trims_history(self):
        self.service.get_history = Mock(return_value=self.quiet)
        result = self.service.get_atr_bands("INTC", lookback=40)
        self.assertAlmostEqual(result["atr"], 2.0, places=4)


if __name__ == "__main__":
    unittest.main()
