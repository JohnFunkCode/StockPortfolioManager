import unittest
from contextlib import closing
from decimal import Decimal

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402

# Synthetic owner/symbol that won't collide with real positions in the
# configured QuantCore database.
OWNER = "zz_owner_lots"
SYMBOL = "ZZLOT1"


class PortfolioRepositoryLotsTest(unittest.TestCase):
    """Issue #126 Step 3.2: the repository stops overwriting and starts
    returning lot identity. These tests exercise PortfolioRepository directly
    (bypassing PortfolioService's duplicate-symbol guard, which is Step 3.3's
    concern) so multi-lot behavior can be verified now.
    """

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = PortfolioRepository()

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM positions WHERE owner = %s", (OWNER,))
            conn.commit()

    def _row(self, **overrides):
        row = {
            "name": "Test Lot",
            "symbol": SYMBOL,
            "purchase_price": 10.0,
            "quantity": 5,
            "purchase_date": "2026-01-02",
            "currency": "USD",
        }
        row.update(overrides)
        return row

    # ------------------------------------------------------------------
    def test_add_position_creates_a_new_lot_each_call(self):
        lot_id_1 = self.repo.add_position(OWNER, self._row())
        lot_id_2 = self.repo.add_position(OWNER, self._row())

        self.assertIsInstance(lot_id_1, int)
        self.assertIsInstance(lot_id_2, int)
        self.assertNotEqual(lot_id_1, lot_id_2)

        positions = self.repo.list_positions(OWNER)
        self.assertEqual(len(positions), 2)
        self.assertEqual(sorted(p["lot_id"] for p in positions), sorted([lot_id_1, lot_id_2]))

    def test_row_to_dict_has_lot_identity_fields_and_keeps_existing_keys(self):
        self.repo.add_position(OWNER, self._row())
        positions = self.repo.list_positions(OWNER)
        self.assertEqual(len(positions), 1)
        row = positions[0]

        # Existing CSV-parity keys, unchanged.
        for key in (
            "name", "symbol", "purchase_price", "quantity", "purchase_date",
            "currency", "sale_price", "sale_date", "source", "tags",
        ):
            self.assertIn(key, row)
        self.assertEqual(row["source"], "portfolio")
        self.assertEqual(row["tags"], [])
        self.assertEqual(row["purchase_price"], Decimal("10.0"))
        self.assertEqual(row["quantity"], Decimal("5"))
        self.assertIsInstance(row["purchase_price"], Decimal)
        self.assertIsInstance(row["quantity"], Decimal)

        # New lot-identity keys.
        for key in (
            "lot_id", "status", "parent_lot_id", "trade_date", "fees",
            "acquisition_type", "account", "notes",
        ):
            self.assertIn(key, row)
        self.assertEqual(row["status"], "OPEN")
        self.assertEqual(row["acquisition_type"], "BUY")
        self.assertIsNone(row["parent_lot_id"])
        self.assertEqual(row["trade_date"].isoformat(), "2026-01-02")

    def test_list_positions_defaults_to_open_lots_only(self):
        open_lot = self.repo.add_position(OWNER, self._row())
        closed_lot = self.repo.add_position(OWNER, self._row())
        self.repo.update_lot(OWNER, closed_lot, {"status": "CLOSED"})

        open_positions = self.repo.list_positions(OWNER)
        self.assertEqual([p["lot_id"] for p in open_positions], [open_lot])

        all_positions = self.repo.list_lots_for_symbol(OWNER, SYMBOL, status=None)
        self.assertEqual(
            sorted(p["lot_id"] for p in all_positions), sorted([open_lot, closed_lot])
        )

    def test_get_lot_is_owner_scoped(self):
        lot_id = self.repo.add_position(OWNER, self._row())

        self.assertIsNotNone(self.repo.get_lot(OWNER, lot_id))
        self.assertIsNone(self.repo.get_lot("zz_someone_else", lot_id))

    def test_update_lot_is_owner_scoped(self):
        lot_id = self.repo.add_position(OWNER, self._row())

        self.assertFalse(self.repo.update_lot("zz_someone_else", lot_id, {"notes": "x"}))
        self.assertTrue(self.repo.update_lot(OWNER, lot_id, {"notes": "updated"}))
        self.assertEqual(self.repo.get_lot(OWNER, lot_id)["notes"], "updated")

    def test_update_lot_rejects_unknown_field(self):
        lot_id = self.repo.add_position(OWNER, self._row())
        with self.assertRaises(ValueError):
            self.repo.update_lot(OWNER, lot_id, {"position_id": 999})

    def test_delete_lot_is_owner_scoped(self):
        lot_id = self.repo.add_position(OWNER, self._row())

        self.assertFalse(self.repo.delete_lot("zz_someone_else", lot_id))
        self.assertTrue(self.repo.delete_lot(OWNER, lot_id))
        self.assertIsNone(self.repo.get_lot(OWNER, lot_id))

    def test_insert_sale_requires_matching_owner(self):
        lot_id = self.repo.add_position(OWNER, self._row(quantity=10))

        with self.assertRaises(ValueError):
            self.repo.insert_sale(
                "zz_someone_else", lot_id, shares_sold=4, sale_price=12.0,
                sale_trade_date="2026-02-01",
            )

        sale_id = self.repo.insert_sale(
            OWNER, lot_id, shares_sold=4, sale_price=12.0,
            sale_trade_date="2026-02-01", allocation_method="FIFO",
        )
        self.assertIsInstance(sale_id, int)


