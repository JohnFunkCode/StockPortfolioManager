"""FundamentalsService.cache_freshness — the warmer's ordering primitive (#147, Part E).

A cold pass over the universe is ~10 serial yfinance calls per symbol and will
not finish inside a Cloud Run Job's timeout, so the daily warmer refreshes
oldest-first under a wall-clock budget. That makes the *order* this method
returns load-bearing, not cosmetic: get it wrong and the same handful of
symbols get refreshed every night while the tail never does.
"""
import time
import unittest

from quantcore.services.fundamentals import FundamentalsService


class _FakeRepo:
    """Just the two methods cache_freshness touches."""

    def __init__(self, entries, ttl_seconds=86400):
        self._entries = entries
        self._ttl = ttl_seconds
        self.requested_data_types = []

    def get_all_latest(self, data_type):
        self.requested_data_types.append(data_type)
        return list(self._entries)

    def ttl_seconds(self):
        return self._ttl


def _service(entries, ttl_seconds=86400):
    return FundamentalsService(_FakeRepo(entries, ttl_seconds), yfinance_gateway=None)


def _entry(symbol, age_seconds):
    return {"symbol": symbol, "_fetched_at_ts": int(time.time()) - age_seconds}


class CacheFreshnessOrderingTest(unittest.TestCase):
    def test_never_fetched_symbols_come_first(self):
        """They have unbounded age, and they are the rows that make a page look
        broken rather than merely stale."""
        svc = _service([_entry("AAPL", 3600), _entry("MSFT", 7200)])

        result = svc.cache_freshness(["AAPL", "NVDA", "MSFT"])

        self.assertEqual(result["symbols"][0]["symbol"], "NVDA")
        self.assertIsNone(result["symbols"][0]["age_seconds"])
        self.assertIsNone(result["symbols"][0]["fetched_at"])

    def test_cached_symbols_are_ordered_oldest_first(self):
        svc = _service([
            _entry("AAPL", 3600),
            _entry("MSFT", 900_000),
            _entry("NVDA", 100_000),
        ])

        result = svc.cache_freshness(["AAPL", "MSFT", "NVDA"])

        self.assertEqual(
            [row["symbol"] for row in result["symbols"]],
            ["MSFT", "NVDA", "AAPL"],
        )

    def test_symbols_are_uppercased_before_matching_the_cache(self):
        svc = _service([_entry("AAPL", 3600)])

        result = svc.cache_freshness(["aapl"])

        self.assertEqual(result["symbols"][0]["symbol"], "AAPL")
        self.assertFalse(result["symbols"][0]["stale"])


class CacheFreshnessHealthTest(unittest.TestCase):
    """The same numbers back the post-run staleness alarm."""

    def test_coverage_counts_only_symbols_inside_the_ttl(self):
        svc = _service(
            [_entry("AAPL", 100), _entry("MSFT", 100), _entry("NVDA", 999_999)],
            ttl_seconds=86400,
        )

        result = svc.cache_freshness(["AAPL", "MSFT", "NVDA", "AMD"])

        self.assertEqual(result["requested"], 4)
        self.assertEqual(result["fresh_count"], 2)
        self.assertEqual(result["stale_count"], 2)  # NVDA aged out, AMD never fetched
        self.assertEqual(result["never_fetched_count"], 1)
        self.assertEqual(result["coverage"], 0.5)

    def test_reports_on_the_symbols_asked_about_not_the_cache_contents(self):
        """A symbol added to the watchlist this morning is a coverage hole; a
        cache-only view (which is what every other ranking method uses) would
        never see it."""
        svc = _service([_entry("AAPL", 100), _entry("TSLA", 100)])

        result = svc.cache_freshness(["AAPL", "AMD"])

        self.assertEqual([row["symbol"] for row in result["symbols"]], ["AMD", "AAPL"])
        self.assertEqual(result["requested"], 2)

    def test_oldest_age_ignores_never_fetched_symbols(self):
        """They have no age to report; never_fetched_count is how they show up."""
        svc = _service([_entry("AAPL", 5000)])

        result = svc.cache_freshness(["AAPL", "AMD"])

        self.assertAlmostEqual(result["oldest_age_seconds"], 5000, delta=2)

    def test_oldest_age_is_none_when_nothing_was_ever_fetched(self):
        result = _service([]).cache_freshness(["AAPL"])

        self.assertIsNone(result["oldest_age_seconds"])
        self.assertEqual(result["coverage"], 0.0)

    def test_an_empty_universe_counts_as_fully_covered(self):
        """Nothing requested is not zero covered -- an empty universe is the
        empty-watchlist alarm's problem, and double-alarming on it would train
        people to ignore both."""
        result = _service([]).cache_freshness([])

        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["symbols"], [])

    def test_a_zero_ttl_disables_staleness_by_age(self):
        svc = _service([_entry("AAPL", 10_000_000)], ttl_seconds=0)

        result = svc.cache_freshness(["AAPL"])

        self.assertFalse(result["symbols"][0]["stale"])

    def test_a_never_fetched_symbol_is_stale_under_a_zero_ttl_too(self):
        svc = _service([], ttl_seconds=0)

        result = svc.cache_freshness(["AAPL"])

        self.assertTrue(result["symbols"][0]["stale"])

    def test_data_type_is_configurable_and_defaults_to_fundamental_score(self):
        repo = _FakeRepo([])
        svc = FundamentalsService(repo, yfinance_gateway=None)

        svc.cache_freshness(["AAPL"])
        svc.cache_freshness(["AAPL"], data_type="earnings_calendar")

        self.assertEqual(
            repo.requested_data_types, ["fundamental_score", "earnings_calendar"]
        )


if __name__ == "__main__":
    unittest.main()
