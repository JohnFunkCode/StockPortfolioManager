"""WatchlistService normalization and policy (issue #83).

Driven against a fake repository: the real SQL is covered by
tests/test_watchlist_repository.py, and what matters here is what the service
decides — normalization, the duplicate policy, and full-sync import.
"""
import os
import tempfile
import time
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


class ExplodingYFinance:
    """Any attribute touched is a network call that should not have happened."""

    def __init__(self):
        self.touched = []

    def __getattr__(self, name):
        self.touched.append(name)
        raise AssertionError(
            f"returns_and_fundamentals reached yfinance via {name}() — it must "
            "read cache only"
        )


class CountingOhlcvRepository:
    """Counts SQL round trips and serves bars from an in-memory dict."""

    def __init__(self, bars_by_symbol):
        self._bars = bars_by_symbol
        self.queries = 0

    def daily_bars_for_symbols(self, symbols):
        self.queries += 1
        rows = []
        for sym in symbols:
            for ts, close in self._bars.get(sym, []):
                rows.append({"symbol": sym, "ts": ts, "close": close,
                             "volume": 1, "high": close, "low": close, "open": close})
        return rows


class CountingFundamentalsRepository:
    def __init__(self, by_type=None):
        self._by_type = by_type or {}
        self.queries = 0
        self.requested = []

    def get_all_latest(self, data_type):
        self.queries += 1
        self.requested.append(data_type)
        return self._by_type.get(data_type, [])

    def ttl_seconds(self):
        return 86400.0   # env read, not a query


class ListingRepository(FakeRepository):
    def __init__(self, entries):
        super().__init__()
        self._entries = entries
        self.queries = 0

    def list_entries(self):
        self.queries += 1
        return list(self._entries)


class WatchlistReturnsAndFundamentalsTest(unittest.TestCase):
    """Issue #147 Part B3.

    The value of this method is entirely in its cost — the report script it
    replaces did the same work with two network calls *per symbol*. So the load
    profile is what's asserted here, not just the output shape: a version that
    loops per symbol would still return correct rows and must still fail.
    """

    DAY = 86400
    # 2026-08-10 as an epoch second, and a year of daily bars behind it.
    LAST_TS = 1786665600

    def _bars(self, start_price, end_price, n=400):
        step = (end_price - start_price) / max(1, n - 1)
        first_ts = self.LAST_TS - (n - 1) * self.DAY
        return [(first_ts + i * self.DAY, start_price + i * step) for i in range(n)]

    def _build(self, symbols, scores=None):
        from quantcore.services.fundamentals import FundamentalsService
        from quantcore.services.prices import PricesService

        entries = [
            {"symbol": s, "name": f"{s} Inc", "currency": "USD", "tags": ["chips"]}
            for s in symbols
        ]
        self.repo = ListingRepository(entries)
        self.ohlcv = CountingOhlcvRepository({s: self._bars(100.0, 150.0) for s in symbols})
        self.fund_repo = CountingFundamentalsRepository(
            {"fundamental_score": scores or []}
        )
        self.yf = ExplodingYFinance()

        prices = PricesService(
            ohlcv_repository=self.ohlcv,
            yfinance_gateway=self.yf,
            options_repository=None,
            sentiment_repository=None,
        )
        fundamentals = FundamentalsService(
            fundamentals_repository=self.fund_repo,
            yfinance_gateway=self.yf,
        )
        return WatchlistService(self.repo, prices=prices, fundamentals=fundamentals)

    def test_six_queries_and_no_yfinance_regardless_of_list_size(self):
        for size in (1, 3, 25):
            with self.subTest(symbols=size):
                svc = self._build([f"SYM{i}" for i in range(size)])
                result = svc.returns_and_fundamentals()

                total = self.repo.queries + self.ohlcv.queries + self.fund_repo.queries
                self.assertEqual(total, 6, f"{size} symbols cost {total} queries")
                self.assertEqual(self.ohlcv.queries, 1)
                self.assertEqual(self.fund_repo.queries, 4)
                self.assertEqual(self.yf.touched, [])
                self.assertEqual(result["count"], size)

    def test_queries_the_four_expected_fundamentals_data_types(self):
        svc = self._build(["NVDA"])
        svc.returns_and_fundamentals()
        self.assertEqual(
            sorted(self.fund_repo.requested),
            ["earnings_acceleration", "earnings_calendar", "fundamental_score",
             "revenue_growth"],
        )

    def test_row_carries_returns_and_scored_fundamentals(self):
        svc = self._build(
            ["NVDA"],
            scores=[{
                "symbol": "NVDA",
                "composite_score": 82.5,
                "fundamental_label": "Strong",
                "coverage": 0.9,
                "sector": "Technology",
                "market_cap": 3_000_000_000_000,
                "metric_scores": {"RevCAGR3Y": {"value": 45.0, "score": 9.0}},
                "fetched_at": "2026-08-10T00:00:00Z",
                "_fetched_at_ts": int(time.time()),
            }],
        )
        row = svc.returns_and_fundamentals()["rows"][0]

        self.assertEqual(row["symbol"], "NVDA")
        self.assertEqual(row["composite_score"], 82.5)
        self.assertEqual(row["rev_cagr_3y"], 45.0)
        self.assertEqual(row["rev_cagr_score"], 9.0)
        self.assertEqual(row["market_cap_usd"], 3_000_000_000_000)
        self.assertFalse(row["fundamentals_stale"])
        # Rising series, so every return window is positive and present.
        for key in ("return_5d", "return_30d", "return_60d", "return_ytd", "return_1y"):
            self.assertIsNotNone(row[key], key)
            self.assertGreater(row[key], 0, key)

    def test_unscored_symbol_is_kept_with_null_fundamentals(self):
        """Dropping it would read as "we stopped watching that"."""
        svc = self._build(["NVDA"])
        result = svc.returns_and_fundamentals()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["unscored_count"], 1)
        row = result["rows"][0]
        self.assertIsNone(row["composite_score"])
        self.assertIsNone(row["fundamentals_stale"])
        self.assertIsNotNone(row["price"])

    def test_stale_fundamentals_are_labelled_not_dropped(self):
        old_ts = int(time.time()) - 10 * 86400
        svc = self._build(
            ["NVDA"],
            scores=[{"symbol": "NVDA", "composite_score": 50.0,
                     "fetched_at": "2026-07-31T00:00:00Z", "_fetched_at_ts": old_ts}],
        )
        result = svc.returns_and_fundamentals()
        row = result["rows"][0]

        self.assertEqual(result["stale_count"], 1)
        self.assertTrue(row["fundamentals_stale"])
        self.assertGreater(row["fundamentals_age_hours"], 200)
        self.assertEqual(row["composite_score"], 50.0)

    def test_scored_symbols_sort_above_unscored(self):
        svc = self._build(
            ["AAA", "BBB", "CCC"],
            scores=[
                {"symbol": "CCC", "composite_score": 40.0, "_fetched_at_ts": int(time.time())},
                {"symbol": "AAA", "composite_score": 90.0, "_fetched_at_ts": int(time.time())},
            ],
        )
        order = [r["symbol"] for r in svc.returns_and_fundamentals()["rows"]]
        self.assertEqual(order, ["AAA", "CCC", "BBB"])

    def test_without_injected_services_it_refuses_rather_than_half_answering(self):
        svc = WatchlistService(FakeRepository())
        with self.assertRaises(RuntimeError):
            svc.returns_and_fundamentals()


if __name__ == "__main__":
    unittest.main()
