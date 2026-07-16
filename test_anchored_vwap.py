"""Tests for anchored-VWAP analytics (anchored_vwap / find_swings),
PricesService.get_anchored_vwap anchor resolution, and the get_higher_lows
regression after its swing-scan was extracted into find_swings (issue #93,
Phase 2). No network, no DB — mocked collaborators throughout.
"""
import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd

from quantcore.analytics.indicators import anchored_vwap, find_swings
from quantcore.services.prices import PricesService


def make_service():
    return PricesService(
        ohlcv_repository=Mock(),
        yfinance_gateway=Mock(),
        options_repository=Mock(),
        sentiment_repository=Mock(),
    )


def make_ohlcv(bars, start="2025-01-02"):
    """bars = list of (high, low, close, volume) tuples on business days."""
    idx = pd.bdate_range(start, periods=len(bars))
    return pd.DataFrame(
        {
            "Open":   [c for _, _, c, _ in bars],
            "High":   [h for h, _, _, _ in bars],
            "Low":    [l for _, l, _, _ in bars],
            "Close":  [c for _, _, c, _ in bars],
            "Volume": [v for _, _, _, v in bars],
        },
        index=idx,
    )


class TestAnchoredVwapMath(unittest.TestCase):
    def setUp(self):
        # Typical prices: 11, 12, 13 with volumes 100, 200, 300.
        self.df = make_ohlcv([(12, 10, 11, 100), (13, 11, 12, 200), (14, 12, 13, 300)])

    def test_cumulative_from_first_bar(self):
        # (11*100 + 12*200 + 13*300) / 600 = 7400/600
        self.assertAlmostEqual(anchored_vwap(self.df, 0), 7400 / 600, places=10)

    def test_cumulative_from_middle_bar(self):
        # (12*200 + 13*300) / 500 = 12.6
        self.assertAlmostEqual(anchored_vwap(self.df, 1), 12.6, places=10)

    def test_zero_volume_returns_none(self):
        df = make_ohlcv([(12, 10, 11, 0), (13, 11, 12, 0)])
        self.assertIsNone(anchored_vwap(df, 0))


class TestFindSwings(unittest.TestCase):
    def test_exact_pivot_indices(self):
        lows  = pd.Series([5.0, 4, 3, 2, 3, 4, 5, 6, 7])
        highs = pd.Series([5.0, 6, 7, 8, 7, 6, 5, 4, 3])
        swings = find_swings(highs, lows, swing_bars=2)
        self.assertEqual(swings["lows"], [3])
        self.assertEqual(swings["highs"], [3])

    def test_last_bars_never_confirmed(self):
        # Minimum at the very end must not be reported — right side incomplete.
        lows  = pd.Series([5.0, 5, 5, 5, 5, 5, 1])
        highs = pd.Series([5.0] * 7)
        swings = find_swings(highs, lows, swing_bars=2)
        self.assertNotIn(6, swings["lows"])

    def test_tie_semantics_match_get_higher_lows(self):
        # Flat series: `<=` / `>=` semantics accept ties, so every interior
        # scanned bar is both a swing low and a swing high.
        flat = pd.Series([5.0] * 8)
        swings = find_swings(flat, flat, swing_bars=2)
        self.assertEqual(swings["lows"], [2, 3, 4, 5])
        self.assertEqual(swings["highs"], [2, 3, 4, 5])


