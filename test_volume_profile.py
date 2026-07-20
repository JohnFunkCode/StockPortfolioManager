"""Tests for volume-profile analytics (build_volume_profile / find_volume_nodes)
and PricesService.get_volume_profile (issue #93, Phase 3). No network, no DB —
mocked collaborators throughout.
"""
import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd

from quantcore.analytics.volume_profile import build_volume_profile, find_volume_nodes
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


class TestBuildVolumeProfile(unittest.TestCase):
    """3 bars, 4 bins over [10, 18] (edges 10/12/14/16/18) — exact overlap math:
    bar1 (10–14, v=400) splits 200/200 into bins 0–1; bar2 (14–18, v=800)
    splits 400/400 into bins 2–3; bar3 is degenerate (13–13, v=500) → all
    into bin 1. Bin volumes: [200, 700, 400, 400]."""

    def setUp(self):
        self.profile = build_volume_profile(
            highs=[14.0, 18.0, 13.0],
            lows=[10.0, 14.0, 13.0],
            volumes=[400, 800, 500],
            bins=4,
        )

    def test_exact_bin_volumes_and_poc(self):
        np.testing.assert_allclose(self.profile["bin_volumes"], [200, 700, 400, 400])
        self.assertAlmostEqual(self.profile["total_volume"], 1700.0)
        self.assertAlmostEqual(self.profile["poc"], 13.0)  # center of bin 1
        self.assertAlmostEqual(self.profile["poc_volume"], 700.0)

    def test_value_area_annexes_higher_volume_neighbors(self):
        # Target 0.7·1700 = 1190; POC bin holds 700 → annex bin 2 (400 > 200)
        # → 1100, then bin 3 (400 > 200) → 1500 ≥ target.
        self.assertAlmostEqual(self.profile["value_area_low"], 12.0)
        self.assertAlmostEqual(self.profile["value_area_high"], 18.0)
        self.assertAlmostEqual(self.profile["value_area_volume_pct"], 1500 / 1700)

    def test_all_bars_at_one_price_collapses_to_spike(self):
        profile = build_volume_profile(
            highs=[50.0, 50.0], lows=[50.0, 50.0], volumes=[300, 700], bins=10
        )
        self.assertAlmostEqual(profile["poc"], 50.0)
        self.assertAlmostEqual(profile["total_volume"], 1000.0)
        self.assertAlmostEqual(profile["value_area_low"], 50.0)
        self.assertAlmostEqual(profile["value_area_high"], 50.0)

    def test_zero_volume_raises(self):
        with self.assertRaises(ValueError):
            build_volume_profile(highs=[11.0], lows=[10.0], volumes=[0])


class TestFindVolumeNodes(unittest.TestCase):
    def test_hvn_lvn_detection_with_run_merging(self):
        # Smoothed (3-bin, min_periods=1): [500, 500, 336.67, 173.33, 10,
        # 173.33, 336.67, 500, 500]; median 336.67 → HVN ≥ 420.83, LVN ≤ 202.
        # HVN runs [0,1] and [7,8] collapse to one node each; LVN is bin 4.
        centers = np.arange(1.0, 10.0)
        volumes = [500, 500, 500, 10, 10, 10, 500, 500, 500]
        nodes = find_volume_nodes(centers, volumes)
        self.assertEqual([n["price"] for n in nodes["hvns"]], [1.0, 8.0])
        self.assertEqual([n["price"] for n in nodes["lvns"]], [5.0])
        self.assertEqual(nodes["lvns"][0]["volume"], 10.0)

    def test_too_few_bins_returns_empty(self):
        nodes = find_volume_nodes([1.0, 2.0], [100, 200])
        self.assertEqual(nodes, {"hvns": [], "lvns": []})

    def test_all_zero_volume_returns_empty(self):
        nodes = find_volume_nodes([1.0, 2.0, 3.0], [0, 0, 0])
        self.assertEqual(nodes, {"hvns": [], "lvns": []})


class TestGetVolumeProfile(unittest.TestCase):
    """24 bars in three clean price tiers over [100, 110] with bins=5 (edges
    100/102/104/106/108/110): 10 bars at 100–102 (v=1000 → bin0 = 10000),
    4 at 104–106 (v=500 → bin2 = 2000), 10 at 108–110 (v=800 → bin4 = 8000).
    Smoothed: [5000, 4000, 666.67, 3333.33, 4000], median 4000 →
    HVN = bin0 (center 101), LVN = bin2 (center 105). Last close 109."""

    def setUp(self):
        bars = (
            [(102.0, 100.0, 101.0, 1_000)] * 10
            + [(106.0, 104.0, 105.0, 500)] * 4
            + [(110.0, 108.0, 109.0, 800)] * 10
        )
        self.service = make_service()
        self.service.get_history = Mock(return_value=make_ohlcv(bars))

    def test_levels_and_nearest_node_selection(self):
        result = self.service.get_volume_profile("nvda", days=365, bins=5)

        self.service.get_history.assert_called_once_with("NVDA", "1d", 365)
        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["bars_used"], 24)
        self.assertEqual(result["poc"], 101.0)
        self.assertEqual(result["poc_volume"], 10000)
        self.assertEqual(result["total_volume"], 20000)
        self.assertEqual(result["value_area"], {"low": 100.0, "high": 110.0})
        self.assertTrue(result["in_value_area"])

        self.assertEqual(result["hvns"], [{"price": 101.0, "volume": 10000}])
        self.assertEqual(result["lvns"], [{"price": 105.0, "volume": 2000}])
        # Close is 109: the only HVN (101) and LVN (105) both sit below it.
        self.assertEqual(result["nearest_hvn_below"]["price"], 101.0)
        self.assertIsNone(result["nearest_hvn_above"])
        self.assertEqual(result["nearest_lvn_below"]["price"], 105.0)
        self.assertIsNone(result["nearest_lvn_above"])

        self.assertEqual(len(result["profile"]), 5)
        self.assertEqual(result["profile"][0], {"price": 101.0, "volume": 10000, "pct": 50.0})
        self.assertIsNone(result["note"])

    def test_intraday_beyond_cap_gets_note_not_error(self):
        result = self.service.get_volume_profile("NVDA", days=365, interval="1h", bins=5)
        self.assertIn("capped", result["note"])
        self.service.get_history.assert_called_once_with("NVDA", "1h", 365)

    def test_invalid_params_raise(self):
        with self.assertRaises(ValueError):
            self.service.get_volume_profile("NVDA", interval="5m")
        with self.assertRaises(ValueError):
            self.service.get_volume_profile("NVDA", value_area_pct=1.5)
        with self.assertRaises(ValueError):
            self.service.get_volume_profile("NVDA", bins=2)

    def test_not_enough_bars_raises(self):
        self.service.get_history = Mock(
            return_value=make_ohlcv([(102.0, 100.0, 101.0, 1_000)] * 5)
        )
        with self.assertRaises(ValueError):
            self.service.get_volume_profile("NVDA")


if __name__ == "__main__":
    unittest.main()
