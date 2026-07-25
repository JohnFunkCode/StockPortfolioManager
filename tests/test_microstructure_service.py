"""Unit tests for MicrostructureService (short interest, dark-pool proxy,
bid/ask spread). Coverage uplift (July 2026): gateway/prices are Mocks and
frames are engineered so each classification branch is provoked analytically.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from quantcore.services.microstructure import (
    MicrostructureService,
    _safe_float,
    _safe_int,
)


def frame(closes, volumes=None, opens=None, highs=None, lows=None):
    closes = [float(c) for c in closes]
    n = len(closes)
    idx = pd.bdate_range(end="2026-06-30", periods=n)
    opens = opens or [c for c in closes]
    highs = highs or [c + 0.5 for c in closes]
    lows = lows or [c - 0.5 for c in closes]
    volumes = volumes or [1_000_000.0] * n
    return pd.DataFrame(
        {
            "Open": [float(v) for v in opens],
            "High": [float(v) for v in highs],
            "Low": [float(v) for v in lows],
            "Close": closes,
            "Volume": [float(v) for v in volumes],
        },
        index=idx,
    )


class TestSafeCasts(unittest.TestCase):
    def test_safe_float(self):
        self.assertEqual(_safe_float("1.5"), 1.5)
        self.assertEqual(_safe_float(None), 0.0)
        self.assertEqual(_safe_float(float("nan")), 0.0)
        self.assertEqual(_safe_float("x", default=7.0), 7.0)

    def test_safe_int(self):
        self.assertEqual(_safe_int(3.9), 3)
        self.assertEqual(_safe_int(None), 0)
        self.assertEqual(_safe_int(float("nan"), default=4), 4)


class MicrostructureTestBase(unittest.TestCase):
    def setUp(self):
        self.yf = Mock()
        self.prices = Mock()
        self.service = MicrostructureService(
            ohlcv_repository=Mock(), yfinance_gateway=self.yf, prices=self.prices
        )


class TestShortInterest(MicrostructureTestBase):
    def test_high_squeeze_and_tight_borrow(self):
        self.yf.ticker_info.return_value = {
            "sharesShort": 30_000_000,
            "sharesOutstanding": 200_000_000,
            "floatShares": 100_000_000,
            "averageVolume": 5_000_000,
            "shortRatio": 6.0,
            "shortPercentOfFloat": 0.30,
            "dateShortInterest": 1_780_000_000,
        }
        out = self.service.get_short_interest("gme")
        self.assertEqual(out["symbol"], "GME")
        self.assertEqual(out["short_float_pct"], 30.0)
        self.assertEqual(out["squeeze_potential"], "HIGH")
        self.assertIn("TIGHT", out["borrow_note"])
        self.assertRegex(out["short_interest_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_missing_yahoo_fields_computed_manually(self):
        self.yf.ticker_info.return_value = {
            "sharesShort": 10_000_000,
            "floatShares": 100_000_000,
            "averageVolume": 2_500_000,
            # no shortRatio / shortPercentOfFloat / dateShortInterest
        }
        out = self.service.get_short_interest("INTC")
        self.assertEqual(out["short_float_pct"], 10.0)          # 10M / 100M
        self.assertEqual(out["short_ratio_days"], 4.0)          # 10M / 2.5M
        self.assertEqual(out["short_interest_date"], "unknown")
        self.assertEqual(out["squeeze_potential"], "MEDIUM")    # ratio >= 3

    def test_low_short_interest(self):
        self.yf.ticker_info.return_value = {
            "sharesShort": 1_000_000,
            "floatShares": 500_000_000,
            "averageVolume": 10_000_000,
            "shortRatio": 0.4,
        }
        out = self.service.get_short_interest("WMT")
        self.assertEqual(out["squeeze_potential"], "LOW")
        self.assertIn("available", out["borrow_note"])

    def test_timeout_degrades_to_error_payload(self):
        self.yf.ticker_info.side_effect = TimeoutError("yahoo hung")
        out = self.service.get_short_interest("INTC")
        self.assertEqual(out["symbol"], "INTC")
        self.assertIn("yahoo hung", out["error"])
        self.assertIn("Retry", out["note"])


class TestDarkPool(MicrostructureTestBase):
    def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            self.service.get_dark_pool("INTC", interval="5m")

    def test_thin_history_rejected(self):
        self.prices.get_history.return_value = frame([100] * 10)
        with self.assertRaises(ValueError):
            self.service.get_dark_pool("INTC")

    def test_quiet_tape_reports_none(self):
        self.prices.get_history.return_value = frame([100] * 60)
        out = self.service.get_dark_pool("INTC")
        self.assertEqual(out["net_signal"], "none")
        self.assertEqual(out["absorption_count"], 0)

    def test_down_day_absorption_reads_accumulation(self):
        n = 60
        closes = [100.0] * (n - 1) + [99.9]
        opens = [100.0] * n
        # Last bar: tiny range (0.1 vs 1.0 avg), 3x volume, closes red.
        highs = [100.5] * (n - 1) + [100.02]
        lows = [99.5] * (n - 1) + [99.88]
        volumes = [1_000_000.0] * (n - 1) + [3_000_000.0]
        self.prices.get_history.return_value = frame(
            closes, volumes=volumes, opens=opens, highs=highs, lows=lows
        )
        out = self.service.get_dark_pool("INTC")
        self.assertEqual(out["net_signal"], "accumulation")
        self.assertEqual(out["absorption_count"], 1)
        event = out["absorption_events"][0]
        self.assertEqual(event["direction"], "down")
        self.assertIn("accumulation", event["interpretation"])
        self.assertIn("bullish", out["interpretation"])

    def test_indecisive_high_volume_bar_reads_two_sided(self):
        n = 60
        closes = [100.0] * (n - 1) + [100.0]   # closes exactly at the midpoint
        opens = [100.0] * n
        highs = [100.5] * n                     # normal range keeps it out of absorption
        lows = [99.5] * n
        volumes = [1_000_000.0] * (n - 1) + [3_000_000.0]
        self.prices.get_history.return_value = frame(
            closes, volumes=volumes, opens=opens, highs=highs, lows=lows
        )
        out = self.service.get_dark_pool("INTC")
        self.assertEqual(out["two_sided_count"], 1)
        self.assertEqual(out["net_signal"], "mixed")


class TestBidAskSpread(MicrostructureTestBase):
    def arm(self, bid=99.0, ask=101.0, price=100.0, expirations=("2026-08-21",),
            hist=None):
        self.yf.fast_info.return_value = SimpleNamespace(
            bid=bid, ask=ask, last_price=price
        )
        self.yf.expirations.return_value = tuple(expirations)
        chain_df = pd.DataFrame({
            "strike": [95.0, 100.0, 105.0],
            "bid": [4.0, 2.0, 0.9],
            "ask": [4.4, 2.2, 1.1],
        })
        self.yf.option_chain.return_value = SimpleNamespace(
            calls=chain_df, puts=chain_df
        )
        self.prices.get_history.return_value = (
            hist if hist is not None else frame([100.0] * 60)
        )

    def test_equity_and_options_spreads_measured(self):
        self.arm()
        out = self.service.get_bid_ask_spread("intc")
        self.assertEqual(out["symbol"], "INTC")
        self.assertEqual(out["equity_spread"], 2.0)
        self.assertEqual(out["equity_spread_pct"], 2.0)   # 2 / 100 mid
        self.assertIsNotNone(out["options_spread_pct"])
        self.assertEqual(out["spread_vs_norm"], "normal")
        self.assertEqual(len(out["spread_history"]), 10)

    def test_wide_last_bar_reads_widening_and_bottom_forming(self):
        n = 60
        closes = [100.0] * n
        highs = [100.5] * (n - 1) + [101.5]
        lows = [99.5] * (n - 1) + [98.5]      # 3% range vs ~1% norm
        self.arm(hist=frame(closes, highs=highs, lows=lows))
        out = self.service.get_bid_ask_spread("INTC")
        self.assertEqual(out["spread_vs_norm"], "widening")
        self.assertEqual(out["bottom_signal"], "forming")

    def test_narrow_last_bar_reads_narrowing_and_bottom_strong(self):
        n = 60
        closes = [100.0] * n
        highs = [100.5] * (n - 1) + [100.05]
        lows = [99.5] * (n - 1) + [99.95]
        self.arm(hist=frame(closes, highs=highs, lows=lows))
        out = self.service.get_bid_ask_spread("INTC")
        self.assertEqual(out["spread_vs_norm"], "narrowing")
        self.assertEqual(out["bottom_signal"], "strong")
        self.assertIn("bounce", out["bottom_note"].lower())

    def test_no_expirations_leaves_options_spread_null(self):
        self.arm(expirations=())
        out = self.service.get_bid_ask_spread("INTC")
        self.assertIsNone(out["options_spread_pct"])

    def test_crossed_quote_leaves_equity_spread_null(self):
        self.arm(bid=101.0, ask=100.0)
        out = self.service.get_bid_ask_spread("INTC")
        self.assertIsNone(out["equity_spread"])


if __name__ == "__main__":
    unittest.main()
