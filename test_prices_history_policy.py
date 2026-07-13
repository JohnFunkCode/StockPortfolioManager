"""Tests for the fetch-when-stale policy in PricesService.get_history.

Issue #74: this policy used to live inside the OHLCV repository; per
architectural-standard-v2 Rule 5 (caching policy is a service concern) it now
lives here, orchestrating YFinanceGateway (fetch) and OhlcvRepository
(persist/query). Everything is mocked — no network, no DB.
"""
import datetime
import time
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from quantcore.services.prices import PricesService, WARM_DAYS


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

    def test_open_bar_triggers_refetch(self):
        self.repo.count_cached.return_value = 500
        self.repo.has_open_bar.return_value = True
        self.service.get_history("INTC", "1d", 30)
        self.gateway.fetch_history.assert_called_once()

    def test_stale_closed_bar_triggers_refetch(self):
        self.repo.count_cached.return_value = 500
        self.repo.has_open_bar.return_value = False
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
        self.repo.has_open_bar.return_value = False
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
