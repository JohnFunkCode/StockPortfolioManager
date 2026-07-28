"""Tests for allocate() lot-selection logic (issue #126 PR 4 Step 4.2)."""
import unittest
from datetime import date
from decimal import Decimal

from quantcore.analytics.portfolio_math import LotAllocationError, allocate


def make_lots():
    """3-lot fixture: oldest/cheapest first by trade_date, HIFO order differs."""
    return [
        {
            "lot_id": 1,
            "quantity": Decimal("10"),
            "trade_date": date(2026, 1, 1),
            "purchase_price": Decimal("100.00"),
            "status": "OPEN",
        },
        {
            "lot_id": 2,
            "quantity": Decimal("10"),
            "trade_date": date(2026, 2, 1),
            "purchase_price": Decimal("150.00"),
            "status": "OPEN",
        },
        {
            "lot_id": 3,
            "quantity": Decimal("10"),
            "trade_date": date(2026, 3, 1),
            "purchase_price": Decimal("80.00"),
            "status": "OPEN",
        },
    ]


class TestFifo(unittest.TestCase):
    def test_single_lot(self):
        self.assertEqual(
            allocate(make_lots(), Decimal("5"), "FIFO"),
            [(1, Decimal("5"))],
        )

    def test_spans_multiple_lots_oldest_first(self):
        result = allocate(make_lots(), Decimal("15"), "FIFO")
        self.assertEqual(result, [(1, Decimal("10")), (2, Decimal("5"))])


class TestLifo(unittest.TestCase):
    def test_single_lot_newest_first(self):
        self.assertEqual(
            allocate(make_lots(), Decimal("5"), "LIFO"),
            [(3, Decimal("5"))],
        )

    def test_spans_multiple_lots(self):
        result = allocate(make_lots(), Decimal("15"), "LIFO")
        self.assertEqual(result, [(3, Decimal("10")), (2, Decimal("5"))])


class TestHifo(unittest.TestCase):
    def test_highest_cost_basis_first(self):
        self.assertEqual(
            allocate(make_lots(), Decimal("5"), "HIFO"),
            [(2, Decimal("5"))],
        )

    def test_spans_multiple_lots(self):
        result = allocate(make_lots(), Decimal("15"), "HIFO")
        self.assertEqual(result, [(2, Decimal("10")), (1, Decimal("5"))])


class TestManual(unittest.TestCase):
    def test_valid_manual_allocation(self):
        pairs = [(1, Decimal("4")), (3, Decimal("6"))]
        self.assertEqual(allocate(make_lots(), Decimal("10"), "MANUAL", pairs), pairs)

    def test_manual_over_allocates_a_lot(self):
        with self.assertRaises(LotAllocationError):
            allocate(make_lots(), Decimal("15"), "MANUAL", [(1, Decimal("15"))])

    def test_manual_totals_mismatch(self):
        with self.assertRaises(LotAllocationError):
            allocate(make_lots(), Decimal("10"), "MANUAL", [(1, Decimal("5"))])

    def test_manual_references_unknown_lot(self):
        with self.assertRaises(LotAllocationError):
            allocate(make_lots(), Decimal("5"), "MANUAL", [(999, Decimal("5"))])

    def test_manual_references_closed_lot(self):
        lots = make_lots()
        lots[0]["status"] = "CLOSED"
        with self.assertRaises(LotAllocationError):
            allocate(lots, Decimal("5"), "MANUAL", [(1, Decimal("5"))])

    def test_manual_duplicate_lot_rejected(self):
        with self.assertRaises(LotAllocationError):
            allocate(
                make_lots(), Decimal("10"), "MANUAL",
                [(1, Decimal("5")), (1, Decimal("5"))],
            )


class TestOverSell(unittest.TestCase):
    def test_fifo_over_sell_raises(self):
        with self.assertRaises(LotAllocationError):
            allocate(make_lots(), Decimal("31"), "FIFO")

    def test_over_sell_does_not_clamp(self):
        # Exactly the total held (30) succeeds; one share more must raise,
        # never silently clamp to what's available.
        allocate(make_lots(), Decimal("30"), "FIFO")
        with self.assertRaises(LotAllocationError):
            allocate(make_lots(), Decimal("30.01"), "FIFO")

    def test_closed_lots_excluded_from_auto_methods(self):
        lots = make_lots()
        lots[2]["status"] = "CLOSED"  # newest/cheapest lot no longer available
        with self.assertRaises(LotAllocationError):
            allocate(lots, Decimal("21"), "FIFO")


if __name__ == "__main__":
    unittest.main()
