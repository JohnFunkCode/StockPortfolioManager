import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.books_repository import BooksRepository  # noqa: E402
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


class PortfolioRepositorySaleNotesTest(unittest.TestCase):
    """Issue #266: the reason for a sale is recorded per lot_sales row, can be
    read back (list_sales) and edited (update_sale_notes), and every one of
    those paths is owner-scoped through the sale's lot."""

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

    def _two_lots(self):
        lot1 = self.repo.add_position(OWNER, self._row(purchase_date="2026-01-01"))
        lot2 = self.repo.add_position(OWNER, self._row(purchase_date="2026-02-01"))
        return lot1, lot2

    def _close_both(self, **kwargs):
        lot1, lot2 = self._two_lots()
        results = self.repo.close_lots(
            OWNER, [(lot1, Decimal("5")), (lot2, Decimal("3"))],
            sale_price=Decimal("18.00"), sale_trade_date="2026-03-01",
            allocation_method="FIFO", **kwargs,
        )
        return lot1, lot2, results

    def test_default_note_is_written_to_every_sale_row(self):
        _, _, results = self._close_both(notes="Rebalancing")

        self.assertEqual([r["notes"] for r in results], ["Rebalancing", "Rebalancing"])
        sales = self.repo.list_sales(OWNER, SYMBOL)
        self.assertEqual([s["notes"] for s in sales], ["Rebalancing", "Rebalancing"])

    def test_lot_notes_override_the_default_for_one_lot(self):
        lot1, lot2 = self._two_lots()
        self.repo.close_lots(
            OWNER, [(lot1, Decimal("5")), (lot2, Decimal("3"))],
            sale_price=Decimal("18.00"), sale_trade_date="2026-03-01",
            allocation_method="FIFO", notes="default", lot_notes={lot2: "specific"},
        )
        by_lot = {s["lot_id"]: s["notes"] for s in self.repo.list_sales(OWNER)}
        self.assertEqual(by_lot, {lot1: "default", lot2: "specific"})

    def test_a_sale_without_notes_stores_none(self):
        self._close_both()
        self.assertTrue(all(s["notes"] is None for s in self.repo.list_sales(OWNER)))

    def test_partial_close_child_keeps_the_purchase_note(self):
        lot_id = self.repo.add_position(OWNER, self._row(quantity=10, notes="thesis"))

        results = self.repo.close_lots(
            OWNER, [(lot_id, Decimal("4"))], sale_price=Decimal("20.00"),
            sale_trade_date="2026-03-01", allocation_method="FIFO", notes="trim",
        )

        child = self.repo.get_lot(OWNER, results[0]["child_lot_id"])
        self.assertEqual(child["notes"], "thesis")
        self.assertEqual(self.repo.get_lot(OWNER, lot_id)["notes"], "thesis")
        self.assertEqual(self.repo.list_sales(OWNER)[0]["notes"], "trim")

    def test_list_sales_returns_the_sale_fields(self):
        lot_id = self.repo.add_position(OWNER, self._row(quantity=10))
        self.repo.close_lots(
            OWNER, [(lot_id, Decimal("10"))], sale_price=Decimal("15.00"),
            sale_trade_date="2026-03-01", allocation_method="FIFO", notes="why",
        )

        (sale,) = self.repo.list_sales(OWNER)
        self.assertEqual(sale["lot_id"], lot_id)
        self.assertEqual(sale["symbol"], SYMBOL)
        self.assertEqual(sale["shares_sold"], Decimal("10"))
        self.assertEqual(sale["sale_price"], Decimal("15"))
        self.assertEqual(sale["sale_trade_date"], "2026-03-01")
        self.assertEqual(sale["allocation_method"], "FIFO")
        self.assertEqual(sale["notes"], "why")

    def test_list_sales_is_scoped_by_owner_and_symbol(self):
        self._close_both(notes="mine")

        self.assertEqual(len(self.repo.list_sales(OWNER)), 2)
        self.assertEqual(len(self.repo.list_sales(OWNER, SYMBOL.lower())), 2)
        self.assertEqual(self.repo.list_sales(OWNER, "ZZOTHER"), [])
        self.assertEqual(self.repo.list_sales("zz_someone_else"), [])

    def test_update_sale_notes_sets_and_clears(self):
        _, _, results = self._close_both(notes="before")
        sale_id = results[0]["sale_id"]

        self.assertTrue(self.repo.update_sale_notes(OWNER, sale_id, "after"))
        by_id = {s["sale_id"]: s["notes"] for s in self.repo.list_sales(OWNER)}
        self.assertEqual(by_id[sale_id], "after")
        self.assertEqual(by_id[results[1]["sale_id"]], "before")

        self.assertTrue(self.repo.update_sale_notes(OWNER, sale_id, None))
        by_id = {s["sale_id"]: s["notes"] for s in self.repo.list_sales(OWNER)}
        self.assertIsNone(by_id[sale_id])

    def test_update_sale_notes_is_owner_scoped(self):
        _, _, results = self._close_both(notes="mine")
        sale_id = results[0]["sale_id"]

        self.assertFalse(self.repo.update_sale_notes("zz_someone_else", sale_id, "hijack"))
        self.assertFalse(self.repo.update_sale_notes(OWNER, 999999999, "nobody"))
        by_id = {s["sale_id"]: s["notes"] for s in self.repo.list_sales(OWNER)}
        self.assertEqual(by_id[sale_id], "mine")


