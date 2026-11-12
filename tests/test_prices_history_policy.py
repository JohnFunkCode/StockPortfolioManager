"""Tests for the fetch-when-stale policy in PricesService.get_history.

Issue #74: this policy used to live inside the OHLCV repository; per
architectural-standard-v2 Rule 5 (caching policy is a service concern) it now
lives here, orchestrating YFinanceGateway (fetch) and OhlcvRepository
(persist/query). Everything is mocked — no network, no DB.
"""
import ast
import datetime
import pathlib
import time
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from quantcore.gateways.yfinance_gateway import Quote
from quantcore.services.prices import (
    OPEN_BAR_MAX_AGE_SECONDS,
    QUOTE_FORCE_FLOOR_SECONDS,
    QUOTE_TTL_SECONDS,
    PricesService,
    WARM_DAYS,
    _latest_price,
)


def make_service(repo=None, gateway=None):
    return PricesService(
        ohlcv_repository=repo or Mock(),
        yfinance_gateway=gateway or Mock(),
        options_repository=Mock(),
        sentiment_repository=Mock(),
    )


def ts_for(date: datetime.date) -> int:
    return int(time.mktime(date.timetuple()))


FRESH_DF = pd.DataFrame(
    {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
    index=pd.to_datetime(["2026-07-13"]),
)


class TestHistoryPolicy(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.gateway = Mock()
        self.gateway.fetch_history.return_value = FRESH_DF
        self.repo.get_bars.return_value = FRESH_DF
        self.service = make_service(self.repo, self.gateway)

    def test_invalid_interval_raises(self):
        with self.assertRaises(ValueError):
            self.service.get_history("INTC", "13m", 30)

    def test_cold_start_fetches_warm_days_and_stores(self):
        self.repo.count_cached.return_value = 0
        result = self.service.get_history("intc", "1d", 30)

        self.gateway.fetch_history.assert_called_once()
        args, kwargs = self.gateway.fetch_history.call_args
        self.assertEqual(args[0], "INTC")  # uppercased
        self.assertEqual(args[1], "1d")
        self.assertEqual(args[2], WARM_DAYS["1d"])  # warm window, not 30
        self.repo.store_bars.assert_called_once()
        self.assertIs(result, self.repo.get_bars.return_value)

    # --- the OPEN-bar re-fetch rule (#518 PR 3) ---------------------------
    TODAY = datetime.date(2026, 7, 13)
    NOW = 1_800_000_000.0

    def _history_with_open_bar(self, interval, *, bar_date, age):
        """get_history against a cache whose only reason to fetch is an OPEN
        bar dated ``bar_date`` and written ``age`` seconds ago."""
        self.repo.count_cached.return_value = 500
        self.repo.latest_closed_ts.return_value = None
        bar_ts = int(datetime.datetime.combine(
            bar_date, datetime.time(14, 30), tzinfo=datetime.timezone.utc).timestamp())
        self.repo.open_bar_state.return_value = (bar_ts, int(self.NOW) - age)
        with patch("quantcore.services.prices.market_date", return_value=self.TODAY), \
                patch("quantcore.services.prices.time.time", return_value=self.NOW):
            self.service.get_history("INTC", interval, 30)
        self.repo.open_bar_state.assert_called_once_with("INTC", interval)

    def test_fresh_open_bar_is_not_refetched(self):
        self._history_with_open_bar("1d", bar_date=self.TODAY, age=OPEN_BAR_MAX_AGE_SECONDS)
        self.gateway.fetch_history.assert_not_called()
        self.repo.store_bars.assert_not_called()

    def test_open_bar_past_the_max_age_is_refetched(self):
        self._history_with_open_bar(
            "1d", bar_date=self.TODAY, age=OPEN_BAR_MAX_AGE_SECONDS + 1)
        self.gateway.fetch_history.assert_called_once()

    def test_open_bar_from_an_earlier_session_is_refetched_at_any_age(self):
        self._history_with_open_bar(
            "1d", bar_date=self.TODAY - datetime.timedelta(days=1), age=1)
        self.gateway.fetch_history.assert_called_once()

    def test_intraday_open_bars_follow_the_same_rule(self):
        for interval in ("1h", "30m", "15m"):
            for age, bar_date, fetched in (
                (OPEN_BAR_MAX_AGE_SECONDS, self.TODAY, False),
                (OPEN_BAR_MAX_AGE_SECONDS + 1, self.TODAY, True),
                (1, self.TODAY - datetime.timedelta(days=3), True),
            ):
                with self.subTest(interval=interval, age=age, bar_date=bar_date):
                    self.repo.reset_mock()
                    self.gateway.reset_mock()
                    self._history_with_open_bar(interval, bar_date=bar_date, age=age)
                    self.assertEqual(self.gateway.fetch_history.called, fetched)

    def test_no_open_bar_falls_through_to_the_closed_bar_check(self):
        self.repo.count_cached.return_value = 500
        self.repo.open_bar_state.return_value = None
        self.repo.latest_closed_ts.return_value = ts_for(datetime.date(2026, 7, 9))
        with patch("quantcore.services.prices.latest_completed_session",
                   return_value=self.TODAY):
            self.service.get_history("INTC", "1d", 30)
        self.gateway.fetch_history.assert_called_once()

    def test_stale_closed_bar_triggers_refetch(self):
        self.repo.count_cached.return_value = 500
        self.repo.open_bar_state.return_value = None
        stale = datetime.date(2026, 7, 9)
        session = datetime.date(2026, 7, 13)
        self.repo.latest_closed_ts.return_value = ts_for(stale)
        with patch(
            "quantcore.services.prices.latest_completed_session", return_value=session
        ):
            self.service.get_history("INTC", "1d", 30)
        self.gateway.fetch_history.assert_called_once()

    def test_fresh_cache_no_fetch(self):
        self.repo.count_cached.return_value = 500
        self.repo.open_bar_state.return_value = None
        session = datetime.date(2026, 7, 13)
        self.repo.latest_closed_ts.return_value = ts_for(session)
        with patch(
            "quantcore.services.prices.latest_completed_session", return_value=session
        ):
            result = self.service.get_history("INTC", "1d", 30)
        self.gateway.fetch_history.assert_not_called()
        self.repo.store_bars.assert_not_called()
        self.assertIs(result, self.repo.get_bars.return_value)

    def test_empty_fetch_result_not_stored(self):
        self.repo.count_cached.return_value = 0
        self.gateway.fetch_history.return_value = pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"]
        )
        self.service.get_history("ZZNONE", "1d", 30)
        self.repo.store_bars.assert_not_called()


class TestReaderPolicy(unittest.TestCase):
    """#518 reader policy: live displays may read today's forming ``OPEN`` bar;
    a reader that ranks, alerts or uses volume reads closed bars only."""

    # Every function that reads closed bars only. A new ranking, alerting or
    # volume-based reader is added here on purpose, in the same change that
    # passes ``include_open=False``; so is one that stops being closed-only.
    CLOSED_ONLY_READERS = {
        ("quantcore/services/prices.py", "get_obv"),
        ("quantcore/services/prices.py", "get_volume_profile"),
        ("quantcore/services/prices.py", "screen_securities"),
        ("quantcore/services/options_screening.py", "fetch_bollinger_bands"),
        ("quantcore/services/options_screening.py", "fetch_iv_analysis"),
        ("quantcore/services/options_screening.py", "_run_cached"),
        ("quantcore/services/microstructure.py", "get_dark_pool"),
        ("quantcore/services/arbitrage.py", "_closes"),
    }

    def setUp(self):
        self.repo = Mock()
        self.repo.count_cached.return_value = 500
        self.repo.open_bar_state.return_value = None
        self.repo.latest_closed_ts.return_value = None
        self.repo.get_bars.return_value = FRESH_DF
        self.service = make_service(self.repo, Mock())

    def test_get_history_reads_the_open_bar_by_default(self):
        self.service.get_history("INTC", "1d", 30)
        self.repo.get_bars.assert_called_once_with("INTC", "1d", 30, include_open=True)

    def test_get_history_passes_closed_only_through(self):
        self.service.get_history("INTC", "1d", 30, include_open=False)
        self.repo.get_bars.assert_called_once_with("INTC", "1d", 30, include_open=False)

    def test_closed_only_readers_are_exactly_the_listed_ones(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        found = set()
        for path in sorted((root / "quantcore" / "services").glob("*.py")):
            tree = ast.parse(path.read_text())
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.Call) and any(
                        kw.arg == "include_open"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                        for kw in node.keywords
                    ):
                        found.add((str(path.relative_to(root)), func.name))
        self.assertEqual(found, self.CLOSED_ONLY_READERS)

    LIVE_ELSEWHERE = {
        ("quantcore/services/microstructure.py", "get_bid_ask_spread"),
        ("quantcore/services/options.py", "get_full_options_chain"),
        ("quantcore/services/portfolio.py", "_warm_cold_history"),
        ("quantcore/services/recommendations.py", "get_relative_strength_history"),
        ("quantcore/services/recommendations.py", "_prev_bar_hl"),
        ("quantcore/services/recommendations.py", "_src_fibonacci"),
    }

    # Every function that reads history on the live-display side (the
    # default). A new get_history caller lands here or in the list above, so
    # its side is chosen, not inherited.
    LIVE_READERS = {
        ("quantcore/services/prices.py", name) for name in (
            "get_stock_price", "get_rsi", "get_macd", "get_stochastic",
            "get_volume_analysis", "get_vwap", "get_vwap_history",
            "get_candlestick_patterns", "get_atr_bands", "get_anchored_vwap",
            "get_higher_lows", "get_gap_analysis", "get_historical_drawdown",
            "get_ohlcv_bars", "get_technicals_table",
        )
    } | LIVE_ELSEWHERE

    def test_live_readers_are_exactly_the_listed_ones(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        found = set()
        for path in sorted((root / "quantcore" / "services").glob("*.py")):
            for func in ast.walk(ast.parse(path.read_text())):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "get_history"
                            and not any(kw.arg == "include_open" for kw in node.keywords)):
                        found.add((str(path.relative_to(root)), func.name))
        self.assertEqual(found, self.LIVE_READERS)

    def test_closed_only_read_does_not_refresh_a_forming_bar(self):
        # The bar would be fetched, stored and then left out of the result.
        self.repo.open_bar_state.return_value = (0, 0)  # overdue OPEN bar
        session = datetime.date(2026, 7, 13)
        self.repo.latest_closed_ts.return_value = ts_for(session)
        with patch(
            "quantcore.services.prices.latest_completed_session", return_value=session
        ):
            self.service.get_history("INTC", "1d", 30, include_open=False)
            self.service._yf.fetch_history.assert_not_called()
            self.service.get_history("INTC", "1d", 30)
            self.service._yf.fetch_history.assert_called_once()

    def test_closed_only_read_still_fetches_when_the_closed_bar_is_behind(self):
        self.repo.open_bar_state.return_value = (0, 0)  # overdue OPEN bar
        self.repo.latest_closed_ts.return_value = ts_for(datetime.date(2026, 7, 9))
        with patch(
            "quantcore.services.prices.latest_completed_session",
            return_value=datetime.date(2026, 7, 13),
        ):
            self.service.get_history("INTC", "1d", 30, include_open=False)
        self.service._yf.fetch_history.assert_called_once()

    def test_volume_readers_ask_the_repository_for_closed_bars(self):
        closes = [100.0 + (i % 7) for i in range(260)]
        self.repo.get_bars.return_value = pd.DataFrame(
            {"Open": closes, "High": [c + 1 for c in closes],
             "Low": [c - 1 for c in closes], "Close": closes,
             "Volume": [1000 + i for i in range(260)]},
            index=pd.date_range("2025-06-01", periods=260, tz="UTC"),
        )
        for reader in (self.service.get_obv, self.service.get_volume_profile):
            self.repo.get_bars.reset_mock()
            with self.subTest(reader=reader.__name__):
                reader("INTC")
                self.assertIs(self.repo.get_bars.call_args.kwargs["include_open"], False)

    def test_composites_report_a_forming_bar_from_any_part(self):
        svc = self.service
        with patch.object(svc, "get_historical_drawdown", return_value={"latest_bar_open": False}), \
             patch.object(svc, "get_vwap", return_value={"vwap": 1.0, "latest_bar_open": True}):
            self.assertIs(svc.get_risk_signals("INTC")["latest_bar_open"], True)
        with patch.object(svc, "get_historical_drawdown", return_value={"latest_bar_open": False}), \
             patch.object(svc, "get_vwap", side_effect=RuntimeError("x")):
            self.assertIs(svc.get_risk_signals("INTC")["latest_bar_open"], False)
        parts = ("get_stochastic", "get_vwap", "get_obv", "get_volume_analysis",
                 "get_candlestick_patterns", "get_higher_lows", "get_gap_analysis")
        for flagged, expected in (("get_gap_analysis", True), (None, False)):
            patches = [
                patch.object(svc, name, return_value={"latest_bar_open": name == flagged})
                for name in parts
            ]
            for p in patches:
                p.start()
            try:
                self.assertIs(svc.get_technical_signals("INTC")["latest_bar_open"], expected)
            finally:
                for p in patches:
                    p.stop()

    def test_latest_bar_open_is_set_on_a_forming_bar(self):
        closes = [100.0 + i for i in range(60)]
        frame = pd.DataFrame(
            {"Open": closes, "High": closes, "Low": closes, "Close": closes,
             "Volume": [1000] * 60},
            index=pd.date_range("2026-01-01", periods=60, tz="UTC"),
        )
        for is_open in (True, False):
            frame.attrs["latest_bar_open"] = is_open
            self.repo.get_bars.return_value = frame
            with self.subTest(latest_bar_open=is_open):
                self.assertIs(self.service.get_rsi("INTC")["latest_bar_open"], is_open)
                self.assertIs(self.service.get_ohlcv_bars("INTC")["latest_bar_open"], is_open)
                self.assertIs(
                    self.service.get_technicals_table("INTC")["latest_bar_open"], is_open)

    def test_latest_bar_open_is_false_for_a_frame_without_the_marker(self):
        self.assertIs(self.service.get_ohlcv_bars("INTC")["latest_bar_open"], False)


if __name__ == "__main__":
    unittest.main()


class TestGetFastPrice(unittest.TestCase):
    """notifier.py's alert loop uses this instead of importing yfinance (issue #76)."""

    def setUp(self):
        self.gateway = Mock()
        self.service = make_service(gateway=self.gateway)

    def test_returns_last_price(self):
        self.gateway.fast_info.return_value = type("FI", (), {"last_price": 123.45})()
        self.assertEqual(self.service.get_fast_price("intc"), 123.45)
        self.gateway.fast_info.assert_called_once_with("INTC")

    def test_none_on_missing_or_nonpositive(self):
        self.gateway.fast_info.return_value = type("FI", (), {"last_price": 0})()
        self.assertIsNone(self.service.get_fast_price("INTC"))
        self.gateway.fast_info.return_value = type("FI", (), {})()
        self.assertIsNone(self.service.get_fast_price("INTC"))

    def test_none_on_gateway_error(self):
        self.gateway.fast_info.side_effect = RuntimeError("yahoo down")
        self.assertIsNone(self.service.get_fast_price("INTC"))


def quote(price=None, at=1794344400, pre=None, pre_at=None, post=None, post_at=None):
    """A gateway Quote with only the fields price selection reads."""
    return Quote(
        regular_open=None, regular_high=None, regular_low=None,
        regular_price=price, regular_volume=None, regular_time=at,
        previous_close=None, regular_change_pct=None,
        pre_price=pre, pre_change_pct=None, pre_time=pre_at,
        post_price=post, post_change_pct=None, post_time=post_at,
        market_state=None, currency="USD", exchange_tz="America/New_York",
    )


class TestGetQuotes(unittest.TestCase):
    """Issue #126 Step 4.4, on the batch endpoint since #518: one upstream
    call per due symbol set, shared TTL cache across calls."""

    def setUp(self):
        self.repo = Mock()
        self.gateway = Mock()
        self.service = make_service(self.repo, self.gateway)

    def test_batches_symbols_into_one_upstream_call(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(10.0), "BBB": quote(20.0)}
        quotes = self.service.get_quotes(["aaa", "bbb"])
        self.gateway.batch_quotes.assert_called_once_with(["AAA", "BBB"])
        self.assertEqual(quotes["AAA"]["price"], 10.0)
        self.assertFalse(quotes["AAA"]["stale"])
        self.assertEqual(quotes["BBB"]["price"], 20.0)
        self.assertEqual(set(quotes["AAA"]), {"price", "as_of", "stale"})

    def test_ttl_is_60_seconds(self):
        self.assertEqual(QUOTE_TTL_SECONDS, 60)
        self.assertEqual(QUOTE_FORCE_FLOOR_SECONDS, 10)

    def test_second_call_within_ttl_makes_no_upstream_call(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(10.0)}
        with patch("quantcore.services.prices.time.monotonic", return_value=1000.0):
            self.service.get_quotes(["AAA"])
        self.gateway.batch_quotes.reset_mock()
        with patch("quantcore.services.prices.time.monotonic", return_value=1059.0):
            quotes = self.service.get_quotes(["AAA"])
        self.gateway.batch_quotes.assert_not_called()
        self.assertEqual(quotes["AAA"]["price"], 10.0)

    def test_call_after_ttl_expires_refetches(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(10.0)}
        with patch("quantcore.services.prices.time.monotonic", return_value=1000.0):
            self.service.get_quotes(["AAA"])
        self.gateway.batch_quotes.reset_mock()
        self.gateway.batch_quotes.return_value = {"AAA": quote(11.0)}
        with patch("quantcore.services.prices.time.monotonic", return_value=1061.0):
            quotes = self.service.get_quotes(["AAA"])
        self.gateway.batch_quotes.assert_called_once_with(["AAA"])
        self.assertEqual(quotes["AAA"]["price"], 11.0)

    def test_force_past_floor_refetches_before_normal_ttl_elapses(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(10.0)}
        with patch("quantcore.services.prices.time.monotonic", return_value=1000.0):
            self.service.get_quotes(["AAA"])
        self.gateway.batch_quotes.reset_mock()
        self.gateway.batch_quotes.return_value = {"AAA": quote(12.0)}
        with patch("quantcore.services.prices.time.monotonic", return_value=1011.0):
            quotes = self.service.get_quotes(["AAA"], force=True)
        self.gateway.batch_quotes.assert_called_once_with(["AAA"])
        self.assertEqual(quotes["AAA"]["price"], 12.0)

    def test_force_within_floor_makes_no_upstream_call(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(10.0)}
        with patch("quantcore.services.prices.time.monotonic", return_value=1000.0):
            self.service.get_quotes(["AAA"])
        self.gateway.batch_quotes.reset_mock()
        with patch("quantcore.services.prices.time.monotonic", return_value=1005.0):
            self.service.get_quotes(["AAA"], force=True)
        self.gateway.batch_quotes.assert_not_called()

    def test_upstream_failure_degrades_to_last_close_stale(self):
        self.gateway.batch_quotes.side_effect = RuntimeError("yahoo down")
        self.repo.daily_bars_for_symbols.return_value = [
            {"symbol": "AAA", "ts": ts_for(datetime.date(2026, 7, 24)), "close": 9.5,
             "volume": 100, "high": 10, "low": 9, "open": 9.5},
        ]
        quotes = self.service.get_quotes(["AAA"])
        self.assertEqual(quotes["AAA"]["price"], 9.5)
        self.assertTrue(quotes["AAA"]["stale"])
        self.assertIsNotNone(quotes["AAA"]["as_of"])

    def test_missing_symbol_falls_back_to_last_close(self):
        # Yahoo silently omits symbols (gotcha 3); the gateway maps them to None.
        self.gateway.batch_quotes.return_value = {"AAA": quote(10.0), "BBB": None}
        self.repo.daily_bars_for_symbols.return_value = [
            {"symbol": "BBB", "ts": ts_for(datetime.date(2026, 7, 24)), "close": 7.0,
             "volume": 1, "high": 7, "low": 7, "open": 7},
        ]
        quotes = self.service.get_quotes(["AAA", "BBB"])
        self.repo.daily_bars_for_symbols.assert_called_once_with(["BBB"], include_open=True)
        self.assertFalse(quotes["AAA"]["stale"])
        self.assertEqual(quotes["BBB"]["price"], 7.0)
        self.assertTrue(quotes["BBB"]["stale"])

    def test_quote_without_a_positive_price_falls_back(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(0.0)}
        self.repo.daily_bars_for_symbols.return_value = []
        quotes = self.service.get_quotes(["AAA"])
        self.assertIsNone(quotes["AAA"]["price"])
        self.assertTrue(quotes["AAA"]["stale"])

    def test_missing_quote_with_no_history_degrades_to_none(self):
        self.gateway.batch_quotes.return_value = {"AAA": None}
        self.repo.daily_bars_for_symbols.return_value = []
        quotes = self.service.get_quotes(["AAA"])
        self.assertIsNone(quotes["AAA"]["price"])
        self.assertTrue(quotes["AAA"]["stale"])

    def test_empty_symbols_returns_empty_dict_and_no_call(self):
        self.assertEqual(self.service.get_quotes([]), {})
        self.gateway.batch_quotes.assert_not_called()

    def test_partial_failure_preserves_prior_cache_entry(self):
        self.gateway.batch_quotes.return_value = {"AAA": quote(10.0)}
        with patch("quantcore.services.prices.time.monotonic", return_value=1000.0):
            self.service.get_quotes(["AAA"])
        self.gateway.batch_quotes.reset_mock()
        self.gateway.batch_quotes.return_value = {"AAA": None}
        with patch("quantcore.services.prices.time.monotonic", return_value=1061.0):
            quotes = self.service.get_quotes(["AAA"])
        self.assertEqual(quotes["AAA"]["price"], 10.0)
        self.repo.daily_bars_for_symbols.assert_not_called()

    def test_get_batch_quotes_returns_the_full_quote(self):
        q = quote(10.0)
        self.gateway.batch_quotes.return_value = {"AAA": q, "BBB": None}
        self.repo.daily_bars_for_symbols.return_value = []
        out = self.service.get_batch_quotes(["AAA", "BBB"])
        self.assertIs(out["AAA"]["quote"], q)
        self.assertIsNone(out["BBB"]["quote"])


class TestLatestPrice(unittest.TestCase):
    """#518 decision 5: the Portfolio keeps showing the latest price."""

    REG = 1794344400          # 2026-11-10 16:00 ET

    def test_regular_session(self):
        price, as_of = _latest_price(quote(100.0, at=self.REG))
        self.assertEqual(price, 100.0)
        self.assertEqual(as_of, datetime.datetime.fromtimestamp(self.REG, datetime.timezone.utc))

    def test_after_hours_uses_post_price(self):
        price, _ = _latest_price(quote(100.0, at=self.REG, post=101.0, post_at=self.REG + 3600))
        self.assertEqual(price, 101.0)

    def test_pre_market_uses_pre_price(self):
        q = quote(100.0, at=self.REG, pre=99.0, pre_at=self.REG + 16 * 3600)
        self.assertEqual(_latest_price(q)[0], 99.0)

    def test_overnight_keeps_last_after_hours_price(self):
        # PREPRE / weekend: no pre price yet, yesterday's post is the latest.
        q = quote(100.0, at=self.REG, post=101.0, post_at=self.REG + 4 * 3600)
        self.assertEqual(_latest_price(q)[0], 101.0)

    def test_older_extended_price_loses_to_regular(self):
        q = quote(100.0, at=self.REG, pre=95.0, pre_at=self.REG - 8 * 3600)
        self.assertEqual(_latest_price(q)[0], 100.0)

    def test_non_positive_and_missing_prices_are_skipped(self):
        self.assertIsNone(_latest_price(None))
        self.assertIsNone(_latest_price(quote(None)))
        self.assertIsNone(_latest_price(quote(0.0, post=-1.0, post_at=self.REG + 1)))
        q = quote(0.0, at=self.REG, post=101.0, post_at=self.REG + 1)
        self.assertEqual(_latest_price(q)[0], 101.0)

    def test_price_without_a_timestamp_is_dated_now(self):
        # Yahoo sent a price but no time: the quote was just fetched, so the
        # fetch time is the best "as of" there is.
        before = datetime.datetime.now(datetime.timezone.utc)
        price, as_of = _latest_price(quote(100.0, at=None))
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertEqual(price, 100.0)
        self.assertLessEqual(before, as_of)
        self.assertLessEqual(as_of, after)

    def test_timed_price_beats_one_without_a_timestamp(self):
        q = quote(100.0, at=None, post=101.0, post_at=self.REG)
        price, as_of = _latest_price(q)
        self.assertEqual(price, 101.0)
        self.assertEqual(as_of, datetime.datetime.fromtimestamp(self.REG, datetime.timezone.utc))
