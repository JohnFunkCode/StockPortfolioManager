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

    def __init__(self, taken=(), entries=()):
        self.added = []
        self.replaced = None
        self.removed = []
        self.currency_writes = []
        self._taken = set(taken)
        self._entries = [dict(e) for e in entries]

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

    def set_currency(self, symbol, currency):
        self.currency_writes.append((symbol, currency))
        for entry in self._entries:
            if entry["symbol"] == symbol:
                entry["currency"] = currency
                return 1
        return 0

    def list_entries(self):
        return [dict(e) for e in self._entries]

    def count(self):
        return len(self._taken)


class FakeYFinance:
    """Stands in for YFinanceGateway.ticker_info."""

    def __init__(self, info_by_symbol=None, raises=False):
        self._info = info_by_symbol or {}
        self._raises = raises
        self.calls = []

    def ticker_info(self, symbol, timeout=15.0):
        self.calls.append(symbol)
        if self._raises:
            raise RuntimeError("yfinance is having a day")
        return self._info.get(symbol)


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


class WatchlistCurrencyTest(unittest.TestCase):
    """The currency is a looked-up fact, not a field the caller supplies.

    watchlist.yaml declared ASSA-B.ST, AUTO.OL and NIB.F as USD; they trade in
    SEK, NOK and EUR. Since the market cap on the fundamentals page is labelled
    with this value, a wrong one renders a foreign cap as dollars — worse than
    no cap at all, because it sorts and compares as if it were real.
    """

    def _service(self, info=None, raises=False, entries=()):
        self.repo = FakeRepository(entries=entries)
        self.yf = FakeYFinance(info, raises=raises)
        return WatchlistService(self.repo, yfinance=self.yf)

    # ---------------------------------------------------------------- add
    def test_the_exchange_beats_what_the_caller_passed(self):
        svc = self._service({"ASSA-B.ST": {"currency": "SEK"}})

        result = svc.add_entry("ASSA-B.ST", currency="USD")

        self.assertEqual(self.repo.added[0]["currency"], "SEK")
        self.assertEqual(result["currency"], "SEK")

    def test_the_override_is_logged_so_it_is_not_silent(self):
        svc = self._service({"AUTO.OL": {"currency": "NOK"}})

        with self.assertLogs("quantcore.services.watchlist", level="INFO") as logs:
            svc.add_entry("AUTO.OL", currency="usd")

        self.assertIn("NOK", logs.output[0])
        self.assertIn("USD", logs.output[0])

    def test_agreeing_with_the_caller_logs_nothing(self):
        svc = self._service({"NVDA": {"currency": "USD"}})

        with self.assertNoLogs("quantcore.services.watchlist", level="INFO"):
            svc.add_entry("NVDA", currency="USD")

    def test_no_currency_supplied_at_all_is_the_normal_case(self):
        svc = self._service({"7203.T": {"currency": "JPY"}})

        self.assertEqual(svc.add_entry("7203.T")["currency"], "JPY")

    def test_a_lookup_failure_falls_back_rather_than_refusing_the_add(self):
        """Yahoo being down is not a reason to refuse to watch a symbol —
        the entry lands with the supplied value and a warning."""
        svc = self._service(raises=True)

        with self.assertLogs("quantcore.services.watchlist", level="WARNING") as logs:
            result = svc.add_entry("NVDA", currency="usd")

        self.assertEqual(result["currency"], "USD")
        self.assertEqual(self.repo.added[0]["symbol"], "NVDA")
        self.assertTrue(any("falling back" in line for line in logs.output))

    def test_an_unknown_symbol_falls_back_to_usd(self):
        svc = self._service({})

        self.assertEqual(svc.add_entry("ZZNOSUCH")["currency"], "USD")

    def test_a_blank_currency_from_the_exchange_is_not_stored(self):
        svc = self._service({"NVDA": {"currency": "  "}})

        self.assertEqual(svc.add_entry("NVDA", currency="USD")["currency"], "USD")

    def test_no_gateway_means_no_lookup_and_no_warning(self):
        """The CRUD half stays constructible from a bare repository — import
        scripts and the repository tests build it that way, and for them the
        supplied value is the design rather than a degraded path."""
        svc = WatchlistService(FakeRepository())

        with self.assertNoLogs("quantcore.services.watchlist", level="WARNING"):
            result = svc.add_entry("NVDA", currency="eur")

        self.assertEqual(result["currency"], "EUR")

    # ------------------------------------------------------------- resync
    ENTRIES = (
        {"symbol": "ASSA-B.ST", "currency": "USD"},
        {"symbol": "NVDA", "currency": "USD"},
        {"symbol": "ZZNOSUCH", "currency": "USD"},
    )
    INFO = {"ASSA-B.ST": {"currency": "SEK"}, "NVDA": {"currency": "USD"}}

    def test_resync_reports_the_diff_without_writing(self):
        svc = self._service(self.INFO, entries=self.ENTRIES)

        results = {r["symbol"]: r for r in svc.resync_currencies()}

        self.assertTrue(results["ASSA-B.ST"]["changed"])
        self.assertFalse(results["ASSA-B.ST"]["updated"])
        self.assertFalse(results["NVDA"]["changed"])
        self.assertEqual(self.repo.currency_writes, [], "dry run writes nothing")

    def test_resync_apply_writes_only_what_changed(self):
        svc = self._service(self.INFO, entries=self.ENTRIES)

        svc.resync_currencies(apply=True)

        self.assertEqual(self.repo.currency_writes, [("ASSA-B.ST", "SEK")])

    def test_resync_leaves_an_unresolvable_symbol_alone(self):
        """A failed lookup is not evidence the stored value is wrong."""
        svc = self._service(self.INFO, entries=self.ENTRIES)

        [row] = [r for r in svc.resync_currencies(apply=True)
                 if r["symbol"] == "ZZNOSUCH"]

        self.assertIsNone(row["resolved"])
        self.assertFalse(row["changed"])
        self.assertEqual(row["stored"], "USD")

    def test_resync_can_be_scoped_to_a_subset(self):
        svc = self._service(self.INFO, entries=self.ENTRIES)

        results = svc.resync_currencies(symbols=["assa-b.st"])

        self.assertEqual([r["symbol"] for r in results], ["ASSA-B.ST"])
        self.assertEqual(self.yf.calls, ["ASSA-B.ST"], "no lookups for the rest")


if __name__ == "__main__":
    unittest.main()
