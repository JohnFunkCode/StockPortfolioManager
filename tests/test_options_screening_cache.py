"""Tests for OptionsScreeningService's cached path (source="cache") and the
live path's chain dedupe.

The cached path must never touch Yahoo: every test here wires a gateway that
fails on any attribute access, so a stray live call is a loud failure rather
than a slow one. Repositories are stubs returning literal payloads in the
shapes the bulk readers produce.
"""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd

from quantcore.services.options_screening import (
    OptionsScreeningService,
    _chain_frames,
    _is_stale,
)

# Tuesday 2026-10-06, 11:00 ET — market date 2026-10-06.
NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
MONDAY_CLOSE = "2026-10-05T21:00:00Z"   # 1 trading day old: fresh
FRIDAY_CLOSE = "2026-10-02T21:00:00Z"   # 2 trading days old: stale


class _NoYahoo:
    """A gateway that fails the test on any use."""

    def __getattr__(self, name):
        raise AssertionError(f"cache mode called the Yahoo gateway: {name}")


def _contract(kind, strike, *, ask=2.1, last=2.0, oi=100, vol=10, iv=45.0):
    return {"kind": kind, "strike": strike, "last_price": last, "bid": 1.9,
            "ask": ask, "implied_vol": iv, "volume": vol,
            "open_interest": oi, "in_the_money": False}


def _contracts(**kw):
    strikes = (90.0, 95.0, 100.0, 105.0, 110.0)
    return ([_contract("call", s, **kw) for s in strikes]
            + [_contract("put", s, **kw) for s in strikes])


def _snapshot(captured_at=MONDAY_CLOSE, price=100.0, expirations=None):
    if expirations is None:
        expirations = [
            {"expiration": "2026-10-09", "put_call_ratio": 1.4,
             "total_call_oi": 1000, "total_put_oi": 1400,
             "total_call_vol": 200, "total_put_vol": 300,
             "avg_call_iv": 40.0, "avg_put_iv": 50.0, "contracts": _contracts()},
            {"expiration": "2026-10-16", "put_call_ratio": 1.1,
             "total_call_oi": 800, "total_put_oi": 880,
             "total_call_vol": 100, "total_put_vol": 110,
             "avg_call_iv": 38.0, "avg_put_iv": 44.0},
        ]
    return {"symbol": "X", "captured_at": captured_at, "price": price,
            "expirations": expirations}


def _history(n=300, base=100.0):
    rng = np.random.default_rng(11)
    closes = base * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"Close": closes},
                        index=pd.bdate_range(end="2026-10-05", periods=n))


def _entry(sym):
    return {"symbol": sym, "name": f"{sym} Corp", "tags": []}


class CachedScreenTestBase(unittest.TestCase):
    def setUp(self):
        self.chains = {"AAA": _snapshot(), "BBB": _snapshot(price=50.0)}
        self.history = {"AAA": _history(), "BBB": _history(base=50.0)}
        self.earnings_rows = []
        self.news = {}
        self.options_repo = Mock()
        self.options_repo.get_latest_full_chains.side_effect = lambda syms, d: {
            s: self.chains[s] for s in syms if s in self.chains}
        self.ohlcv = Mock()
        self.ohlcv.daily_history_for_symbols.side_effect = lambda syms, days: {
            s: self.history[s] for s in syms if s in self.history}
        self.fundamentals = Mock()
        self.fundamentals.get_all_latest.side_effect = lambda dt: self.earnings_rows
        self.news_store = Mock()
        self.news_store.get_sentiment_summaries.side_effect = lambda syms, days: {
            s: self.news.get(s, {"signal": "INSUFFICIENT_DATA"}) for s in syms}
        self.svc = OptionsScreeningService(
            ohlcv_repository=self.ohlcv, yfinance_gateway=_NoYahoo(), prices=_NoYahoo(),
            options_repository=self.options_repo,
            fundamentals_repository=self.fundamentals,
            news_store=self.news_store,
        )

    def run_cached(self, *symbols):
        return self.svc._run_cached([_entry(s) for s in symbols], 1000.0, 10, now=NOW)


