import csv
import os
import tempfile
import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.portfolio_repository import PortfolioRepository  # noqa: E402
from quantcore.services.portfolio import (  # noqa: E402
    DuplicateSymbolError,
    PortfolioService,
)

# Synthetic owners/symbols that won't collide with real positions in the
# configured QuantCore database.
OWNER_A = "zz_owner_a"
OWNER_B = "zz_owner_b"
TEST_SYMBOLS = ["ZZTEST", "ZZTST2", "ZZTST3"]


class FakePrices:
    """Stand-in for PricesService: canned quotes/history, no network/DB."""

    def __init__(self, quotes=None, histories=None):
        self._quotes = quotes or {}
        self._histories = histories or {}

    def get_fast_price(self, symbol):
        return self._quotes.get(symbol)

    def get_quotes(self, symbols, force=False):
        result = {}
        for symbol in symbols:
            price = self._quotes.get(symbol)
            result[symbol] = {"price": price, "as_of": None, "stale": price is None}
        return result

    def get_history(self, symbol, interval="1d", days=365):
        return self._histories.get(symbol)


class FakeOptions:
    """Stand-in for OptionsService: canned gamma-wall history, no I/O."""

    def __init__(self, histories=None):
        self._histories = histories or {}

    def get_gamma_wall_history(self, symbol, since_days=90):
        return {"history": self._histories.get(symbol, [])}


class PortfolioServiceTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.prices = FakePrices()
        self.service = PortfolioService(PortfolioRepository(), prices=self.prices)

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM positions WHERE owner IN (%s, %s)", (OWNER_A, OWNER_B)
            )
            conn.commit()

    def _write_csv(self, rows):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        fields = [
            "name", "symbol", "purchase_price", "quantity",
            "purchase_date", "currency", "sale_price", "sale_date", "current_price",
        ]
        with open(path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        return path

    def _sample_rows(self):
        return [
            {
                "name": "Test One", "symbol": "ZZTEST", "purchase_price": "10.00",
                "quantity": "5", "purchase_date": "2026-01-02", "currency": "USD",
            },
            {
                "name": "Test Two", "symbol": "ZZTST2", "purchase_price": "20.50",
                "quantity": "3", "purchase_date": "2026-02-03", "currency": "USD",
            },
        ]

    # ------------------------------------------------------------------
    def test_import_csv_returns_count_and_lists_positions(self):
        path = self._write_csv(self._sample_rows())
        count = self.service.import_csv(path, OWNER_A)
        self.assertEqual(count, 2)

        positions = self.service.list_positions(OWNER_A)
        self.assertEqual(len(positions), 2)
        symbols = sorted(p["symbol"] for p in positions)
        self.assertEqual(symbols, ["ZZTEST", "ZZTST2"])
        first = next(p for p in positions if p["symbol"] == "ZZTEST")
        self.assertEqual(first["purchase_price"], 10.0)
        self.assertEqual(first["quantity"], 5)
        self.assertEqual(first["source"], "portfolio")
        self.assertEqual(first["tags"], [])

    def test_import_csv_skips_empty_symbol_rows(self):
        rows = self._sample_rows() + [{"name": "Blank", "symbol": ""}]
        path = self._write_csv(rows)
        count = self.service.import_csv(path, OWNER_A)
        self.assertEqual(count, 2)

    def test_import_csv_is_full_sync_replace(self):
        first_path = self._write_csv(self._sample_rows())
        self.service.import_csv(first_path, OWNER_A)

        # Re-import with a single, different row — the prior rows must be gone.
        replacement = [{
            "name": "Replacement", "symbol": "ZZTST3", "purchase_price": "99.00",
            "quantity": "1", "purchase_date": "2026-03-04", "currency": "USD",
        }]
        second_path = self._write_csv(replacement)
        count = self.service.import_csv(second_path, OWNER_A)
        self.assertEqual(count, 1)

        positions = self.service.list_positions(OWNER_A)
        self.assertEqual([p["symbol"] for p in positions], ["ZZTST3"])

    def test_reimport_is_idempotent(self):
        path = self._write_csv(self._sample_rows())
        self.assertEqual(self.service.import_csv(path, OWNER_A), 2)
        self.assertEqual(self.service.import_csv(path, OWNER_A), 2)
        self.assertEqual(len(self.service.list_positions(OWNER_A)), 2)

    def test_owner_isolation(self):
        path = self._write_csv(self._sample_rows())
        self.service.import_csv(path, OWNER_A)
        self.service.import_csv(path, OWNER_B)

        # Replacing owner A leaves owner B untouched.
        replacement = self._write_csv([{
            "name": "Only", "symbol": "ZZTST3", "purchase_price": "1.00",
            "quantity": "1", "purchase_date": "2026-04-05", "currency": "USD",
        }])
        self.service.import_csv(replacement, OWNER_A)

        self.assertEqual([p["symbol"] for p in self.service.list_positions(OWNER_A)], ["ZZTST3"])
        self.assertEqual(
            sorted(p["symbol"] for p in self.service.list_positions(OWNER_B)),
            ["ZZTEST", "ZZTST2"],
        )

        owners = self.service.list_owners()
        self.assertIn(OWNER_A, owners)
        self.assertIn(OWNER_B, owners)

    def test_add_position_allows_multiple_lots_of_same_symbol(self):
        result = self.service.add_position(
            OWNER_A, name="Added", symbol="zztest", purchase_price="12.5",
            quantity="4", purchase_date="2026-01-10", currency="usd",
        )
        self.assertEqual(result, {"symbol": "ZZTEST"})

        positions = self.service.list_positions(OWNER_A)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "ZZTEST")
        self.assertEqual(positions[0]["currency"], "USD")

        # A second lot of the same symbol on the same date no longer raises —
        # multiple lots per symbol is the feature (issue #126 Step 3.3).
        second = self.service.add_position(
            OWNER_A, name="Second Lot", symbol="ZZTEST", purchase_price="13",
            quantity="1", purchase_date="2026-01-10",
        )
        self.assertEqual(second, {"symbol": "ZZTEST"})

        positions = self.service.list_positions(OWNER_A)
        self.assertEqual(len(positions), 2)
        lot_ids = {p["lot_id"] for p in positions}
        self.assertEqual(len(lot_ids), 2)

    def test_add_position_fractional_quantity_round_trips_exactly(self):
        self.service.add_position(
            OWNER_A, name="Fractional", symbol="ZZTEST", purchase_price="100",
            quantity="0.0625", purchase_date="2026-01-10",
        )
        positions = self.service.list_positions(OWNER_A)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["quantity"], Decimal("0.0625"))

    def test_add_position_purchase_date_populates_trade_date(self):
        self.service.add_position(
            OWNER_A, name="Legacy Alias", symbol="ZZTEST", purchase_price="10",
            quantity="1", purchase_date="2026-01-10",
        )
        positions = self.service.list_positions(OWNER_A)
        self.assertEqual(positions[0]["trade_date"].isoformat(), "2026-01-10")

    def test_add_position_without_symbol_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(OWNER_A, name="No Symbol", purchase_price="1")

    def test_add_position_missing_purchase_price_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(
                OWNER_A, symbol="ZZTEST", quantity="1", purchase_date="2026-01-10",
            )

    def test_add_position_missing_quantity_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(
                OWNER_A, symbol="ZZTEST", purchase_price="1", purchase_date="2026-01-10",
            )

    def test_add_position_missing_trade_date_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(
                OWNER_A, symbol="ZZTEST", purchase_price="1", quantity="1",
            )

    def test_add_position_zero_purchase_price_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(
                OWNER_A, symbol="ZZTEST", purchase_price="0", quantity="1",
                purchase_date="2026-01-10",
            )

    def test_add_position_negative_quantity_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(
                OWNER_A, symbol="ZZTEST", purchase_price="1", quantity="-5",
                purchase_date="2026-01-10",
            )

    def test_add_position_invalid_trade_date_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.add_position(
                OWNER_A, symbol="ZZTEST", purchase_price="1", quantity="1",
                trade_date="not-a-date",
            )

    def test_remove_position_returns_rows_removed(self):
        path = self._write_csv(self._sample_rows())
        self.service.import_csv(path, OWNER_A)

        removed = self.service.remove_position(OWNER_A, "zztest")
        self.assertEqual(removed, 1)
        self.assertEqual(
            [p["symbol"] for p in self.service.list_positions(OWNER_A)], ["ZZTST2"]
        )

    def test_remove_missing_position_returns_zero(self):
        self.assertEqual(self.service.remove_position(OWNER_A, "ZZTEST"), 0)


