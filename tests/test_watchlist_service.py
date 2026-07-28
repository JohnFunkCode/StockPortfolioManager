"""WatchlistService normalization and policy (issue #83).

Driven against a fake repository: the real SQL is covered by
tests/test_watchlist_repository.py, and what matters here is what the service
decides — normalization, the duplicate policy, and full-sync import.
"""
import os
import tempfile
import unittest

from quantcore.services.portfolio import DuplicateSymbolError
from quantcore.services.watchlist import WatchlistService


class FakeRepository:
    """Records calls and mimics the repository's return contract."""

    def __init__(self, taken=()):
        self.added = []
        self.replaced = None
        self.removed = []
        self._taken = set(taken)

    def add_entry(self, symbol, name=None, currency="USD", tags=None, added_by=None):
        self.added.append({
            "symbol": symbol, "name": name, "currency": currency,
            "tags": tags, "added_by": added_by,
        })
        if symbol in self._taken:
            return None          # ON CONFLICT DO NOTHING suppressed the insert
        self._taken.add(symbol)
        return len(self.added)

    def remove_entry(self, symbol):
        self.removed.append(symbol)
        return 1 if symbol.upper() in self._taken else 0

    def replace_all(self, rows):
        self.replaced = rows
        return len(rows)

    def list_entries(self):
        return []

    def count(self):
        return len(self._taken)


class WatchlistServiceTest(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepository()
        self.svc = WatchlistService(self.repo)

    # ---------------------------------------------------------------- add
    def test_add_normalizes_symbol_and_currency_to_upper(self):
        self.svc.add_entry(" nvda ", currency="usd")

        self.assertEqual(self.repo.added[0]["symbol"], "NVDA")
        self.assertEqual(self.repo.added[0]["currency"], "USD")

    def test_name_defaults_to_the_symbol(self):
        """Adding by ticker alone must not leave the front end a blank label."""
        self.svc.add_entry("nvda")

        self.assertEqual(self.repo.added[0]["name"], "NVDA")

    def test_blank_tags_are_dropped_and_survivors_stripped(self):
        self.svc.add_entry("NVDA", tags=[" ai ", "", "   ", "semis"])

        self.assertEqual(self.repo.added[0]["tags"], ["ai", "semis"])

    def test_added_by_is_passed_through_for_audit(self):
        self.svc.add_entry("NVDA", added_by="john")

        self.assertEqual(self.repo.added[0]["added_by"], "john")

    def test_empty_symbol_raises(self):
        for bad in ("", "   ", None):
            with self.subTest(symbol=bad):
                with self.assertRaises(ValueError):
                    self.svc.add_entry(bad)

    def test_duplicate_add_raises_duplicate_symbol_error(self):
        """The repository reports None; turning that into the class
        api/errors.py maps to 409 is this layer's decision.
        """
        self.svc.add_entry("NVDA")

        with self.assertRaises(DuplicateSymbolError):
            self.svc.add_entry("nvda")

    def test_duplicate_detection_is_case_insensitive(self):
        self.svc.add_entry("nvda")

        with self.assertRaises(DuplicateSymbolError):
            self.svc.add_entry("NVDA")

    def test_add_returns_the_normalized_symbol(self):
        self.assertEqual(self.svc.add_entry(" nvda ")["symbol"], "NVDA")

    # ------------------------------------------------------------- remove
    def test_remove_returns_the_rowcount(self):
        self.svc.add_entry("NVDA")

        self.assertEqual(self.svc.remove_entry("NVDA"), 1)
        self.assertEqual(self.svc.remove_entry("AAPL"), 0)

    def test_remove_with_no_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.svc.remove_entry("  ")

    # ------------------------------------------------------------- import
    def _write_yaml(self, text):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_import_yaml_normalizes_and_full_syncs(self):
        path = self._write_yaml(
            "- name: NVIDIA\n"
            "  symbol: nvda\n"
            "  currency: usd\n"
            "  tags: [ai, semis]\n"
            "- symbol: AAPL\n"
        )

        n = self.svc.import_yaml(path)

        self.assertEqual(n, 2)
        self.assertEqual(
            self.repo.replaced,
            [
                {"symbol": "NVDA", "name": "NVIDIA", "currency": "USD",
                 "tags": ["ai", "semis"], "added_by": None},
                {"symbol": "AAPL", "name": "AAPL", "currency": "USD",
                 "tags": [], "added_by": None},
            ],
        )

    def test_import_yaml_is_idempotent(self):
        """Full-sync/replace, so a re-run converges instead of accumulating."""
        path = self._write_yaml("- symbol: NVDA\n- symbol: AAPL\n")

        first = self.svc.import_yaml(path)
        first_rows = self.repo.replaced
        second = self.svc.import_yaml(path)

        self.assertEqual(first, second)
        self.assertEqual(first_rows, self.repo.replaced)

    def test_import_yaml_skips_entries_with_no_symbol(self):
        path = self._write_yaml("- name: Mystery\n- symbol: NVDA\n")

        with self.assertLogs("quantcore.services.watchlist", level="WARNING") as logs:
            n = self.svc.import_yaml(path)

        self.assertEqual(n, 1)
        self.assertEqual([r["symbol"] for r in self.repo.replaced], ["NVDA"])
        self.assertIn("skipped 1", logs.output[0])

    def test_import_yaml_of_an_empty_file_replaces_with_nothing(self):
        """Deliberate: import is a full sync. scripts/import_watchlist.py is
        where the "this would empty the capture universe" warning lives.
        """
        path = self._write_yaml("")

        self.assertEqual(self.svc.import_yaml(path), 0)
        self.assertEqual(self.repo.replaced, [])


if __name__ == "__main__":
    unittest.main()