class TestGetAnchoredVwap(unittest.TestCase):
    """300 flat bars (H=101, L=99, C=100) with a 52w low trough at bar 200, a
    52w high spike at bar 250 (its AVWAP window is clean of the trough; the
    trough's window contains the spike but the deeper trough TP dominates),
    and a stubbed earnings date at bar 280."""

    def setUp(self):
        bars = [(101.0, 99.0, 100.0, 1_000)] * 300
        bars[200] = (101.0, 60.0, 100.0, 1_000)   # 52w low + swing low
        bars[250] = (120.0, 99.0, 100.0, 1_000)   # 52w high + swing high
        self.hist = make_ohlcv(bars)
        self.service = make_service()
        self.service.get_history = Mock(return_value=self.hist)
        self.service._yf.earnings_dates.return_value = pd.DataFrame(
            {"EPS Estimate": [1.0]}, index=pd.DatetimeIndex([self.hist.index[280]])
        )

    def test_anchor_resolution_and_dedupe(self):
        result = self.service.get_anchored_vwap("intc")

        self.assertEqual(result["symbol"], "INTC")
        types = {a["type"] for a in result["anchors"]}
        self.assertEqual(types, {"earnings", "52w_high", "52w_low", "swing_high"})
        # swing_low resolves to the same flat-region bar as swing_high and is
        # deduped away by priority (swing ties resolve in collection order).
        self.assertEqual(result["anchor_count"], 4)

    def test_avwap_values_and_nearest_levels(self):
        result = self.service.get_anchored_vwap("INTC")
        by_type = {a["type"]: a for a in result["anchors"]}

        # From bar 250: spike TP 319/3 once, then 49 flat bars of TP 100.
        expected_high_anchor = (319 / 3 + 49 * 100) / 50
        self.assertAlmostEqual(by_type["52w_high"]["avwap"], round(expected_high_anchor, 4), places=4)
        self.assertEqual(by_type["52w_high"]["position"], "resistance")

        # From bar 200: trough TP 261/3, spike TP 319/3, and 98 flat bars of
        # TP 100 — the trough's 13-point TP deficit outweighs the spike's
        # 6.33-point excess, keeping this anchor below price.
        expected_low_anchor = (261 / 3 + 319 / 3 + 98 * 100) / 100
        self.assertAlmostEqual(by_type["52w_low"]["avwap"], round(expected_low_anchor, 4), places=4)
        self.assertEqual(by_type["52w_low"]["position"], "support")

        self.assertEqual(result["nearest_resistance"]["type"], "52w_high")
        # Earnings anchor sits in the flat region → AVWAP 100.0 == price → support.
        self.assertEqual(result["nearest_support"]["avwap"], 100.0)

    def test_user_anchor_outranks_earnings_in_dedupe(self):
        anchor = self.hist.index[281].strftime("%Y-%m-%d")
        result = self.service.get_anchored_vwap("INTC", anchor_date=anchor)
        types = {a["type"] for a in result["anchors"]}
        self.assertIn("user", types)
        self.assertNotIn("earnings", types)  # within 3 trading days of the user anchor

    def test_earnings_failure_degrades_gracefully(self):
        self.service._yf.earnings_dates.side_effect = RuntimeError("yahoo down")
        result = self.service.get_anchored_vwap("INTC")
        types = {a["type"] for a in result["anchors"]}
        self.assertNotIn("earnings", types)
        self.assertGreater(result["anchor_count"], 0)

    def test_invalid_anchor_date_raises(self):
        with self.assertRaises(ValueError):
            self.service.get_anchored_vwap("INTC", anchor_date="07/15/2026")

    def test_out_of_range_anchor_date_raises(self):
        with self.assertRaises(ValueError):
            self.service.get_anchored_vwap("INTC", anchor_date="2020-01-01")


def original_swing_low_scan(low: pd.Series, swing_bars: int) -> list:
    """Verbatim copy of the pre-refactor scan from get_higher_lows — the
    regression oracle for the find_swings extraction."""
    swing_low_indices = []
    scan_end = len(low) - swing_bars
    for i in range(swing_bars, scan_end):
        l = float(low.iloc[i])
        left_ok  = all(l <= float(low.iloc[i - k]) for k in range(1, swing_bars + 1))
        right_ok = all(l <= float(low.iloc[i + k]) for k in range(1, swing_bars + 1))
        if left_ok and right_ok:
            swing_low_indices.append(i)
    return swing_low_indices


class TestGetHigherLowsRegression(unittest.TestCase):
    def test_swing_scan_matches_original_on_random_series(self):
        rng = np.random.default_rng(42)
        lows = pd.Series(100 + rng.normal(0, 2, 500).cumsum())
        highs = lows + rng.uniform(0.5, 3.0, 500)
        for swing_bars in (2, 3, 5):
            self.assertEqual(
                find_swings(highs, lows, swing_bars)["lows"],
                original_swing_low_scan(lows, swing_bars),
                f"swing_bars={swing_bars}",
            )

    def test_get_higher_lows_output_on_fixed_series(self):
        # Rising dips at bars 10/20/30/40 (lows 90→91→92→93) on a gently
        # increasing base (no ties): exactly 4 swing lows, 3 consecutive
        # higher, min rise (93−92)/92 ≈ 1.087% → strong; the base slopes down
        # 5.2% into the first dip → downtrend.
        bars = []
        for i in range(60):
            base_low = 95 + i * 0.01
            if i in (10, 20, 30, 40):
                dip = {10: 90.0, 20: 91.0, 30: 92.0, 40: 93.0}[i]
                bars.append((dip + 5, dip, dip + 2, 1_000))
            else:
                bars.append((base_low + 5, base_low, base_low + 2, 1_000))
        service = make_service()
        service.get_history = Mock(return_value=make_ohlcv(bars))

        result = service.get_higher_lows("INTC", swing_bars=3, lookback_swings=6, interval="1d")

        self.assertEqual(result["swing_lows_found"], 4)
        self.assertTrue(result["higher_low_pattern"])
        self.assertEqual(result["consecutive_higher_lows"], 3)
        self.assertEqual(result["pattern_strength"], "strong")
        self.assertEqual(result["trend_before_lows"], "downtrend")
        self.assertEqual([s["low"] for s in result["swing_lows"]], [90.0, 91.0, 92.0, 93.0])
        self.assertAlmostEqual(result["min_rise_between_lows_pct"], round(1 / 92 * 100, 3), places=3)


if __name__ == "__main__":
    unittest.main()
