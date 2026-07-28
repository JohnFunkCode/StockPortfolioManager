"""WatchlistRepository round-trips against a real database (issue #83).

The repository is the riskiest code in this branch — it is the first array
column in the schema and the first table with no `owner` scoping — so these
tests hit real SQL rather than a fake connection.

The watchlist is global, so `replace_all` truncates whatever the configured
database holds. This class snapshots the full table (including `added_by`,
which `list_entries` does not return) and restores it on teardown, so running
the suite against a populated test database is non-destructive.

The snapshot/restore is per-class, not per-test: restoring is one INSERT per
row, and against a seeded database (227 entries) doing that after every test
put the suite in the minutes. Individual tests already filter by their own
synthetic symbols, so they do not need the real rows present in between.
"""
import unittest
from contextlib import closing

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.watchlist_repository import WatchlistRepository  # noqa: E402

# Synthetic symbols that won't collide with anything real.
SYMBOL = "ZZWL1"
OTHER = "ZZWL2"

SQL_SNAPSHOT = """
SELECT s.ticker AS symbol, w.name AS name, w.currency AS currency,
       w.tags AS tags, w.added_by AS added_by
FROM watchlist w
JOIN symbols s ON s.symbol_id = w.symbol_id
ORDER BY s.ticker;
"""


class WatchlistRepositoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = WatchlistRepository()
        with closing(get_connection()) as conn:
            cls._saved = [dict(r) for r in conn.execute(SQL_SNAPSHOT).fetchall()]

    @classmethod
    def tearDownClass(cls):
        cls.repo.replace_all(cls._saved)

    def setUp(self):
        self._purge()

    def _purge(self):
        for sym in (SYMBOL, OTHER):
            self.repo.remove_entry(sym)

    # ------------------------------------------------------------------
    def test_add_list_remove_round_trip(self):
        entry_id = self.repo.add_entry(
            SYMBOL, name="Test Co", currency="USD", tags=["ai", "semis"],
            added_by="tester",
        )
        self.assertIsInstance(entry_id, int)

        [entry] = [e for e in self.repo.list_entries() if e["symbol"] == SYMBOL]
        self.assertEqual(entry["name"], "Test Co")
        self.assertEqual(entry["currency"], "USD")
        self.assertEqual(entry["tags"], ["ai", "semis"])

        self.assertEqual(self.repo.remove_entry(SYMBOL), 1)
        self.assertEqual([e for e in self.repo.list_entries() if e["symbol"] == SYMBOL], [])

    def test_tags_survive_the_round_trip_as_a_list(self):
        """The schema's first TEXT[] column: psycopg2 must hand back a list,
        not the '{a,b}' literal a naive TEXT column would produce.
        """
        self.repo.add_entry(SYMBOL, tags=["one", "two", "three"])

        [entry] = [e for e in self.repo.list_entries() if e["symbol"] == SYMBOL]
        self.assertIsInstance(entry["tags"], list)
        self.assertEqual(entry["tags"], ["one", "two", "three"])

    def test_no_tags_is_an_empty_list_not_null(self):
        self.repo.add_entry(SYMBOL)

        [entry] = [e for e in self.repo.list_entries() if e["symbol"] == SYMBOL]
        self.assertEqual(entry["tags"], [])

    def test_duplicate_add_returns_none_rather_than_raising(self):
        """The UNIQUE constraint makes "already watching" a database
        invariant. The repository reports the conflict; deciding it means 409
        is the service's job.
        """
        first = self.repo.add_entry(SYMBOL, name="First")
        second = self.repo.add_entry(SYMBOL, name="Second")

        self.assertIsInstance(first, int)
        self.assertIsNone(second)

        matches = [e for e in self.repo.list_entries() if e["symbol"] == SYMBOL]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["name"], "First", "conflict must not overwrite")

    def test_remove_missing_symbol_returns_zero(self):
        self.assertEqual(self.repo.remove_entry("ZZNOSUCH"), 0)

    def test_row_shape_matches_the_legacy_yaml_loader(self):
        """api/deps.load_watchlist() emitted these exact keys, including the
        None placeholders that let GET /api/securities merge watchlist rows
        with portfolio rows into one list.
        """
        self.repo.add_entry(SYMBOL, name="Test Co", currency="usd", tags=["x"])

        [entry] = [e for e in self.repo.list_entries() if e["symbol"] == SYMBOL]
        self.assertEqual(
            set(entry),
            {"name", "symbol", "currency", "purchase_price", "quantity",
             "purchase_date", "sale_price", "sale_date", "source", "tags"},
        )
        self.assertEqual(entry["source"], "watchlist")
        self.assertEqual(entry["currency"], "USD")
        for key in ("purchase_price", "quantity", "purchase_date",
                    "sale_price", "sale_date"):
            self.assertIsNone(entry[key])

    def test_list_is_ordered_by_symbol(self):
        """Inserted out of order, returned in order.

        Asserted against a controlled two-row table rather than whatever the
        database holds: PostgreSQL's collation ignores punctuation at the
        primary level, so a real seeded list sorts 'TE.PA' after 'TENB' while
        Python's code-point ``sorted()`` does the opposite. That difference is
        the collation, not an ordering bug, and it should not fail this test.
        """
        self.repo.replace_all([{"symbol": OTHER}, {"symbol": SYMBOL}])

        symbols = [e["symbol"] for e in self.repo.list_entries()]
        self.assertEqual(symbols, [SYMBOL, OTHER])

    def test_replace_all_is_a_full_sync(self):
        self.repo.add_entry(SYMBOL, name="Doomed")

        inserted = self.repo.replace_all([
            {"symbol": OTHER, "name": "Survivor", "currency": "USD", "tags": ["kept"]},
        ])

        self.assertEqual(inserted, 1)
        entries = self.repo.list_entries()
        self.assertEqual([e["symbol"] for e in entries], [OTHER])
        self.assertEqual(entries[0]["tags"], ["kept"])

    def test_replace_all_collapses_duplicate_symbols(self):
        """A YAML file listing a symbol twice imports it once instead of
        failing the whole import on the UNIQUE constraint.
        """
        inserted = self.repo.replace_all([
            {"symbol": SYMBOL, "name": "First"},
            {"symbol": SYMBOL, "name": "Second"},
        ])

        self.assertEqual(inserted, 1)
        self.assertEqual([e["symbol"] for e in self.repo.list_entries()], [SYMBOL])

    def test_replace_all_skips_rows_with_no_symbol(self):
        """A blank symbol is dropped rather than registered as an empty ticker
        in `symbols`, and is not counted as imported.
        """
        inserted = self.repo.replace_all([
            {"symbol": SYMBOL},
            {"symbol": "   "},
            {"name": "no symbol key at all"},
        ])

        self.assertEqual(inserted, 1)
        self.assertEqual([e["symbol"] for e in self.repo.list_entries()], [SYMBOL])

    def test_count_tracks_the_table(self):
        before = self.repo.count()
        self.repo.add_entry(SYMBOL)
        self.assertEqual(self.repo.count(), before + 1)
        self.repo.remove_entry(SYMBOL)
        self.assertEqual(self.repo.count(), before)


if __name__ == "__main__":
    unittest.main()
