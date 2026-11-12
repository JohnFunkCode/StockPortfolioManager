"""The live 1d columns for the Portfolio and Watchlist pages (#518 PR 6).

``analytics.returns.display_1d`` picks the figure a page shows;
``PricesService.quote_changes`` builds the per-symbol columns from one batched
quote call; ``WatchlistService.quotes`` is the Watchlist's envelope around it.
Yahoo is stubbed throughout.
"""
import unittest
from unittest.mock import Mock

from quantcore.analytics.returns import display_1d
from quantcore.gateways.yfinance_gateway import Quote
from quantcore.services.prices import PricesService
from quantcore.services.watchlist import WatchlistService

DAY = 86400
LAST_TS = 1794268800          # 2026-11-10, midnight UTC


def quote(change=1.2345, *, pre=None, post=None, state="REGULAR", price=110.0):
    return Quote(
        regular_open=108.0, regular_high=111.0, regular_low=107.0,
        regular_price=price, regular_volume=1000, regular_time=LAST_TS + 16 * 3600,
        previous_close=108.66, regular_change_pct=change,
        pre_price=None, pre_change_pct=pre, pre_time=None,
        post_price=None, post_change_pct=post, post_time=None,
        market_state=state, currency="USD", exchange_tz="America/New_York",
    )


def bars(symbol, closes):
    """Daily rows as ``daily_bars_for_symbols`` returns them, ending LAST_TS."""
    first = LAST_TS - (len(closes) - 1) * DAY
    return [{"symbol": symbol, "ts": first + i * DAY, "close": c, "volume": 1,
             "high": c, "low": c, "open": c} for i, c in enumerate(closes)]


class DisplayOneDayTest(unittest.TestCase):
    def test_fresh_quote_change_is_shown_rounded_and_not_stale(self):
        self.assertEqual(display_1d(1.23456, -9.0, False), (1.23, False))
        self.assertEqual(display_1d(0.0, -9.0, False), (0.0, False))

    def test_stale_quote_falls_back_to_the_cached_return(self):
        self.assertEqual(display_1d(1.23, 0.4, True), (0.4, True))
        self.assertEqual(display_1d(None, 0.4, True), (0.4, True))

    def test_fresh_quote_without_a_change_falls_back_and_is_stale(self):
        self.assertEqual(display_1d(None, 0.4, False), (0.4, True))

    def test_nothing_to_show_is_none_and_stale(self):
        self.assertEqual(display_1d(None, None, True), (None, True))


