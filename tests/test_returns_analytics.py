"""Tests for quantcore.analytics.returns (issue #147 Part B1).

Each case here is one of the three defects in portfolio/metrics.py that this
module exists to not repeat, plus the market-cap currency bug. They are written
against hand-built bar lists rather than fixtures so the expected number is
computable by eye from the test body.
"""
import unittest
from datetime import date
from decimal import Decimal

from quantcore.analytics import returns


class TestTrailingReturn(unittest.TestCase):
    def test_close_to_close_over_n_bars(self):
        # 5 bars back from the last: 100 -> 110 is +10%.
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0]
        self.assertEqual(returns.trailing_return(closes, 5), Decimal("10.00"))

    def test_uses_the_close_not_the_open(self):
        """metrics.py reads Open.iloc[-6] against Close.iloc[-1], so its 5-day
        return absorbs the sixth day's overnight gap. Both ends here are closes,
        so the anchor is the *close* six bars ago and nothing else."""
        closes = [50.0, 60.0, 60.0, 60.0, 60.0, 60.0, 75.0]
        # Anchor is closes[-6] == 60, not the older 50.
        self.assertEqual(returns.trailing_return(closes, 5), Decimal("25.00"))

    def test_short_history_is_none_not_a_shorter_window(self):
        self.assertIsNone(returns.trailing_return([100.0, 110.0, 120.0], 5))

    def test_exactly_enough_bars(self):
        self.assertEqual(returns.trailing_return([100.0, 1, 1, 1, 1, 200.0], 5), Decimal("100.00"))

    def test_zero_anchor_is_none_not_a_crash(self):
        self.assertIsNone(returns.trailing_return([0.0, 1, 1, 1, 1, 10.0], 5))

    def test_non_positive_window_is_none(self):
        self.assertIsNone(returns.trailing_return([100.0, 110.0], 0))


class TestYtdReturn(unittest.TestCase):
    def test_anchors_to_the_first_bar_of_the_current_year(self):
        """A frame that crosses a year boundary must ignore every prior-year
        bar — the December closes below are decoys."""
        bars = [
            (date(2025, 12, 29), 500.0),
            (date(2025, 12, 30), 600.0),
            (date(2025, 12, 31), 700.0),
            (date(2026, 1, 2), 100.0),
            (date(2026, 3, 2), 125.0),
        ]
        self.assertEqual(returns.ytd_return(bars, date(2026, 3, 2)), Decimal("25.00"))

    def test_new_years_day_holiday_does_not_shift_the_anchor(self):
        """Jan 1 is always a market holiday and Jan 2 2027 is a Saturday, so the
        year's first bar is Jan 4. bdate_range would count Jan 1 as a business
        day and index off the wrong row; anchoring by date cannot."""
        bars = [
            (date(2026, 12, 31), 900.0),
            (date(2027, 1, 4), 200.0),
            (date(2027, 1, 5), 210.0),
        ]
        self.assertEqual(returns.ytd_return(bars, date(2027, 1, 5)), Decimal("5.00"))

    def test_ignores_bars_after_as_of(self):
        bars = [
            (date(2026, 1, 2), 100.0),
            (date(2026, 2, 2), 110.0),
            (date(2026, 8, 2), 400.0),
        ]
        self.assertEqual(returns.ytd_return(bars, date(2026, 2, 2)), Decimal("10.00"))

    def test_no_bars_this_year_is_none(self):
        bars = [(date(2025, 6, 1), 100.0), (date(2025, 12, 31), 120.0)]
        self.assertIsNone(returns.ytd_return(bars, date(2026, 1, 5)))

    def test_single_bar_is_not_a_return(self):
        self.assertIsNone(returns.ytd_return([(date(2026, 1, 2), 100.0)], date(2026, 1, 2)))

    def test_empty_bars(self):
        self.assertIsNone(returns.ytd_return([], date(2026, 3, 2)))