class PortfolioServiceLotLifecycleTest(unittest.TestCase):
    """Issue #126 PR 4 Step 4.3: list_lots/symbol_rows enrichment and the
    update/delete/close_lot write path.
    """

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.prices = FakePrices(quotes={"ZZTEST": 15.0, "ZZTST2": 25.0})
        self.options = FakeOptions()
        self.service = PortfolioService(
            PortfolioRepository(), prices=self.prices, options=self.options
        )

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM positions WHERE owner IN (%s, %s)", (OWNER_A, OWNER_B)
            )
            conn.commit()

    def _add(self, **overrides):
        fields = {
            "name": "Test Lot", "symbol": "ZZTEST", "purchase_price": "10.00",
            "quantity": "5", "purchase_date": "2020-01-02", "currency": "USD",
        }
        fields.update(overrides)
        result = self.service.add_position(OWNER_A, **fields)
        lots = self.service.list_lots(OWNER_A, status=None) if False else self.service.list_positions(OWNER_A)
        lot = max(
            (p for p in lots if p["symbol"] == result["symbol"]),
            key=lambda p: p["lot_id"],
        )
        return lot["lot_id"]

    # ------------------------------------------------------------------
    def test_list_lots_enriches_with_price_and_gain_loss(self):
        self._add(purchase_price="10.00", quantity="5", purchase_date="2020-01-02")

        lots = self.service.list_lots(OWNER_A)
        self.assertEqual(len(lots), 1)
        lot = lots[0]
        self.assertEqual(lot["current_price"], Decimal("15.0"))
        self.assertEqual(lot["gain_loss"], Decimal("25.00"))
        self.assertEqual(lot["gain_loss_pct"], Decimal("50.00"))
        self.assertGreater(lot["days_held"], 0)
        self.assertIsNotNone(lot["dollars_per_day"])

    def test_list_lots_no_price_available_degrades_to_none(self):
        self._add(symbol="ZZTST3", purchase_price="10.00", quantity="1")

        lots = self.service.list_lots(OWNER_A)
        lot = next(p for p in lots if p["symbol"] == "ZZTST3")
        self.assertIsNone(lot["current_price"])
        self.assertIsNone(lot["gain_loss"])
        self.assertIsNone(lot["gain_loss_pct"])

    def test_symbol_rows_groups_and_totals_by_symbol(self):
        self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-02")
        self._add(symbol="ZZTEST", purchase_price="12.00", quantity="5", purchase_date="2020-02-02")
        self._add(symbol="ZZTST2", purchase_price="20.00", quantity="2", purchase_date="2020-01-02")

        rows = self.service.symbol_rows(OWNER_A)
        self.assertEqual([r["symbol"] for r in rows], ["ZZTEST", "ZZTST2"])

        zztest = rows[0]
        self.assertEqual(len(zztest["lots"]), 2)
        self.assertEqual(zztest["total_investment"], Decimal("110.00"))
        self.assertEqual(zztest["total_current_value"], Decimal("150.00"))
        self.assertEqual(zztest["total_gain_loss"], Decimal("40.00"))

    def test_symbol_rows_carry_the_allocation_bar_segments(self):
        """Issue #147 Part A: the chart consumes the same response the page
        already fetches, so the segments have to arrive on the row itself."""
        self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-02")

        row = self.service.symbol_rows(OWNER_A)[0]
        self.assertEqual(row["bar_base"], row["total_investment"])
        self.assertEqual(row["bar_gain"], Decimal("25.00"))
        self.assertEqual(row["bar_loss"], Decimal("0.00"))

    def test_symbol_rows_allocation_segments_are_none_without_a_price(self):
        self._add(symbol="ZZTST3", purchase_price="10.00", quantity="1", purchase_date="2020-01-02")

        row = next(r for r in self.service.symbol_rows(OWNER_A) if r["symbol"] == "ZZTST3")
        # summary_totals reports zeroes for a symbol with no priced lot; the
        # bar must be absent instead, or the chart draws a flat bar that reads
        # as a worthless position rather than an unknown one.
        self.assertEqual(row["total_investment"], Decimal("0.00"))
        self.assertIsNone(row["bar_base"])
        self.assertIsNone(row["bar_gain"])
        self.assertIsNone(row["bar_loss"])

    def test_portfolio_totals_aggregates_across_symbols(self):
        self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-02")
        self._add(symbol="ZZTST2", purchase_price="20.00", quantity="2", purchase_date="2020-01-02")

        rows = self.service.symbol_rows(OWNER_A)
        totals = self.service.portfolio_totals(rows)

        self.assertEqual(totals["total_investment"], Decimal("90.00"))
        self.assertEqual(totals["total_current_value"], Decimal("125.00"))
        self.assertEqual(totals["total_gain_loss"], Decimal("35.00"))

    def test_symbol_rows_period_returns_from_history(self):
        import pandas as pd

        history = pd.DataFrame({"Close": [10.0] * 5 + [15.0]})
        self.prices = FakePrices(
            quotes={"ZZTEST": 15.0}, histories={"ZZTEST": history}
        )
        self.service = PortfolioService(
            PortfolioRepository(), prices=self.prices, options=self.options
        )
        self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-02")

        rows = self.service.symbol_rows(OWNER_A)
        zztest = rows[0]
        self.assertIn("return_7d", zztest)
        self.assertIn("return_30d", zztest)
        self.assertIn("return_90d", zztest)

    def test_symbol_rows_history_fetch_failure_degrades_to_none(self):
        class BoomPrices(FakePrices):
            def get_history(self, symbol, interval="1d", days=365):
                raise RuntimeError("boom")

        self.prices = BoomPrices(quotes={"ZZTEST": 15.0})
        self.service = PortfolioService(
            PortfolioRepository(), prices=self.prices, options=self.options
        )
        self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-02")

        rows = self.service.symbol_rows(OWNER_A)
        zztest = rows[0]
        self.assertIsNone(zztest["return_7d"])
        self.assertIsNone(zztest["return_30d"])
        self.assertIsNone(zztest["return_90d"])

    def test_symbol_rows_mm_hedge_bias_none_without_options_service(self):
        self.service = PortfolioService(PortfolioRepository(), prices=self.prices, options=None)
        self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-02")

        rows = self.service.symbol_rows(OWNER_A)
        self.assertIsNone(rows[0]["mm_hedge_bias"])

    def test_symbol_rows_mm_hedge_bias_uses_latest_history_row(self):
        self.options = FakeOptions(histories={
            "ZZTEST": [
                {"mm_hedge_bias": "long", "captured_at": "2026-01-01"},
                {"mm_hedge_bias": "short", "captured_at": "2026-01-02"},
            ]
        })
        self.service = PortfolioService(
            PortfolioRepository(), prices=self.prices, options=self.options
        )
        self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-02")

        rows = self.service.symbol_rows(OWNER_A)
        self.assertEqual(rows[0]["mm_hedge_bias"], "short")
        self.assertEqual(rows[0]["mm_hedge_bias_captured_at"], "2026-01-02")

    def test_update_lot_patches_field(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5")
        self.assertTrue(self.service.update_lot(OWNER_A, lot_id, notes="corrected"))

        lots = self.service.list_positions(OWNER_A)
        self.assertEqual(lots[0]["notes"], "corrected")

    def test_update_lot_zero_purchase_price_raises_value_error(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5")
        with self.assertRaises(ValueError):
            self.service.update_lot(OWNER_A, lot_id, purchase_price="0")

    def test_update_lot_negative_quantity_raises_value_error(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5")
        with self.assertRaises(ValueError):
            self.service.update_lot(OWNER_A, lot_id, quantity="-1")

    def test_update_lot_invalid_trade_date_raises_value_error(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5")
        with self.assertRaises(ValueError):
            self.service.update_lot(OWNER_A, lot_id, trade_date="not-a-date")

    def test_update_lot_partial_patch_without_required_fields_succeeds(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5")
        self.assertTrue(self.service.update_lot(OWNER_A, lot_id, notes="ok"))

    def test_delete_lot_removes_entry(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5")
        self.assertTrue(self.service.delete_lot(OWNER_A, lot_id))
        self.assertEqual(self.service.list_positions(OWNER_A), [])

    def test_close_lot_full_close(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="10")

        result = self.service.close_lot(
            OWNER_A, lot_id, shares="10", sale_price="15.00",
            sale_trade_date="2026-03-01",
        )
        self.assertEqual(result["symbol"], "ZZTEST")
        self.assertEqual(len(result["allocations"]), 1)
        self.assertIsNone(result["allocations"][0]["child_lot_id"])

        open_lots = self.service.list_positions(OWNER_A)
        self.assertEqual(open_lots, [])

    def test_close_lot_partial_close_creates_child(self):
        lot_id = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="10")

        result = self.service.close_lot(
            OWNER_A, lot_id, shares="4", sale_price="15.00",
            sale_trade_date="2026-03-01",
        )
        child_lot_id = result["allocations"][0]["child_lot_id"]
        self.assertIsNotNone(child_lot_id)

        open_lots = self.service.list_positions(OWNER_A)
        self.assertEqual(len(open_lots), 1)
        self.assertEqual(open_lots[0]["lot_id"], child_lot_id)
        self.assertEqual(open_lots[0]["quantity"], Decimal("6"))
        self.assertEqual(open_lots[0]["parent_lot_id"], lot_id)

    def test_close_lot_spans_multiple_lots_fifo(self):
        lot1 = self._add(symbol="ZZTEST", purchase_price="10.00", quantity="5", purchase_date="2020-01-01")
        self._add(symbol="ZZTEST", purchase_price="12.00", quantity="5", purchase_date="2020-02-01")

        result = self.service.close_lot(
            OWNER_A, lot1, shares="8", sale_price="15.00",
            sale_trade_date="2026-03-01", method="FIFO",
        )
        self.assertEqual(len(result["allocations"]), 2)

        open_lots = self.service.list_positions(OWNER_A)
        self.assertEqual(len(open_lots), 1)
        self.assertEqual(open_lots[0]["quantity"], Decimal("2"))

    def test_close_lot_unknown_lot_raises(self):
        with self.assertRaises(ValueError):
            self.service.close_lot(
                OWNER_A, 999999, shares="1", sale_price="15.00",
                sale_trade_date="2026-03-01",
            )


class AllSymbolsTest(unittest.TestCase):
    """Half of the "tracked" roster the fundamentals views scope to (#147 B4).

    Repository-mocked rather than DB-backed: what's being pinned is the walk
    across owners, and the SQL underneath it is already covered above.
    """

    class Repo:
        def __init__(self, by_owner):
            self._by_owner = by_owner

        def list_owners(self):
            return list(self._by_owner)

        def list_positions(self, owner):
            return [{"symbol": s} for s in self._by_owner[owner]]

    def test_walks_every_owner_and_dedupes(self):
        # Other owners have no daily job of their own, so their symbols are part
        # of the tracked universe too (issue #126 decision #5).
        service = PortfolioService(
            self.Repo({"john": ["AAA", "BBB"], "thomas": ["BBB", "CCC"]}),
            prices=FakePrices(),
        )
        self.assertEqual(service.all_symbols(), ["AAA", "BBB", "CCC"])

    def test_no_positions_is_an_empty_roster(self):
        service = PortfolioService(self.Repo({}), prices=FakePrices())
        self.assertEqual(service.all_symbols(), [])


class RecordingHarvesterRepo:
    """Stand-in for HarvesterPlanDB, recording the exit seam's calls.

    The SQL those two methods run is pinned in test_harvester_repository_db;
    what is being pinned here is *when* the portfolio service reaches for them.
    """

    def __init__(self, plan_ids=None, raises=False):
        self._plan_ids = plan_ids or {}
        self._raises = raises
        self.closed = []
        self.id_lookups = 0

    def close_active_plan_for_symbol(self, ticker, *, owner, reason=None):
        if self._raises:
            raise RuntimeError("harvester is down")
        self.closed.append((ticker, owner, reason))
        return 1

    def active_plan_ids(self, *, owner):
        self.id_lookups += 1
        if self._raises:
            raise RuntimeError("harvester is down")
        return dict(self._plan_ids)


class PlanExitSeamTest(unittest.TestCase):
    """Issue #147 Part H5 — the exit half of the holding invariant: going flat
    on a symbol closes the ladder that was selling into it.
    """

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.harvester = RecordingHarvesterRepo()
        self.service = self._service(self.harvester)

    def _service(self, harvester):
        return PortfolioService(
            PortfolioRepository(),
            prices=FakePrices(quotes={"ZZTEST": 15.0}),
            harvester_repository=harvester,
        )

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM positions WHERE owner IN (%s, %s)", (OWNER_A, OWNER_B)
            )
            conn.commit()

    def _add(self, quantity="10", symbol="ZZTEST"):
        self.service.add_position(
            OWNER_A, name="Test Lot", symbol=symbol, purchase_price="10.00",
            quantity=quantity, purchase_date="2020-01-02", currency="USD",
        )
        lots = [p for p in self.service.list_positions(OWNER_A) if p["symbol"] == symbol]
        return max(lots, key=lambda p: p["lot_id"])["lot_id"]

    def test_closing_the_last_open_lot_closes_the_ladder(self):
        lot_id = self._add(quantity="10")
        self.service.close_lot(
            OWNER_A, lot_id, shares="10", sale_price="15.00",
            sale_trade_date="2026-03-01",
        )
        self.assertEqual(
            self.harvester.closed, [("ZZTEST", OWNER_A, "last open lot closed")]
        )

    def test_a_partial_close_leaves_the_ladder_running(self):
        # The off-by-one that matters: shares remain, so the plan still has
        # something to sell. Closing it here would silently kill a live ladder
        # in the middle of a harvest.
        lot_id = self._add(quantity="10")
        self.service.close_lot(
            OWNER_A, lot_id, shares="4", sale_price="15.00",
            sale_trade_date="2026-03-01",
        )
        self.assertEqual(self.harvester.closed, [])

    def test_a_second_lot_still_open_leaves_the_ladder_running(self):
        first = self._add(quantity="5")
        self._add(quantity="5")
        self.service.close_lot(
            OWNER_A, first, shares="5", sale_price="15.00",
            sale_trade_date="2026-03-01",
        )
        self.assertEqual(self.harvester.closed, [])

    def test_deleting_the_last_lot_closes_the_ladder(self):
        lot_id = self._add()
        self.assertTrue(self.service.delete_lot(OWNER_A, lot_id))
        self.assertEqual(
            self.harvester.closed, [("ZZTEST", OWNER_A, "last lot deleted")]
        )

    def test_removing_the_position_closes_the_ladder(self):
        self._add()
        self.assertEqual(self.service.remove_position(OWNER_A, "ZZTEST"), 1)
        self.assertEqual(
            self.harvester.closed, [("ZZTEST", OWNER_A, "position removed")]
        )

    def test_a_broken_harvester_does_not_undo_the_write_that_committed(self):
        # The sale is already committed on its own connection; a harvester
        # failure must not surface as a failed sale. The orphan flag is the
        # backstop for what didn't get closed.
        service = self._service(RecordingHarvesterRepo(raises=True))
        self.service = service
        lot_id = self._add(quantity="10")
        result = service.close_lot(
            OWNER_A, lot_id, shares="10", sale_price="15.00",
            sale_trade_date="2026-03-01",
        )
        self.assertEqual(result["symbol"], "ZZTEST")
        self.assertEqual(service.list_positions(OWNER_A), [])

    def test_without_a_harvester_repository_nothing_is_attempted(self):
        service = PortfolioService(PortfolioRepository(), prices=FakePrices())
        self.service = service
        lot_id = self._add()
        self.assertTrue(service.delete_lot(OWNER_A, lot_id))

    # -- the Plan chip's id (issue #147 Part H6) ---------------------------
    def test_symbol_rows_carry_the_active_plan_id(self):
        service = self._service(RecordingHarvesterRepo(plan_ids={"ZZTEST": 42}))
        self.service = service
        self._add()
        rows = {r["symbol"]: r for r in service.symbol_rows(OWNER_A)}
        self.assertEqual(rows["ZZTEST"]["active_plan_id"], 42)

    def test_symbol_rows_degrade_to_no_chip_when_the_lookup_fails(self):
        # A harvester outage costs the chip, not the portfolio table.
        service = self._service(RecordingHarvesterRepo(raises=True))
        self.service = service
        self._add()
        rows = service.symbol_rows(OWNER_A)
        self.assertIsNone(rows[0]["active_plan_id"])

    def test_symbol_rows_look_the_plan_ids_up_once_not_per_row(self):
        harvester = RecordingHarvesterRepo(plan_ids={"ZZTEST": 42})
        service = self._service(harvester)
        self.service = service
        self._add(symbol="ZZTEST")
        self._add(symbol="ZZTST2")
        service.symbol_rows(OWNER_A)
        self.assertEqual(harvester.id_lookups, 1)


if __name__ == "__main__":
    unittest.main()