class CachedScreenTests(CachedScreenTestBase):
    def test_scores_from_the_database_without_touching_yahoo(self):
        result = self.run_cached("AAA", "BBB")
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["stale"], [])
        self.assertNotIn("stale_hint", result)
        self.assertEqual(result["as_of"], MONDAY_CLOSE)
        summary = result["long_candidates"][0]
        self.assertEqual(summary["as_of"], MONDAY_CLOSE)
        self.assertEqual(summary["pc_analysis"]["near_oi_pc"], 1.4)
        self.assertEqual(summary["pc_analysis"]["mid_oi_pc"], 1.1)

    def test_one_bulk_read_per_input_and_market_date_cutoff(self):
        self.run_cached("aaa", "BBB")
        self.options_repo.get_latest_full_chains.assert_called_once_with(
            ["AAA", "BBB"], "2026-10-06")
        self.ohlcv.daily_history_for_symbols.assert_called_once()
        self.fundamentals.get_all_latest.assert_called_once_with("earnings_calendar")
        self.news_store.get_sentiment_summaries.assert_called_once()

    def test_as_of_is_the_oldest_capture_used(self):
        self.chains["BBB"] = _snapshot(captured_at="2026-10-05T20:30:00Z", price=50.0)
        self.assertEqual(self.run_cached("AAA", "BBB")["as_of"], "2026-10-05T20:30:00Z")

    def test_stale_and_missing_captures_are_listed_not_fetched(self):
        self.chains["BBB"] = _snapshot(captured_at=FRIDAY_CLOSE, price=50.0)
        result = self.run_cached("AAA", "BBB", "CCC")
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["stale"], [
            {"symbol": "BBB", "last_captured": FRIDAY_CLOSE},
            {"symbol": "CCC", "last_captured": None},
        ])
        self.assertIn("analyze_options_symbol", result["stale_hint"])

    def test_capture_whose_expirations_all_passed_is_stale(self):
        # The reader drops expirations before today; an empty list remains.
        self.chains["AAA"] = _snapshot(expirations=[])
        result = self.run_cached("AAA")
        self.assertEqual(result["stale"], [{"symbol": "AAA", "last_captured": MONDAY_CLOSE}])
        self.assertIsNone(result["as_of"])

    def test_no_history_is_a_failure_not_a_fetch(self):
        del self.history["BBB"]
        result = self.run_cached("AAA", "BBB")
        self.assertEqual(result["failed"], ["BBB"])

    def test_cached_earnings_hit_and_miss(self):
        self.earnings_rows = [{"symbol": "aaa", "earnings_date": "2026-10-16T00:00:00"},
                              {"symbol": "ZZZ", "earnings_date": None}]
        days = self.svc._cached_earnings(NOW.date())
        self.assertEqual(days, {"AAA": 10})
        result = self.run_cached("AAA", "BBB")
        by_symbol = {c["symbol"]: c for c in result["long_candidates"]}
        self.assertEqual(set(by_symbol), {"AAA", "BBB"})

    def test_scored_bullish_news_flags_the_catalyst(self):
        self.news["AAA"] = {"signal": "BULLISH", "scored_articles": 3,
                            "top_positive": ["AAA beats"]}
        captured = {}
        self.svc._ranked_response = lambda entries, results, *a: captured.update(
            {s.symbol: s for s in results}) or {"put_trades": []}
        self.run_cached("AAA", "BBB")
        self.assertTrue(captured["AAA"].recent_positive_catalyst)
        self.assertEqual(captured["AAA"].catalyst_headline, "AAA beats")
        self.assertIn("positive catalyst", self.svc.put_guardrail_reason(captured["AAA"]))
        self.assertFalse(captured["BBB"].recent_positive_catalyst)
        self.assertEqual(captured["BBB"].news_signal, "INSUFFICIENT_DATA")

    def test_trades_are_marked_indicative(self):
        self.svc._build_put_trades = lambda candidates, budget: [{"symbol": "AAA"}]
        result = self.run_cached("AAA")
        self.assertEqual(result["put_trades"][0]["pricing"],
                         f"indicative, as of {MONDAY_CLOSE}")

    def test_cache_mode_needs_its_repositories(self):
        svc = OptionsScreeningService(ohlcv_repository=self.ohlcv,
                                      yfinance_gateway=_NoYahoo())
        with self.assertRaises(RuntimeError):
            svc._run_cached([_entry("AAA")], 1000.0, 10, now=NOW)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            self.svc._run([_entry("AAA")], 1000.0, 10, "yahoo")

    def test_watchlist_defaults_to_cache_and_symbol_to_live(self):
        seen = []
        self.svc._run = lambda entries, b, n, source: seen.append(source)
        self.svc.analyze_watchlist(entries=[_entry("AAA")])
        self.svc.analyze_symbol("AAA")
        self.assertEqual(seen, ["cache", "live"])


