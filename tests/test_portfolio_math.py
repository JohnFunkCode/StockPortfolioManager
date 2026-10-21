"""Tests for quantcore/analytics/portfolio_math.py (issue #126 PR 4 Step 4.1).
Pure Decimal math, no I/O — no mocks needed.
"""
import unittest
from datetime import date
from decimal import Decimal

from quantcore.analytics.portfolio_math import (
    allocation_segments,
    dollars_per_day,
    days_held,
    gain_loss,
    gain_loss_pct,
    period_return,
    position_rollup,
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

    def test_zero_cost_basis_returns_none(self):
        self.assertIsNone(gain_loss_pct(Decimal("10.00"), Decimal("0"), Decimal("10")))


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


class TestAllocationSegments(unittest.TestCase):
    """Issue #147 Part A: the legacy report's stacked bar, as three numbers.

    The property that matters for the drawing: the visible top of the bar is
    always the current value, whichever way the position went. base + gain for
    a winner, base + loss (loss being negative) for a loser.
    """

    def test_a_gain_stacks_above_the_cost_basis(self):
        segments = allocation_segments(Decimal("100.00"), Decimal("25.00"))
        self.assertEqual(segments["bar_base"], Decimal("100.00"))
        self.assertEqual(segments["bar_gain"], Decimal("25.00"))
        self.assertEqual(segments["bar_loss"], Decimal("0.00"))
        self.assertEqual(
            segments["bar_base"] + segments["bar_gain"], Decimal("125.00")
        )

    def test_a_loss_is_negative_so_it_draws_down_from_the_cost_basis(self):
        segments = allocation_segments(Decimal("100.00"), Decimal("-40.00"))
        self.assertEqual(segments["bar_base"], Decimal("100.00"))
        self.assertEqual(segments["bar_gain"], Decimal("0.00"))
        self.assertEqual(segments["bar_loss"], Decimal("-40.00"))
        self.assertEqual(
            segments["bar_base"] + segments["bar_loss"], Decimal("60.00")
        )

    def test_break_even_has_neither_segment(self):
        segments = allocation_segments(Decimal("100.00"), Decimal("0"))
        self.assertEqual(segments["bar_gain"], Decimal("0.00"))
        self.assertEqual(segments["bar_loss"], Decimal("0.00"))

    def test_an_unpriced_symbol_has_no_bar_at_all(self):
        """Rather than a zero-height bar, which reads as "worth nothing"."""
        self.assertEqual(
            allocation_segments(Decimal("100.00"), None),
            {"bar_base": None, "bar_gain": None, "bar_loss": None},
        )
        self.assertEqual(
            allocation_segments(None, Decimal("25.00")),
            {"bar_base": None, "bar_gain": None, "bar_loss": None},
        )

    def test_segments_are_quantized_to_cents(self):
        segments = allocation_segments(Decimal("100.005"), Decimal("1.004"))
        self.assertEqual(segments["bar_base"], Decimal("100.01"))
        self.assertEqual(segments["bar_gain"], Decimal("1.00"))


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

    def test_lot_missing_trade_date_is_excluded_not_crashed(self):
        lots = [
            {
                "quantity": Decimal("10"),
                "purchase_price": Decimal("100.00"),
                "current_price": Decimal("110.00"),
                "trade_date": None,
            },
            {
                "quantity": Decimal("20"),
                "purchase_price": Decimal("200.00"),
                "current_price": Decimal("220.00"),
                "trade_date": date(2026, 1, 1),
            },
        ]
        totals = summary_totals(lots, as_of=date(2026, 1, 11))

        # Only the second (valid) lot contributes.
        self.assertEqual(totals["total_investment"], Decimal("4000.00"))
        self.assertEqual(totals["total_current_value"], Decimal("4400.00"))

    def test_lot_missing_purchase_price_is_excluded_not_crashed(self):
        lots = [
            {
                "quantity": Decimal("10"),
                "purchase_price": None,
                "current_price": Decimal("110.00"),
                "trade_date": date(2026, 1, 1),
            },
        ]
        totals = summary_totals(lots, as_of=date(2026, 1, 11))
        self.assertEqual(totals["total_investment"], Decimal("0.00"))
        self.assertEqual(totals["total_current_value"], Decimal("0.00"))

    def test_lot_missing_quantity_is_excluded_not_crashed(self):
        lots = [
            {
                "quantity": None,
                "purchase_price": Decimal("100.00"),
                "current_price": Decimal("110.00"),
                "trade_date": date(2026, 1, 1),
            },
        ]
        totals = summary_totals(lots, as_of=date(2026, 1, 11))
        self.assertEqual(totals["total_investment"], Decimal("0.00"))
        self.assertEqual(totals["total_current_value"], Decimal("0.00"))


class TestPositionRollup(unittest.TestCase):
    """Issue #224: one held row per symbol, every open lot counted."""

    def test_no_lots_is_none_not_zero(self):
        self.assertEqual(
            position_rollup([]),
            {"quantity": None, "avg_price": None, "lot_count": 0},
        )

    def test_single_lot(self):
        result = position_rollup([{"quantity": Decimal("10"), "purchase_price": Decimal("50")}])
        self.assertEqual(result["quantity"], Decimal("10"))
        self.assertEqual(result["avg_price"], Decimal("50.00"))
        self.assertEqual(result["lot_count"], 1)

    def test_mixed_lot_sizes_are_quantity_weighted(self):
        # 10 @ 100 + 30 @ 200 = 7000 over 40 shares = 175, not the plain mean 150
        # and not the last lot's 200 (the retired /api/securities bug).
        result = position_rollup([
            {"quantity": Decimal("10"), "purchase_price": Decimal("100")},
            {"quantity": Decimal("30"), "purchase_price": Decimal("200")},
        ])
        self.assertEqual(result["quantity"], Decimal("40"))
        self.assertEqual(result["avg_price"], Decimal("175.00"))
        self.assertEqual(result["lot_count"], 2)

    def test_lot_missing_a_field_is_skipped(self):
        result = position_rollup([
            {"quantity": Decimal("5"), "purchase_price": Decimal("20")},
            {"quantity": None, "purchase_price": Decimal("999")},
            {"quantity": Decimal("7"), "purchase_price": None},
        ])
        self.assertEqual(result["quantity"], Decimal("5"))
        self.assertEqual(result["avg_price"], Decimal("20.00"))
        self.assertEqual(result["lot_count"], 1)

    def test_zero_total_quantity_does_not_divide(self):
        result = position_rollup([{"quantity": Decimal("0"), "purchase_price": Decimal("10")}])
        self.assertIsNone(result["quantity"])
        self.assertIsNone(result["avg_price"])

    def test_avg_price_rounds_half_up_to_cents(self):
        # (1 * 10.00 + 2 * 10.01) / 3 = 10.00666... -> 10.01
        result = position_rollup([
            {"quantity": Decimal("1"), "purchase_price": Decimal("10.00")},
            {"quantity": Decimal("2"), "purchase_price": Decimal("10.01")},
        ])
        self.assertEqual(result["avg_price"], Decimal("10.01"))


if __name__ == "__main__":
    unittest.main()