class TestOneYearReturn(unittest.TestCase):
    def test_two_year_frame_yields_a_one_year_return(self):
        """The metrics.py defect exactly: iloc[0] of a 2y frame. The 2024 bar
        must not be the anchor."""
        bars = [
            (date(2024, 8, 9), 10.0),   # two years back — the wrong anchor
            (date(2025, 8, 11), 100.0),  # first bar on/after as_of - 365d
            (date(2026, 8, 10), 150.0),
        ]
        self.assertEqual(returns.one_year_return(bars, date(2026, 8, 10)), Decimal("50.00"))

    def test_missing_bar_on_the_anniversary_uses_the_next_available(self):
        """as_of - 365d lands on a weekend/holiday with no bar; the anchor is
        the next bar that exists, not a skipped calculation."""
        bars = [
            (date(2025, 7, 1), 80.0),
            (date(2025, 8, 12), 200.0),  # first bar at/after 2026-08-10 minus 365d
            (date(2026, 8, 10), 240.0),
        ]
        self.assertEqual(returns.one_year_return(bars, date(2026, 8, 10)), Decimal("20.00"))

    def test_frame_shorter_than_a_year_is_none(self):
        """A six-month history has no one-year return; reporting its six-month
        return under a '1y' heading is the same mislabelling in reverse."""
        bars = [(date(2026, 3, 2), 100.0), (date(2026, 8, 10), 130.0)]
        self.assertIsNone(returns.one_year_return(bars, date(2026, 8, 10)))

    def test_ignores_bars_after_as_of(self):
        bars = [
            (date(2025, 1, 2), 50.0),
            (date(2025, 8, 12), 100.0),
            (date(2026, 8, 10), 120.0),
            (date(2026, 8, 11), 999.0),
        ]
        self.assertEqual(returns.one_year_return(bars, date(2026, 8, 10)), Decimal("20.00"))

    def test_empty_bars(self):
        self.assertIsNone(returns.one_year_return([], date(2026, 8, 10)))


class TestNormalizeMarketCap(unittest.TestCase):
    def test_usd_passes_through_comparable(self):
        out = returns.normalize_market_cap(3_000_000_000_000, "USD")
        self.assertEqual(out["market_cap"], 3_000_000_000_000)
        self.assertEqual(out["market_cap_currency"], "USD")
        self.assertEqual(out["market_cap_usd"], 3_000_000_000_000)

    def test_foreign_currency_without_a_rate_is_native_only(self):
        """SK hynix: ₩1,456T rendered as '1456.62T' in a dollar column made it
        look ~60x larger than Nvidia. Native value is kept and labelled KRW; the
        comparable field stays None rather than lying."""
        out = returns.normalize_market_cap(1_456_620_000_000_000, "KRW")
        self.assertEqual(out["market_cap"], 1_456_620_000_000_000)
        self.assertEqual(out["market_cap_currency"], "KRW")
        self.assertIsNone(out["market_cap_usd"])

    def test_foreign_currency_with_a_rate_converts(self):
        out = returns.normalize_market_cap(1_000_000_000_000, "krw", fx_rate=0.00072)
        self.assertEqual(out["market_cap_currency"], "KRW")
        self.assertAlmostEqual(out["market_cap_usd"], 720_000_000.0)

    def test_missing_currency_defaults_to_usd(self):
        out = returns.normalize_market_cap(5.0, None)
        self.assertEqual(out["market_cap_currency"], "USD")
        self.assertEqual(out["market_cap_usd"], 5.0)

    def test_missing_value(self):
        out = returns.normalize_market_cap(None, "EUR")
        self.assertIsNone(out["market_cap"])
        self.assertIsNone(out["market_cap_usd"])
        self.assertEqual(out["market_cap_currency"], "EUR")

    def test_unparseable_value(self):
        out = returns.normalize_market_cap("n/a", "USD")
        self.assertIsNone(out["market_cap"])
        self.assertIsNone(out["market_cap_usd"])


if __name__ == "__main__":
    unittest.main()