class CacheHelperTests(unittest.TestCase):
    def test_zero_ask_falls_back_to_last_and_iv_becomes_a_fraction(self):
        calls, puts = _chain_frames([_contract("call", 100.0, ask=0.0, last=3.5, iv=40.0),
                                     _contract("put", 100.0, ask=2.2)])
        self.assertEqual(calls.iloc[0]["ask"], 3.5)
        self.assertEqual(puts.iloc[0]["ask"], 2.2)
        self.assertAlmostEqual(calls.iloc[0]["impliedVolatility"], 0.40)

    def test_empty_contracts_give_empty_frames(self):
        calls, puts = _chain_frames([])
        self.assertTrue(calls.empty and puts.empty)

    def test_staleness_counts_trading_days(self):
        today = NOW.date()
        self.assertFalse(_is_stale(_snapshot(MONDAY_CLOSE), today))
        self.assertTrue(_is_stale(_snapshot(FRIDAY_CLOSE), today))
        self.assertTrue(_is_stale(None, today))
        # Friday's capture read on Monday (one session later) is still fresh.
        self.assertFalse(_is_stale(_snapshot(FRIDAY_CLOSE), datetime(2026, 10, 5).date()))


class LiveDedupeTests(unittest.TestCase):
    def test_one_expirations_call_and_one_nearest_chain_call(self):
        def frame(oi):
            strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
            return pd.DataFrame({"strike": strikes, "openInterest": [oi] * 5,
                                 "volume": [10] * 5, "impliedVolatility": [0.4] * 5,
                                 "lastPrice": [2.0] * 5, "bid": [1.9] * 5,
                                 "ask": [2.1] * 5, "inTheMoney": [False] * 5})

        yf = Mock()
        yf.fast_info.return_value = SimpleNamespace(last_price=100.0)
        yf.expirations.return_value = ("2026-10-09", "2026-10-16")
        yf.option_chain.return_value = SimpleNamespace(calls=frame(100), puts=frame(150))
        yf.calendar.return_value = {}
        prices = Mock()
        prices.get_history.return_value = _history()
        news_store = Mock()
        news_store.get_sentiment_summary.return_value = {
            "signal": "NEUTRAL", "scored_articles": 2}
        svc = OptionsScreeningService(ohlcv_repository=Mock(), yfinance_gateway=yf,
                                      prices=prices)

        sec = svc.fetch_security("AAA", "AAA Corp", [], news_store=news_store)

        self.assertIsNotNone(sec)
        self.assertIsNotNone(sec.options)
        self.assertIsNotNone(sec.pc)
        yf.expirations.assert_called_once_with("AAA")
        nearest = [c for c in yf.option_chain.call_args_list if c.args[1] == "2026-10-09"]
        self.assertEqual(len(nearest), 1)


if __name__ == "__main__":
    unittest.main()
