"""Tests for quantcore/analytics/market_time.py — pure market-calendar helpers

shared by the OHLCV persistence layer (bar classification) and PricesService
(staleness policy). Pure functions, injectable clock, no I/O.
"""
import datetime
import unittest

import pytz

from quantcore.analytics.market_time import (
    ET,
    is_market_open,
    latest_completed_session,
    period_to_days,
)


def et(y, m, d, hh, mm):
    return pytz.timezone("America/New_York").localize(
        datetime.datetime(y, m, d, hh, mm)
    )


class TestIsMarketOpen(unittest.TestCase):
    def test_weekday_regular_hours(self):
        self.assertTrue(is_market_open(now=et(2026, 7, 15, 10, 0)))   # Wed 10:00
        self.assertTrue(is_market_open(now=et(2026, 7, 15, 9, 30)))   # open bell

    def test_weekday_outside_hours(self):
        self.assertFalse(is_market_open(now=et(2026, 7, 15, 9, 0)))   # pre-market
        self.assertFalse(is_market_open(now=et(2026, 7, 15, 16, 0)))  # close bell
        self.assertFalse(is_market_open(now=et(2026, 7, 15, 20, 0)))  # evening

    def test_weekend_closed(self):
        self.assertFalse(is_market_open(now=et(2026, 7, 11, 11, 0)))  # Saturday


class TestLatestCompletedSession(unittest.TestCase):
    def test_midweek_after_open_is_today(self):
        self.assertEqual(
            latest_completed_session(now=et(2026, 7, 15, 10, 0)),
            datetime.date(2026, 7, 15),
        )

    def test_midweek_before_open_is_previous_day(self):
        self.assertEqual(
            latest_completed_session(now=et(2026, 7, 15, 8, 0)),
            datetime.date(2026, 7, 14),
        )

    def test_monday_premarket_rolls_back_to_friday(self):
        self.assertEqual(
            latest_completed_session(now=et(2026, 7, 13, 8, 0)),  # Mon 8am
            datetime.date(2026, 7, 10),                            # Friday
        )

    def test_weekend_rolls_back_to_friday(self):
        self.assertEqual(
            latest_completed_session(now=et(2026, 7, 12, 12, 0)),  # Sunday
            datetime.date(2026, 7, 10),
        )


class TestPeriodToDays(unittest.TestCase):
    def test_known_periods(self):
        self.assertEqual(period_to_days("1y"), 365)
        self.assertEqual(period_to_days("2y"), 730)
        self.assertEqual(period_to_days("6mo"), 182)
        self.assertEqual(period_to_days("5d"), 5)

    def test_case_insensitive_and_default(self):
        self.assertEqual(period_to_days("1Y"), 365)
        self.assertEqual(period_to_days("bogus"), 182)


if __name__ == "__main__":
    unittest.main()
