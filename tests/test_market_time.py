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
    is_trading_day,
    latest_completed_session,
    market_date,
    nyse_holidays,
    period_to_days,
    trading_days_after,
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


class TestMarketDate(unittest.TestCase):
    """The date a holding period is measured against.

    On Cloud Run ``date.today()`` is the host's UTC date, so from ~5pm ET until
    midnight ET it is already tomorrow and every calendar-day count anchored to
    it comes out one day long — an 8-day hold reporting 9, with any per-day
    rate deflated by the same ratio.
    """

    def test_evening_et_is_still_today_not_tomorrow_utc(self):
        # 23:35 ET on the 14th is 03:35 UTC on the 15th.
        evening = et(2026, 8, 14, 23, 35)
        self.assertEqual(evening.astimezone(pytz.utc).date(), datetime.date(2026, 8, 15))
        self.assertEqual(market_date(now=evening), datetime.date(2026, 8, 14))

    def test_morning_et_agrees_with_utc(self):
        self.assertEqual(market_date(now=et(2026, 8, 14, 9, 45)), datetime.date(2026, 8, 14))

    def test_accepts_an_aware_datetime_in_any_zone(self):
        utc_moment = pytz.utc.localize(datetime.datetime(2026, 8, 15, 3, 35))
        self.assertEqual(market_date(now=utc_moment), datetime.date(2026, 8, 14))

    def test_does_not_roll_back_over_a_weekend(self):
        # Deliberately not latest_completed_session: a holding period keeps
        # accruing on a Sunday, so rolling back to Friday would undercount it.
        sunday = et(2026, 8, 16, 12, 0)
        self.assertEqual(market_date(now=sunday), datetime.date(2026, 8, 16))
        self.assertEqual(latest_completed_session(now=sunday), datetime.date(2026, 8, 14))

    def test_does_not_roll_back_overnight(self):
        premarket = et(2026, 8, 14, 6, 0)  # Friday, before the open
        self.assertEqual(market_date(now=premarket), datetime.date(2026, 8, 14))
        self.assertEqual(latest_completed_session(now=premarket), datetime.date(2026, 8, 13))


class TestPeriodToDays(unittest.TestCase):
    def test_known_periods(self):
        self.assertEqual(period_to_days("1y"), 365)
        self.assertEqual(period_to_days("2y"), 730)
        self.assertEqual(period_to_days("6mo"), 182)
        self.assertEqual(period_to_days("5d"), 5)

    def test_case_insensitive_and_default(self):
        self.assertEqual(period_to_days("1Y"), 365)
        self.assertEqual(period_to_days("bogus"), 182)


class NyseCalendarTest(unittest.TestCase):
    D = datetime.date

    def test_known_2026_holidays_are_closed(self):
        for d in [self.D(2026, 1, 1), self.D(2026, 1, 19), self.D(2026, 2, 16),
                  self.D(2026, 4, 3),    # Good Friday
                  self.D(2026, 5, 25), self.D(2026, 6, 19),
                  self.D(2026, 7, 3),    # July 4 is a Saturday -> Friday
                  self.D(2026, 9, 7), self.D(2026, 11, 26), self.D(2026, 12, 25)]:
            self.assertFalse(is_trading_day(d), d)

    def test_ordinary_weekday_is_open_and_weekend_is_closed(self):
        self.assertTrue(is_trading_day(self.D(2026, 7, 2)))
        self.assertFalse(is_trading_day(self.D(2026, 7, 4)))
        self.assertFalse(is_trading_day(self.D(2026, 7, 5)))

    def test_new_years_saturday_is_not_observed_on_prior_friday(self):
        # Jan 1 2022 was a Saturday: NYSE stayed open Friday Dec 31 2021.
        self.assertNotIn(self.D(2021, 12, 31), nyse_holidays(2021))
        self.assertTrue(is_trading_day(self.D(2021, 12, 31)))

    def test_juneteenth_only_from_2022(self):
        self.assertNotIn(self.D(2021, 6, 18), nyse_holidays(2021))
        self.assertIn(self.D(2022, 6, 20), nyse_holidays(2022))

    def test_good_friday_follows_easter(self):
        self.assertIn(self.D(2025, 4, 18), nyse_holidays(2025))
        self.assertIn(self.D(2027, 3, 26), nyse_holidays(2027))


class TestTradingDaysAfter(unittest.TestCase):
    def test_counts_sessions_after_start_through_end(self):
        fri = datetime.date(2026, 10, 2)
        self.assertEqual(trading_days_after(fri, fri), 0)
        self.assertEqual(trading_days_after(fri, datetime.date(2026, 10, 4)), 0)  # weekend
        self.assertEqual(trading_days_after(fri, datetime.date(2026, 10, 5)), 1)
        self.assertEqual(trading_days_after(fri, datetime.date(2026, 10, 6)), 2)

    def test_holidays_do_not_count_and_reversed_is_zero(self):
        # Thanksgiving 2026 is Thursday 11-26.
        self.assertEqual(
            trading_days_after(datetime.date(2026, 11, 25), datetime.date(2026, 11, 26)), 0)
        self.assertEqual(
            trading_days_after(datetime.date(2026, 10, 6), datetime.date(2026, 10, 2)), 0)


if __name__ == "__main__":
    unittest.main()
