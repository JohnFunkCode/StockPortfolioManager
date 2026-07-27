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


if __name__ == "__main__":
    unittest.main()
