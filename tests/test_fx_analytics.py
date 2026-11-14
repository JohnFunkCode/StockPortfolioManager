"""quantcore.analytics.fx: which daily close rates a trade date (#235 step 2.3)."""
import datetime
import unittest
from decimal import Decimal

from quantcore.analytics import fx

D = Decimal
FRI = datetime.date(2026, 3, 6)
SAT = datetime.date(2026, 3, 7)
SUN = datetime.date(2026, 3, 8)
MON = datetime.date(2026, 3, 9)


class TestCurrency(unittest.TestCase):
    def test_usd_and_blank_need_no_rate(self):
        for currency in ("USD", "usd", " Usd ", "", None, "   "):
            with self.subTest(currency=currency):
                self.assertFalse(fx.needs_rate(currency))
                self.assertEqual(fx.normalize_currency(currency), "USD")

    def test_anything_else_needs_one(self):
        self.assertTrue(fx.needs_rate("eur"))
        self.assertEqual(fx.normalize_currency(" eur "), "EUR")


class TestAsDate(unittest.TestCase):
    def test_reads_dates_datetimes_and_iso_strings(self):
        self.assertEqual(fx.as_date(FRI), FRI)
        self.assertEqual(fx.as_date(datetime.datetime(2026, 3, 6, 15, 30)), FRI)
        self.assertEqual(fx.as_date("2026-03-06"), FRI)
        self.assertEqual(fx.as_date("2026-03-06T15:30:00"), FRI)

    def test_missing_or_malformed_is_none(self):
        for value in (None, "", "not a date", "2026-13-40"):
            with self.subTest(value=value):
                self.assertIsNone(fx.as_date(value))


class TestFetchWindow(unittest.TestCase):
    def test_reaches_a_week_before_the_earliest_date(self):
        self.assertEqual(
            fx.fetch_window([MON, FRI, SUN]),
            (FRI - datetime.timedelta(days=7), MON),
        )

    def test_no_dates_is_an_error(self):
        with self.assertRaises(ValueError):
            fx.fetch_window([])


class TestPriorCloseRate(unittest.TestCase):
    def test_a_trading_day_takes_its_own_close(self):
        self.assertEqual(fx.prior_close_rate({FRI: 1.08, MON: 1.10}, MON), D("1.10000000"))

    def test_a_weekend_takes_the_friday_close(self):
        closes = {FRI: 1.08, MON: 1.10}
        self.assertEqual(fx.prior_close_rate(closes, SAT), D("1.08000000"))
        self.assertEqual(fx.prior_close_rate(closes, SUN), D("1.08000000"))

    def test_never_a_later_close(self):
        self.assertIsNone(fx.prior_close_rate({MON: 1.10}, SUN))

    def test_no_close_within_the_gap_is_no_rate(self):
        old = MON - datetime.timedelta(days=8)
        self.assertIsNone(fx.prior_close_rate({old: 1.08}, MON))
        edge = MON - datetime.timedelta(days=7)
        self.assertEqual(fx.prior_close_rate({edge: 1.08}, MON), D("1.08000000"))
        self.assertIsNone(fx.prior_close_rate({edge: 1.08}, MON, max_gap_days=3))

    def test_an_empty_series_is_no_rate_never_one(self):
        self.assertIsNone(fx.prior_close_rate({}, MON))

    def test_a_bad_close_falls_through_to_the_one_before(self):
        for bad in (None, 0, -1.0, float("nan"), float("inf"), "n/a", 0.000000001):
            with self.subTest(bad=bad):
                self.assertEqual(fx.prior_close_rate({FRI: 1.08, MON: bad}, MON), D("1.08000000"))

    def test_the_rate_is_rounded_to_the_stored_precision(self):
        rate = fx.prior_close_rate({MON: 0.0067123456789}, MON)
        self.assertEqual(rate, D("0.00671235"))
        self.assertEqual(rate.as_tuple().exponent, -8)

    def test_a_float_close_does_not_carry_binary_noise(self):
        self.assertEqual(fx.prior_close_rate({MON: 1.1}, MON), D("1.10000000"))


if __name__ == "__main__":
    unittest.main()
