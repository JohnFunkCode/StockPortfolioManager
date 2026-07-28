"""Tests for quantcore/analytics/portfolio_math.py (issue #126 PR 4 Step 4.1).
Pure Decimal math, no I/O — no mocks needed.
"""
import unittest
from datetime import date
from decimal import Decimal

from quantcore.analytics.portfolio_math import (
    dollars_per_day,
    days_held,
    gain_loss,
    gain_loss_pct,
    period_return,
    summary_totals,
)


class TestGainLoss(unittest.TestCase):
    def test_gain(self):
        self.assertEqual(
            gain_loss(Decimal("120.00"), Decimal("100.00"), Decimal("10")),
            Decimal("200.00"),
        )

    def test_loss(self):
        self.assertEqual(
            gain_loss(Decimal("90.00"), Decimal("100.00"), Decimal("10")),
            Decimal("-100.00"),
        )

    def test_fractional_share(self):
        # (100 - 133.33) * 0.0625 = -2.083125 -> quantized to cents
        self.assertEqual(
            gain_loss(Decimal("100.00"), Decimal("133.33"), Decimal("0.0625")),
            Decimal("-2.08"),
        )


class TestGainLossPct(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(
            gain_loss_pct(Decimal("120.00"), Decimal("100.00"), Decimal("10")),
            Decimal("20.00"),
        )

    def test_negative_return(self):
        self.assertEqual(
            gain_loss_pct(Decimal("50.00"), Decimal("100.00"), Decimal("10")),
            Decimal("-50.00"),
        )


class TestDaysHeld(unittest.TestCase):
    def test_calendar_days(self):
        self.assertEqual(days_held(date(2026, 1, 1), date(2026, 1, 31)), 30)

    def test_same_day_is_zero(self):
        self.assertEqual(days_held(date(2026, 1, 1), date(2026, 1, 1)), 0)


class TestDollarsPerDay(unittest.TestCase):
    def test_zero_days_returns_none(self):
        self.assertIsNone(dollars_per_day(Decimal("100.00"), 0))

    def test_nonzero_days(self):
        self.assertEqual(dollars_per_day(Decimal("100.00"), 10), Decimal("10.00"))

    def test_negative_gain_loss(self):
        self.assertEqual(dollars_per_day(Decimal("-100.00"), 10), Decimal("-10.00"))


class TestPeriodReturn(unittest.TestCase):
    def test_positive_return(self):
        self.assertEqual(
            period_return(Decimal("110.00"), Decimal("100.00")), Decimal("10.00")
        )

    def test_negative_return(self):
        self.assertEqual(
            period_return(Decimal("90.00"), Decimal("100.00")), Decimal("-10.00")
        )


class TestSummaryTotals(unittest.TestCase):
    def test_empty_lots(self):
        totals = summary_totals([], as_of=date(2026, 1, 1))
        self.assertEqual(totals["total_investment"], Decimal("0.00"))
        self.assertEqual(totals["total_current_value"], Decimal("0.00"))
        self.assertEqual(totals["total_gain_loss"], Decimal("0.00"))
        self.assertIsNone(totals["total_gain_loss_pct"])
        self.assertIsNone(totals["total_dollars_per_day"])

    def test_two_lots(self):
        lots = [
            {
                "quantity": Decimal("10"),
                "purchase_price": Decimal("100.00"),
                "current_price": Decimal("110.00"),
                "trade_date": date(2026, 1, 1),
            },
            {
                "quantity": Decimal("20"),
                "purchase_price": Decimal("200.00"),
                "current_price": Decimal("220.00"),
                "trade_date": date(2026, 1, 1),
            },
        ]
        totals = summary_totals(lots, as_of=date(2026, 1, 11))

        self.assertEqual(totals["total_investment"], Decimal("5000.00"))
        self.assertEqual(totals["total_current_value"], Decimal("5500.00"))
        self.assertEqual(totals["total_gain_loss"], Decimal("500.00"))
        self.assertEqual(totals["total_gain_loss_pct"], Decimal("10.00"))
        # lot1: 100/10 days = 10/day, lot2: 400/10 days = 40/day -> 50/day
        self.assertEqual(totals["total_dollars_per_day"], Decimal("50.00"))

    def test_fractional_lot_bought_today_excluded_from_dollars_per_day(self):
        lots = [
            {
                "quantity": Decimal("0.0625"),
                "purchase_price": Decimal("133.33"),
                "current_price": Decimal("100.00"),
                "trade_date": date(2026, 1, 11),
            },
        ]
        totals = summary_totals(lots, as_of=date(2026, 1, 11))

        self.assertEqual(totals["total_gain_loss"], Decimal("-2.08"))
        self.assertIsNone(totals["total_dollars_per_day"])


if __name__ == "__main__":
    unittest.main()