class PortfolioRepositoryCloseLotsTest(unittest.TestCase):
    """Issue #126 Step 4.3: close_lots() atomically records a sale that may
    span multiple lots, splitting a partially-sold lot into a CLOSED parent
    plus an OPEN child that preserves the original trade_date/purchase_price.
    """

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = PortfolioRepository()

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM positions WHERE owner = %s", (OWNER,))
            conn.commit()

    def _row(self, **overrides):
        row = {
            "name": "Test Lot",
            "symbol": SYMBOL,
            "purchase_price": 10.0,
            "quantity": 5,
            "purchase_date": "2026-01-02",
            "currency": "USD",
        }
        row.update(overrides)
        return row

    def test_full_close_marks_lot_closed_with_no_child(self):
        lot_id = self.repo.add_position(OWNER, self._row(quantity=10))

        results = self.repo.close_lots(
            OWNER, [(lot_id, Decimal("10"))], sale_price=Decimal("15.00"),
            sale_trade_date="2026-03-01", allocation_method="FIFO",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["lot_id"], lot_id)
        self.assertIsNone(results[0]["child_lot_id"])
        self.assertEqual(results[0]["shares_sold"], Decimal("10"))

        lot = self.repo.get_lot(OWNER, lot_id)
        self.assertEqual(lot["status"], "CLOSED")
        self.assertEqual(lot["quantity"], Decimal("10"))

    def test_partial_close_creates_child_with_original_lineage(self):
        lot_id = self.repo.add_position(OWNER, self._row(
            quantity=10, purchase_price=12.5, purchase_date="2026-01-05",
        ))

        results = self.repo.close_lots(
            OWNER, [(lot_id, Decimal("4"))], sale_price=Decimal("20.00"),
            sale_trade_date="2026-03-01", allocation_method="FIFO",
        )

        child_lot_id = results[0]["child_lot_id"]
        self.assertIsNotNone(child_lot_id)

        parent = self.repo.get_lot(OWNER, lot_id)
        self.assertEqual(parent["status"], "CLOSED")
        self.assertEqual(parent["quantity"], Decimal("4"))

        child = self.repo.get_lot(OWNER, child_lot_id)
        self.assertEqual(child["status"], "OPEN")
        self.assertEqual(child["quantity"], Decimal("6"))
        self.assertEqual(child["parent_lot_id"], lot_id)
        self.assertEqual(child["trade_date"].isoformat(), "2026-01-05")
        self.assertEqual(child["purchase_price"], Decimal("12.5"))

    def test_multi_lot_close_spans_two_lots(self):
        lot1 = self.repo.add_position(OWNER, self._row(
            quantity=5, purchase_date="2026-01-01",
        ))
        lot2 = self.repo.add_position(OWNER, self._row(
            quantity=5, purchase_date="2026-02-01",
        ))

        results = self.repo.close_lots(
            OWNER, [(lot1, Decimal("5")), (lot2, Decimal("3"))],
            sale_price=Decimal("18.00"), sale_trade_date="2026-03-01",
            allocation_method="FIFO",
        )
        self.assertEqual(len(results), 2)

        closed_fully = self.repo.get_lot(OWNER, lot1)
        self.assertEqual(closed_fully["status"], "CLOSED")
        self.assertIsNone(results[0]["child_lot_id"])

        parent2 = self.repo.get_lot(OWNER, lot2)
        self.assertEqual(parent2["status"], "CLOSED")
        self.assertEqual(parent2["quantity"], Decimal("3"))
        child2 = self.repo.get_lot(OWNER, results[1]["child_lot_id"])
        self.assertEqual(child2["quantity"], Decimal("2"))
        self.assertEqual(child2["parent_lot_id"], lot2)

    def test_mid_transaction_failure_leaves_db_unchanged(self):
        lot_id = self.repo.add_position(OWNER, self._row(quantity=10))

        with self.assertRaises(ValueError):
            self.repo.close_lots(
                OWNER, [(lot_id, Decimal("5")), (999999, Decimal("1"))],
                sale_price=Decimal("20.00"), sale_trade_date="2026-03-01",
                allocation_method="FIFO",
            )

        lot = self.repo.get_lot(OWNER, lot_id)
        self.assertEqual(lot["status"], "OPEN")
        self.assertEqual(lot["quantity"], Decimal("10"))

        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM lot_sales WHERE lot_id = %s", (lot_id,)
            ).fetchone()
        self.assertEqual(row["n"], 0)


if __name__ == "__main__":
    unittest.main()
