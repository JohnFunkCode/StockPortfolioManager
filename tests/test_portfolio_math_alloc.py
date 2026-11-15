"""Tests for allocate() lot-selection logic (issue #126 PR 4 Step 4.2)."""
import unittest
from datetime import date
from decimal import Decimal

from quantcore.analytics.portfolio_math import (
    LotAllocationError,
    _ordered_lots,
    _take_in_order,
    allocate,
)


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


class TestOrderedLots(unittest.TestCase):
    """`_ordered_lots`: the order each automatic method draws in (plan step 2.4)."""

    def _ids(self, lots, method):
        return [lot["lot_id"] for lot in _ordered_lots(lots, method)]

    def test_each_method_has_its_own_order(self):
        lots = list(reversed(make_lots()))  # arrival order must not matter
        self.assertEqual(self._ids(lots, "FIFO"), [1, 2, 3])
        self.assertEqual(self._ids(lots, "LIFO"), [3, 2, 1])
        self.assertEqual(self._ids(lots, "HIFO"), [2, 1, 3])

    def test_a_tie_is_broken_by_lot_id(self):
        lots = make_lots()
        for lot in lots:
            lot["trade_date"] = date(2026, 1, 1)
            lot["purchase_price"] = Decimal("100.00")
        self.assertEqual(self._ids(lots, "FIFO"), [1, 2, 3])
        # Descending methods reverse the whole key, the lot id included.
        self.assertEqual(self._ids(lots, "LIFO"), [3, 2, 1])
        self.assertEqual(self._ids(lots, "HIFO"), [3, 2, 1])

    def test_no_lots(self):
        self.assertEqual(_ordered_lots([], "FIFO"), [])

    def test_manual_has_no_order(self):
        with self.assertRaises(KeyError):
            _ordered_lots(make_lots(), "MANUAL")


class TestTakeInOrder(unittest.TestCase):
    """`_take_in_order`: drawing shares from lots already put in order."""

    def test_draws_front_first_and_stops_when_filled(self):
        self.assertEqual(
            _take_in_order(make_lots(), Decimal("12.5")),
            [(1, Decimal("10")), (2, Decimal("2.5"))],
        )

    def test_an_exact_fill_takes_nothing_from_the_next_lot(self):
        self.assertEqual(_take_in_order(make_lots(), Decimal("10")), [(1, Decimal("10"))])

    def test_an_empty_lot_is_skipped_not_listed(self):
        lots = make_lots()
        lots[0]["quantity"] = Decimal("0")
        self.assertEqual(_take_in_order(lots, Decimal("4")), [(2, Decimal("4"))])

    def test_too_few_shares_raises_with_what_was_available(self):
        with self.assertRaises(LotAllocationError) as ctx:
            _take_in_order(make_lots(), Decimal("31"))
        self.assertIn("Cannot allocate 31 shares: only 30 available", str(ctx.exception))

    def test_no_lots_raises(self):
        with self.assertRaises(LotAllocationError):
            _take_in_order([], Decimal("1"))


if __name__ == "__main__":
    unittest.main()