if __name__ == "__main__":
    unittest.main()


class PortfolioRepositoryRealizedSalesTest(unittest.TestCase):
    """#235 step 2.1: `list_realized_sales` returns each sale with the basis
    side of its lot, bounded by `sale_trade_date`, for one owner."""

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = PortfolioRepository()

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM positions WHERE owner = %s", (OWNER,))
            conn.commit()

    def _sold(self, sale_date, *, shares="2", **lot):
        row = {
            "name": "Test Lot", "symbol": SYMBOL, "purchase_price": 10.0, "quantity": 5,
            "purchase_date": "2026-01-02", "currency": "USD",
        }
        row.update(lot)
        lot_id = self.repo.add_position(OWNER, row)
        (result,) = self.repo.close_lots(
            OWNER, [(lot_id, Decimal(shares))], sale_price=Decimal("18.50"),
            sale_trade_date=sale_date, allocation_method="FIFO", notes="why",
        )
        return lot_id, result["sale_id"]

    def test_a_sale_carries_its_lots_basis(self):
        lot_id, sale_id = self._sold("2026-03-01", account="IRA")

        (sale,) = self.repo.list_realized_sales(OWNER)
        self.assertEqual(sale, {
            "sale_id": sale_id,
            "lot_id": lot_id,
            "symbol": SYMBOL,
            "shares_sold": Decimal("2"),
            "sale_price": Decimal("18.5"),
            "sale_trade_date": "2026-03-01",
            "allocation_method": "FIFO",
            "notes": "why",
            "purchase_price": Decimal("10"),
            "trade_date": "2026-01-02",
            "currency": "USD",
            "account": "IRA",
            "fx_rate_at_purchase": None,
            "fx_rate_at_sale": None,
        })

    def test_a_partial_close_is_listed_once_against_the_closed_parent(self):
        lot_id, _ = self._sold("2026-03-01", shares="2")

        (sale,) = self.repo.list_realized_sales(OWNER)
        self.assertEqual((sale["lot_id"], sale["shares_sold"]), (lot_id, Decimal("2")))
        # The 3-share remainder is an open child lot, not a sale.
        self.assertEqual([p["quantity"] for p in self.repo.list_positions(OWNER)], [Decimal("3")])

    def test_the_window_bounds_the_sale_trade_date_inclusively(self):
        for day in ("2026-02-28", "2026-03-01", "2026-03-15", "2026-03-31", "2026-04-01"):
            self._sold(day)

        def dates(**window):
            return [s["sale_trade_date"] for s in self.repo.list_realized_sales(OWNER, **window)]

        self.assertEqual(len(dates()), 5)
        self.assertEqual(
            dates(start_date=date(2026, 3, 1), end_date=date(2026, 3, 31)),
            ["2026-03-01", "2026-03-15", "2026-03-31"],
        )
        self.assertEqual(dates(start_date=date(2026, 3, 31)), ["2026-03-31", "2026-04-01"])
        self.assertEqual(dates(end_date=date(2026, 3, 1)), ["2026-02-28", "2026-03-01"])
        self.assertEqual(dates(start_date=date(2027, 1, 1)), [])

    def test_the_legacy_sale_columns_are_not_a_source(self):
        # A CSV-imported row with sale_price / sale_date and no lot_sales row.
        self.repo.add_position(OWNER, {
            "name": "Legacy", "symbol": SYMBOL, "purchase_price": 10.0, "quantity": 5,
            "purchase_date": "2026-01-02", "currency": "USD",
            "sale_price": 20.0, "sale_date": "2026-02-01",
        })
        self.assertEqual(self.repo.list_realized_sales(OWNER), [])

    def test_list_realized_sales_is_owner_scoped(self):
        self._sold("2026-03-01")

        self.assertEqual(len(self.repo.list_realized_sales(OWNER)), 1)
        self.assertEqual(self.repo.list_realized_sales("zz_someone_else"), [])
        self.assertEqual(
            self.repo.list_realized_sales(
                "zz_someone_else", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
            [],
        )


FX_OTHER_OWNER = "zz_owner_lots_other"


class PortfolioRepositoryFxRatesTest(unittest.TestCase):
    """#235 step 2.3: the FX rates stored with a lot and with a sale, and the
    backfill's reads and writes."""

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = PortfolioRepository()

    def _purge(self):
        with closing(get_connection()) as conn:
            for owner in (OWNER, FX_OTHER_OWNER):
                conn.execute("DELETE FROM positions WHERE owner = %s", (owner,))
            conn.commit()

    def _lot(self, owner=OWNER, **overrides):
        row = {
            "name": "Test Lot", "symbol": SYMBOL, "purchase_price": 10.0, "quantity": 5,
            "purchase_date": "2026-01-02", "currency": "EUR",
        }
        row.update(overrides)
        return self.repo.add_position(owner, row)

    def _rates(self, owner=OWNER):
        return [
            (s["fx_rate_at_purchase"], s["fx_rate_at_sale"])
            for s in self.repo.list_realized_sales(owner)
        ]

    def test_a_lot_and_its_sale_store_their_rates(self):
        lot = self._lot(fx_rate_at_purchase=Decimal("1.10"))
        self.repo.close_lots(
            OWNER, [(lot, Decimal("5"))], Decimal("12"), "2026-03-01",
            fx_rates_at_sale={"EUR": Decimal("1.05")},
        )
        self.assertEqual(self._rates(), [(Decimal("1.1"), Decimal("1.05"))])

    def test_a_sale_takes_the_rate_for_its_own_lots_currency(self):
        eur = self._lot(fx_rate_at_purchase=Decimal("1.10"))
        usd = self._lot(currency="USD")
        gbp = self._lot(currency="GBP", fx_rate_at_purchase=Decimal("1.30"))
        self.repo.close_lots(
            OWNER, [(eur, Decimal("5")), (usd, Decimal("5")), (gbp, Decimal("5"))],
            Decimal("12"), "2026-03-01", fx_rates_at_sale={"EUR": Decimal("1.05")},
        )
        by_lot = {s["lot_id"]: s["fx_rate_at_sale"] for s in self.repo.list_realized_sales(OWNER)}
        # USD needs none, and GBP had none to be found: both stay NULL, never 1.
        self.assertEqual(by_lot, {eur: Decimal("1.05"), usd: None, gbp: None})

    def test_no_rates_given_stores_none(self):
        lot = self._lot()
        self.repo.close_lots(OWNER, [(lot, Decimal("5"))], Decimal("12"), "2026-03-01")
        self.assertEqual(self._rates(), [(None, None)])

    def test_the_remainder_of_a_split_keeps_its_parents_purchase_rate(self):
        lot = self._lot(fx_rate_at_purchase=Decimal("1.10"))
        (first,) = self.repo.close_lots(
            OWNER, [(lot, Decimal("2"))], Decimal("12"), "2026-03-01",
            fx_rates_at_sale={"EUR": Decimal("1.05")},
        )
        self.repo.close_lots(
            OWNER, [(first["child_lot_id"], Decimal("3"))], Decimal("13"), "2026-04-01",
            fx_rates_at_sale={"EUR": Decimal("1.07")},
        )
        self.assertEqual(self._rates(), [
            (Decimal("1.1"), Decimal("1.05")),
            (Decimal("1.1"), Decimal("1.07")),
        ])

    def test_update_lot_can_replace_or_clear_the_purchase_rate(self):
        lot = self._lot(fx_rate_at_purchase=Decimal("1.10"))
        self.repo.insert_sale(OWNER, lot, 1, 12.0, "2026-03-01")

        self.assertTrue(self.repo.update_lot(OWNER, lot, {"fx_rate_at_purchase": Decimal("1.20")}))
        self.assertEqual(self._rates(), [(Decimal("1.2"), None)])
        self.assertTrue(self.repo.update_lot(OWNER, lot, {"fx_rate_at_purchase": None}))
        self.assertEqual(self._rates(), [(None, None)])

    def test_a_currency_edit_clears_that_lots_sale_rates_only(self):
        lot = self._lot(fx_rate_at_purchase=Decimal("1.10"))
        other = self._lot(fx_rate_at_purchase=Decimal("1.10"))
        for lot_id in (lot, other):
            self.repo.insert_sale(
                OWNER, lot_id, 1, 12.0, "2026-03-01", fx_rate_at_sale=Decimal("1.05"))

        def sale_rates():
            return {s["lot_id"]: s["fx_rate_at_sale"] for s in self.repo.list_realized_sales(OWNER)}

        # An edit that leaves the currency alone leaves the sale rates alone.
        self.repo.update_lot(OWNER, lot, {"notes": "x"})
        self.assertEqual(sale_rates(), {lot: Decimal("1.05"), other: Decimal("1.05")})
        # Another owner's edit matches no lot, so it clears nothing.
        self.assertFalse(self.repo.update_lot(FX_OTHER_OWNER, lot, {"currency": "GBP"}))
        self.assertEqual(sale_rates(), {lot: Decimal("1.05"), other: Decimal("1.05")})

        self.assertTrue(self.repo.update_lot(OWNER, lot, {"currency": "GBP"}))
        self.assertEqual(sale_rates(), {lot: None, other: Decimal("1.05")})

    def test_missing_lists_only_non_usd_rows_without_a_rate(self):
        missing = self._lot()
        self._lot(fx_rate_at_purchase=Decimal("1.10"))
        usd = self._lot(currency="USD")
        blank = self._lot(currency=None)
        lower = self._lot(currency=" gbp ")
        sale_missing = self.repo.insert_sale(OWNER, missing, 1, 12.0, "2026-03-01")
        self.repo.insert_sale(OWNER, missing, 1, 12.0, "2026-03-02", fx_rate_at_sale=Decimal("1.05"))
        self.repo.insert_sale(OWNER, usd, 1, 12.0, "2026-03-03")
        self.repo.insert_sale(OWNER, blank, 1, 12.0, "2026-03-04")

        self.assertEqual(self.repo.list_lots_missing_fx(OWNER), [
            {"lot_id": missing, "symbol": SYMBOL, "currency": "EUR", "trade_date": "2026-01-02"},
            {"lot_id": lower, "symbol": SYMBOL, "currency": "GBP", "trade_date": "2026-01-02"},
        ])
        self.assertEqual(self.repo.list_sales_missing_fx(OWNER), [{
            "sale_id": sale_missing, "lot_id": missing, "symbol": SYMBOL,
            "currency": "EUR", "sale_trade_date": "2026-03-01",
        }])

    def test_a_closed_lot_is_still_listed(self):
        lot = self._lot()
        self.repo.close_lots(OWNER, [(lot, Decimal("5"))], Decimal("12"), "2026-03-01")
        self.assertEqual([r["lot_id"] for r in self.repo.list_lots_missing_fx(OWNER)], [lot])

    def test_fill_writes_a_missing_rate_and_never_replaces_one(self):
        lot = self._lot()
        sale = self.repo.insert_sale(OWNER, lot, 1, 12.0, "2026-03-01")

        self.assertTrue(self.repo.fill_lot_fx_rate(OWNER, lot, Decimal("1.10")))
        self.assertTrue(self.repo.fill_sale_fx_rate(OWNER, sale, Decimal("1.05")))
        self.assertEqual(self._rates(), [(Decimal("1.1"), Decimal("1.05"))])
        self.assertEqual(self.repo.list_lots_missing_fx(OWNER), [])
        self.assertEqual(self.repo.list_sales_missing_fx(OWNER), [])

        self.assertFalse(self.repo.fill_lot_fx_rate(OWNER, lot, Decimal("9")))
        self.assertFalse(self.repo.fill_sale_fx_rate(OWNER, sale, Decimal("9")))
        self.assertEqual(self._rates(), [(Decimal("1.1"), Decimal("1.05"))])

    def test_fx_backfill_is_owner_scoped(self):
        lot = self._lot()
        sale = self.repo.insert_sale(OWNER, lot, 1, 12.0, "2026-03-01")

        self.assertEqual(self.repo.list_lots_missing_fx(FX_OTHER_OWNER), [])
        self.assertEqual(self.repo.list_sales_missing_fx(FX_OTHER_OWNER), [])
        self.assertFalse(self.repo.fill_lot_fx_rate(FX_OTHER_OWNER, lot, Decimal("9")))
        self.assertFalse(self.repo.fill_sale_fx_rate(FX_OTHER_OWNER, sale, Decimal("9")))
        self.assertEqual(self._rates(), [(None, None)])


BOOK_OWNER = "zz_owner_books"
OTHER_OWNER = "zz_owner_books_other"


class PortfolioRepositoryBooksTest(unittest.TestCase):
    """#431 step 1.1: every positions statement is restricted to one book, and
    with no book named that book is the owner's real one. A paper lot is
    therefore invisible to, and untouched by, every default call.
    """

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.repo = PortfolioRepository()
        self.books = BooksRepository()

    def _purge(self):
        with closing(get_connection()) as conn:
            for owner in (BOOK_OWNER, OTHER_OWNER):
                conn.execute("DELETE FROM positions WHERE owner = %s", (owner,))
                conn.execute("DELETE FROM books WHERE owner = %s", (owner,))
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

    def _book_ids(self, owner):
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT book_id FROM books WHERE owner = %s ORDER BY book_id", (owner,)
            ).fetchall()
        return [int(r["book_id"]) for r in rows]

    def _seed(self):
        """One real lot and one paper lot of the same symbol. Returns
        (real_lot, paper_lot, paper_book)."""
        real_lot = self.repo.add_position(BOOK_OWNER, self._row())
        paper = self.books.create_paper_book(owner=BOOK_OWNER, name="Momentum", starting_cash=1000)
        paper_lot = self.repo.add_position(BOOK_OWNER, self._row(quantity=7), book_id=paper)
        return real_lot, paper_lot, paper

    # ------------------------------------------------------------------
    def test_the_real_book_is_created_once_by_the_first_write(self):
        self.assertEqual(self.repo.list_positions(BOOK_OWNER), [])
        self.assertEqual(self._book_ids(BOOK_OWNER), [], "a read must not create a book")

        self.repo.add_position(BOOK_OWNER, self._row())
        self.repo.add_position(BOOK_OWNER, self._row())

        books = self.books.list_books(owner=BOOK_OWNER)
        self.assertEqual([(b["kind"], b["name"]) for b in books], [("real", "Real")])

    def test_default_reads_see_only_the_real_book(self):
        real_lot, paper_lot, _ = self._seed()

        self.assertEqual([p["lot_id"] for p in self.repo.list_positions(BOOK_OWNER)], [real_lot])
        self.assertEqual(
            [p["lot_id"] for p in self.repo.list_lots_for_symbol(BOOK_OWNER, SYMBOL, status=None)],
            [real_lot],
        )
        self.assertEqual(self.repo.count_for_symbol(BOOK_OWNER, SYMBOL), 1)
        self.assertIsNotNone(self.repo.get_lot(BOOK_OWNER, real_lot))
        self.assertIsNone(self.repo.get_lot(BOOK_OWNER, paper_lot))

    def test_a_named_book_reads_only_that_book(self):
        real_lot, paper_lot, paper = self._seed()

        self.assertEqual(
            [p["lot_id"] for p in self.repo.list_positions(BOOK_OWNER, book_id=paper)], [paper_lot]
        )
        self.assertEqual(
            [p["lot_id"] for p in self.repo.list_lots_for_symbol(BOOK_OWNER, SYMBOL, book_id=paper)],
            [paper_lot],
        )
        self.assertEqual(self.repo.count_for_symbol(BOOK_OWNER, SYMBOL, book_id=paper), 1)
        self.assertIsNotNone(self.repo.get_lot(BOOK_OWNER, paper_lot, book_id=paper))
        self.assertIsNone(self.repo.get_lot(BOOK_OWNER, real_lot, book_id=paper))

        # Naming the real book is the same as naming nothing.
        real = self.books.ensure_real_book(owner=BOOK_OWNER)
        self.assertEqual(
            [p["lot_id"] for p in self.repo.list_positions(BOOK_OWNER, book_id=real)], [real_lot]
        )

    def test_default_mutations_leave_a_paper_book_alone(self):
        _, paper_lot, paper = self._seed()

        self.assertFalse(self.repo.update_lot(BOOK_OWNER, paper_lot, {"notes": "x"}))
        self.assertFalse(self.repo.delete_lot(BOOK_OWNER, paper_lot))
        with self.assertRaises(ValueError):
            self.repo.insert_sale(
                BOOK_OWNER, paper_lot, shares_sold=1, sale_price=12.0, sale_trade_date="2026-02-01",
            )
        with self.assertRaises(ValueError):
            self.repo.close_lots(
                BOOK_OWNER, [(paper_lot, Decimal("1"))], sale_price=12.0, sale_trade_date="2026-02-01",
            )
        # Removing the symbol and a CSV full-sync both act on the real book only.
        self.assertEqual(self.repo.remove_position(BOOK_OWNER, SYMBOL), 1)
        self.assertEqual(self.repo.replace_owner_positions(BOOK_OWNER, [self._row(quantity=2)]), 1)

        untouched = self.repo.get_lot(BOOK_OWNER, paper_lot, book_id=paper)
        self.assertEqual(untouched["quantity"], Decimal("7"))
        self.assertEqual(untouched["status"], "OPEN")
        self.assertIsNone(untouched["notes"])
        self.assertEqual(self.repo.list_sales(BOOK_OWNER, book_id=paper), [])
        self.assertEqual(len(self.repo.list_positions(BOOK_OWNER)), 1)

    def test_sales_follow_their_lots_book(self):
        real_lot, paper_lot, paper = self._seed()
        self.repo.insert_sale(
            BOOK_OWNER, real_lot, shares_sold=1, sale_price=12.0, sale_trade_date="2026-02-01",
        )
        paper_sale = self.repo.insert_sale(
            BOOK_OWNER, paper_lot, shares_sold=2, sale_price=13.0, sale_trade_date="2026-02-02",
            book_id=paper,
        )

        self.assertEqual([s["lot_id"] for s in self.repo.list_sales(BOOK_OWNER)], [real_lot])
        self.assertEqual(
            [s["sale_id"] for s in self.repo.list_sales(BOOK_OWNER, book_id=paper)], [paper_sale]
        )
        # A paper sale's note can't be edited through the real book.
        self.assertFalse(self.repo.update_sale_notes(BOOK_OWNER, paper_sale, "x"))
        self.assertTrue(self.repo.update_sale_notes(BOOK_OWNER, paper_sale, "x", book_id=paper))
        self.assertEqual(self.repo.list_sales(BOOK_OWNER, book_id=paper)[0]["notes"], "x")

    def test_realized_sales_follow_their_lots_book(self):
        real_lot, paper_lot, paper = self._seed()
        self.repo.close_lots(BOOK_OWNER, [(real_lot, Decimal("1"))], Decimal("12"), "2026-02-01")
        self.repo.close_lots(
            BOOK_OWNER, [(paper_lot, Decimal("2"))], Decimal("13"), "2026-02-02", book_id=paper)

        def lots(owner=BOOK_OWNER, **kwargs):
            return [s["lot_id"] for s in self.repo.list_realized_sales(owner, **kwargs)]

        self.assertEqual(lots(), [real_lot], "the default read reached a paper sale")
        self.assertEqual(lots(book_id=paper), [paper_lot])
        real = self.books.ensure_real_book(owner=BOOK_OWNER)
        self.assertEqual(lots(book_id=real), [real_lot])
        # Another owner naming this book reads nothing, and no book is made for them.
        self.assertEqual(lots(OTHER_OWNER, book_id=paper), [])
        self.assertEqual(lots(OTHER_OWNER), [])
        self.assertEqual(self._book_ids(OTHER_OWNER), [])

    def test_fx_backfill_follows_its_lots_book(self):
        real_lot = self.repo.add_position(BOOK_OWNER, self._row(currency="EUR"))
        paper = self.books.create_paper_book(owner=BOOK_OWNER, name="Momentum", starting_cash=1000)
        paper_lot = self.repo.add_position(BOOK_OWNER, self._row(currency="EUR"), book_id=paper)
        self.repo.insert_sale(BOOK_OWNER, real_lot, 1, 12.0, "2026-02-01")
        paper_sale = self.repo.insert_sale(
            BOOK_OWNER, paper_lot, 1, 13.0, "2026-02-02", book_id=paper)

        def missing(**kwargs):
            return (
                [r["lot_id"] for r in self.repo.list_lots_missing_fx(BOOK_OWNER, **kwargs)],
                [r["lot_id"] for r in self.repo.list_sales_missing_fx(BOOK_OWNER, **kwargs)],
            )

        self.assertEqual(missing(), ([real_lot], [real_lot]), "the default read reached a paper row")
        self.assertEqual(missing(book_id=paper), ([paper_lot], [paper_lot]))

        # A paper row can't be filled through the real book, or by another owner.
        self.assertFalse(self.repo.fill_lot_fx_rate(BOOK_OWNER, paper_lot, Decimal("1.1")))
        self.assertFalse(self.repo.fill_sale_fx_rate(BOOK_OWNER, paper_sale, Decimal("1.1")))
        self.assertFalse(
            self.repo.fill_lot_fx_rate(OTHER_OWNER, paper_lot, Decimal("1.1"), book_id=paper))
        self.assertFalse(
            self.repo.fill_sale_fx_rate(OTHER_OWNER, paper_sale, Decimal("1.1"), book_id=paper))
        self.assertEqual(missing(book_id=paper), ([paper_lot], [paper_lot]))
        self.assertEqual(self._book_ids(OTHER_OWNER), [])

        self.assertTrue(self.repo.fill_lot_fx_rate(BOOK_OWNER, paper_lot, Decimal("1.1"), book_id=paper))
        self.assertTrue(
            self.repo.fill_sale_fx_rate(BOOK_OWNER, paper_sale, Decimal("1.1"), book_id=paper))
        self.assertEqual(missing(book_id=paper), ([], []))
        self.assertEqual(missing(), ([real_lot], [real_lot]))

        # A currency edit through the real book can't clear a paper sale's rate.
        self.assertFalse(self.repo.update_lot(BOOK_OWNER, paper_lot, {"currency": "GBP"}))
        self.assertEqual(missing(book_id=paper), ([], []))

    def test_partial_close_child_stays_in_its_parents_book(self):
        _, paper_lot, paper = self._seed()

        result = self.repo.close_lots(
            BOOK_OWNER, [(paper_lot, Decimal("3"))], sale_price=12.0,
            sale_trade_date="2026-02-01", book_id=paper,
        )
        child = result[0]["child_lot_id"]

        self.assertEqual(
            [p["lot_id"] for p in self.repo.list_positions(BOOK_OWNER, book_id=paper)], [child]
        )
        self.assertEqual(self.repo.get_lot(BOOK_OWNER, child, book_id=paper)["quantity"], Decimal("4"))
        self.assertIsNone(self.repo.get_lot(BOOK_OWNER, child), "the remainder leaked into the real book")

    def test_another_owners_book_reads_nothing_and_takes_no_writes(self):
        _, paper_lot, paper = self._seed()

        self.assertEqual(self.repo.list_positions(OTHER_OWNER, book_id=paper), [])
        self.assertEqual(self.repo.list_lots_for_symbol(OTHER_OWNER, SYMBOL, book_id=paper), [])
        self.assertEqual(self.repo.count_for_symbol(OTHER_OWNER, SYMBOL, book_id=paper), 0)
        self.assertIsNone(self.repo.get_lot(OTHER_OWNER, paper_lot, book_id=paper))
        self.assertEqual(self.repo.list_sales(OTHER_OWNER, book_id=paper), [])

        self.assertFalse(self.repo.update_lot(OTHER_OWNER, paper_lot, {"notes": "x"}, book_id=paper))
        self.assertFalse(self.repo.delete_lot(OTHER_OWNER, paper_lot, book_id=paper))
        self.assertEqual(self.repo.remove_position(OTHER_OWNER, SYMBOL, book_id=paper), 0)
        with self.assertRaises(ValueError):
            self.repo.add_position(OTHER_OWNER, self._row(), book_id=paper)
        with self.assertRaises(ValueError):
            self.repo.replace_owner_positions(OTHER_OWNER, [], book_id=paper)

        # Nothing was written for the other owner, not even a real book.
        self.assertEqual(self._book_ids(OTHER_OWNER), [])
        self.assertEqual(len(self.repo.list_positions(BOOK_OWNER, book_id=paper)), 1)

    def test_list_owners_ignores_an_owner_with_only_paper_positions(self):
        paper = self.books.create_paper_book(owner=OTHER_OWNER, name="Only paper")
        self.repo.add_position(OTHER_OWNER, self._row(), book_id=paper)
        self.repo.add_position(BOOK_OWNER, self._row())

        owners = self.repo.list_owners()
        self.assertIn(BOOK_OWNER, owners)
        self.assertNotIn(OTHER_OWNER, owners)