class QuoteChangesTest(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.repo.daily_bars_for_symbols.return_value = []
        self.gateway = Mock()
        self.service = PricesService(
            ohlcv_repository=self.repo, yfinance_gateway=self.gateway,
            options_repository=Mock(), sentiment_repository=Mock())

    def test_fresh_quotes_fill_every_column_and_read_no_bars(self):
        self.gateway.batch_quotes.return_value = {
            "AAA": quote(1.2345, post=-0.4567, state="POST"), "BBB": quote(-2.0)}
        out = self.service.quote_changes(["bbb", "AAA"])

        self.assertEqual(list(out), ["AAA", "BBB"])
        row = out["AAA"]
        self.assertEqual(row["regular_change_pct"], 1.23)
        self.assertEqual(row["post_change_pct"], -0.46)
        self.assertIsNone(row["pre_change_pct"])
        self.assertEqual(row["market_state"], "POST")
        self.assertIsNotNone(row["quote_as_of"])
        self.assertIs(row["quote_stale"], False)
        self.assertEqual((row["return_1d_display"], row["return_1d_stale"]), (1.23, False))
        self.gateway.batch_quotes.assert_called_once_with(["AAA", "BBB"])
        # Every quote was live, so the cached fallback was never read.
        self.repo.daily_bars_for_symbols.assert_not_called()

    def test_a_symbol_yahoo_left_out_falls_back_to_its_cached_return(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(1.0), "DOMO": None}
        self.repo.daily_bars_for_symbols.return_value = bars("DOMO", [100.0, 105.0])
        out = self.service.quote_changes(["AAA", "DOMO"])

        row = out["DOMO"]
        self.assertEqual((row["return_1d_display"], row["return_1d_stale"]), (5.0, True))
        self.assertIs(row["quote_stale"], True)
        self.assertIsNone(row["regular_change_pct"])
        self.assertIsNone(row["market_state"])
        self.assertIs(out["AAA"]["return_1d_stale"], False)
        # The fallback is read for the symbol that needs it, not the whole list.
        reads = [c.args[0] for c in self.repo.daily_bars_for_symbols.call_args_list]
        self.assertIn(["DOMO"], reads)
        self.assertNotIn(["AAA", "DOMO"], reads)

    def test_yahoo_down_marks_every_row_stale_and_never_raises(self):
        self.gateway.batch_quotes.side_effect = RuntimeError("yahoo down")
        self.repo.daily_bars_for_symbols.return_value = (
            bars("AAA", [100.0, 101.0]) + bars("BBB", [50.0, 49.0]))
        with self.assertLogs("quantcore.services.prices", "WARNING"):
            out = self.service.quote_changes(["AAA", "BBB"])
        self.assertEqual(out["AAA"]["return_1d_display"], 1.0)
        self.assertEqual(out["BBB"]["return_1d_display"], -2.0)
        self.assertTrue(all(r["return_1d_stale"] and r["quote_stale"] for r in out.values()))

    def test_a_symbol_with_no_quote_and_no_bars_is_an_empty_stale_row(self):
        self.gateway.batch_quotes.return_value = {"NEW": None}
        row = self.service.quote_changes(["NEW"])["NEW"]
        self.assertEqual((row["return_1d_display"], row["return_1d_stale"]), (None, True))

    def test_a_live_quote_without_a_change_uses_the_fallback(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(None)}
        self.repo.daily_bars_for_symbols.return_value = bars("AAA", [100.0, 102.0])
        row = self.service.quote_changes(["AAA"])["AAA"]
        self.assertEqual((row["return_1d_display"], row["return_1d_stale"]), (2.0, True))
        self.assertIs(row["quote_stale"], False)      # the price itself is live

    def test_a_supplied_fallback_means_no_read_at_all(self):
        self.gateway.batch_quotes.return_value = {"AAA": None, "BBB": quote(3.0)}
        self.repo.daily_bars_for_symbols.return_value = []     # the last-close fallback
        self.repo.daily_bars_for_symbols.reset_mock()
        out = self.service.quote_changes(["AAA", "BBB"], return_1d={"AAA": 0.75, "BBB": 9.9})
        self.assertEqual(out["AAA"]["return_1d_display"], 0.75)
        self.assertEqual(out["BBB"]["return_1d_display"], 3.0)   # live beats the fallback
        # One read is the quote cache's own last-close fallback for AAA; none
        # is a returns read (which asks with include_open=True for one symbol set).
        self.assertLessEqual(self.repo.daily_bars_for_symbols.call_count, 1)

    def test_force_reaches_the_quote_cache(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(1.0)}
        self.service.get_batch_quotes = Mock(return_value={
            "AAA": {"price": 1.0, "as_of": None, "stale": False, "quote": quote(1.0)}})
        self.service.quote_changes(["AAA"], force=True)
        self.service.get_batch_quotes.assert_called_once_with(["AAA"], force=True)

    def test_no_symbols_is_empty(self):
        self.assertEqual(self.service.quote_changes([]), {})
        self.gateway.batch_quotes.assert_not_called()


class WatchlistQuotesTest(unittest.TestCase):
    def _service(self, entries, changes):
        repo = Mock()
        repo.list_entries.return_value = entries
        self.prices = Mock()
        self.prices.quote_changes.return_value = changes
        return WatchlistService(repo, prices=self.prices)

    @staticmethod
    def _row(stale):
        return {"regular_change_pct": None if stale else 1.0, "pre_change_pct": None,
                "post_change_pct": None, "market_state": None if stale else "REGULAR",
                "quote_as_of": None, "quote_stale": stale,
                "return_1d_display": 0.5 if stale else 1.0, "return_1d_stale": stale}

    def test_envelope_rows_and_stale_count(self):
        svc = self._service(
            [{"symbol": "nvda "}, {"symbol": "AMD"}, {"symbol": None}],
            {"AMD": self._row(False), "NVDA": self._row(True)})
        out = svc.quotes(force=True)

        self.prices.quote_changes.assert_called_once_with(["NVDA", "AMD"], force=True)
        self.assertEqual(set(out), {"generated_at", "count", "stale_count", "quotes"})
        self.assertEqual((out["count"], out["stale_count"]), (2, 1))
        self.assertEqual([r["symbol"] for r in out["quotes"]], ["AMD", "NVDA"])
        self.assertEqual(out["quotes"][1]["return_1d_display"], 0.5)

    def test_empty_watchlist(self):
        out = self._service([], {}).quotes()
        self.assertEqual((out["count"], out["stale_count"], out["quotes"]), (0, 0, []))
        self.prices.quote_changes.assert_called_once_with([], force=False)

    def test_needs_the_prices_service(self):
        with self.assertRaises(RuntimeError):
            WatchlistService(Mock()).quotes()


if __name__ == "__main__":
    unittest.main()
