"""Tests for YFinanceGateway.fetch_history — the single yf.download seam.

Issue #74/#75: all yfinance downloads go through the gateway, which owns the
serialization lock (yf.download shares module-global state and is not
thread-safe — the July 2026 corruption vector), the MultiIndex column
flattening, the fetch-window cap, and provider-internal cache cleanup.
"""
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd

from quantcore.gateways import yfinance_gateway as gw_mod
from quantcore.gateways.yfinance_gateway import YFinanceGateway


def flat_df():
    idx = pd.to_datetime(["2026-07-10", "2026-07-11"])
    return pd.DataFrame(
        {
            "Open": [1.0, 2.0],
            "High": [1.5, 2.5],
            "Low": [0.5, 1.5],
            "Close": [1.2, 2.2],
            "Volume": [100, 200],
        },
        index=idx,
    )


class TestFetchHistory(unittest.TestCase):
    def setUp(self):
        self.gateway = YFinanceGateway()

    def test_download_runs_under_the_serialization_lock(self):
        seen = {}

        def fake_download(*args, **kwargs):
            seen["locked_during_call"] = gw_mod._YF_DOWNLOAD_LOCK.locked()
            return flat_df()

        with patch.object(gw_mod.yf, "download", side_effect=fake_download):
            self.gateway.fetch_history("INTC", "1d", 30)
        self.assertTrue(seen["locked_during_call"])
        self.assertFalse(gw_mod._YF_DOWNLOAD_LOCK.locked())  # released after

    def test_bulk_download_also_serialized(self):
        seen = {}

        def fake_download(*args, **kwargs):
            seen["locked_during_call"] = gw_mod._YF_DOWNLOAD_LOCK.locked()
            return flat_df()

        with patch.object(gw_mod.yf, "download", side_effect=fake_download):
            self.gateway.download(["INTC", "SPY"], period="6mo")
        self.assertTrue(seen["locked_during_call"])

    def test_flattens_multiindex_columns_field_level_first(self):
        df = flat_df()
        df.columns = pd.MultiIndex.from_product([df.columns, ["INTC"]])
        with patch.object(gw_mod.yf, "download", return_value=df):
            out = self.gateway.fetch_history("INTC", "1d", 30)
        self.assertEqual(
            list(out.columns), ["Open", "High", "Low", "Close", "Volume"]
        )
        self.assertEqual(len(out), 2)

    def test_flattens_multiindex_columns_ticker_level_first(self):
        df = flat_df()
        df.columns = pd.MultiIndex.from_product([["INTC"], df.columns])
        with patch.object(gw_mod.yf, "download", return_value=df):
            out = self.gateway.fetch_history("INTC", "1d", 30)
        self.assertEqual(
            list(out.columns), ["Open", "High", "Low", "Close", "Volume"]
        )

    def test_empty_result_returns_standard_empty_frame(self):
        with patch.object(gw_mod.yf, "download", return_value=pd.DataFrame()):
            out = self.gateway.fetch_history("ZZNONE", "1d", 30)
        self.assertTrue(out.empty)
        self.assertEqual(
            list(out.columns), ["Open", "High", "Low", "Close", "Volume"]
        )

    def test_drops_rows_with_nan_close(self):
        df = flat_df()
        df.loc[df.index[0], "Close"] = float("nan")
        with patch.object(gw_mod.yf, "download", return_value=df):
            out = self.gateway.fetch_history("INTC", "1d", 30)
        self.assertEqual(len(out), 1)

    def test_window_capped_at_max_fetch_days(self):
        captured = {}

        def fake_download(symbol, **kwargs):
            captured.update(kwargs)
            return flat_df()

        with patch.object(gw_mod.yf, "download", side_effect=fake_download):
            self.gateway.fetch_history("INTC", "1d", 5000)
        start = pd.Timestamp(captured["start"])
        end = pd.Timestamp(captured["end"])
        self.assertLessEqual((end - start).days, 730)

    def test_auto_adjust_passthrough(self):
        captured = {}

        def fake_download(symbol, **kwargs):
            captured.update(kwargs)
            return flat_df()

        with patch.object(gw_mod.yf, "download", side_effect=fake_download):
            self.gateway.fetch_history("INTC", "1d", 30, auto_adjust=False)
        self.assertFalse(captured["auto_adjust"])


class TestCloseThreadCaches(unittest.TestCase):
    def test_never_raises_even_when_yfinance_internals_change(self):
        gateway = YFinanceGateway()
        # Must be safe to call regardless of yfinance version internals.
        gateway.close_thread_caches()
        gateway.close_thread_caches()


class HangingTicker:
    """A yf.Ticker whose .info never returns within the test's patience."""

    def __init__(self, hang_seconds, started=None):
        self._hang = hang_seconds
        self._started = started

    @property
    def info(self):
        if self._started is not None:
            self._started.set()
        time.sleep(self._hang)
        return {"currency": "USD"}


class TestTickerInfoTimeoutIsRealWallClock(unittest.TestCase):
    """The timeout has to bound the *caller*, not just the exception text.

    The original implementation ran the fetch in a
    ``with ThreadPoolExecutor(...)`` block. ``Executor.__exit__`` calls
    ``shutdown(wait=True)``, so raising TimeoutError from inside the block
    blocked on the way out until the hung worker finished — a 1s timeout
    measured 6.01s against a 6s hang. Every assertion here is on elapsed
    time, because the old code raised the right exception at the wrong time.
    """

    def test_returns_control_within_the_timeout_when_info_hangs(self):
        with patch.object(gw_mod.yf, "Ticker", lambda s: HangingTicker(30)):
            gateway = YFinanceGateway()
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                gateway.ticker_info("HANG", timeout=0.25)
            elapsed = time.monotonic() - start

        # Generous ceiling: the point is 0.25s-not-30s, not scheduler precision.
        self.assertLess(
            elapsed, 5.0,
            f"caller was held {elapsed:.2f}s by a 0.25s timeout — the "
            "timeout is not bounding the caller",
        )

    def test_a_prior_timeout_does_not_slow_the_next_call(self):
        """The abandoned thread must not be something the next caller joins."""
        with patch.object(gw_mod.yf, "Ticker", lambda s: HangingTicker(30)):
            gateway = YFinanceGateway()
            with self.assertRaises(TimeoutError):
                gateway.ticker_info("HANG", timeout=0.25)

        with patch.object(gw_mod.yf, "Ticker", lambda s: FastTicker()):
            start = time.monotonic()
            info = gateway.ticker_info("FAST", timeout=5.0)
            elapsed = time.monotonic() - start

        self.assertEqual(info["currency"], "SEK")
        self.assertLess(elapsed, 2.0, f"second call took {elapsed:.2f}s")

    def test_a_fetch_that_finishes_in_time_returns_its_payload(self):
        with patch.object(gw_mod.yf, "Ticker", lambda s: FastTicker()):
            info = YFinanceGateway().ticker_info("ASSA-B.ST", timeout=5.0)
        self.assertEqual(info["currency"], "SEK")

    def test_an_error_inside_the_worker_reaches_the_caller(self):
        """Swallowing it would look identical to "Yahoo has no currency"."""
        class ExplodingTicker:
            @property
            def info(self):
                raise ValueError("yahoo said no")

        with patch.object(gw_mod.yf, "Ticker", lambda s: ExplodingTicker()):
            with self.assertRaises(ValueError):
                YFinanceGateway().ticker_info("BOOM", timeout=5.0)

    def test_the_abandoned_worker_is_a_daemon(self):
        """A non-daemon worker would wedge interpreter exit instead of a request."""
        started = threading.Event()
        with patch.object(gw_mod.yf, "Ticker",
                          lambda s: HangingTicker(30, started=started)):
            gateway = YFinanceGateway()
            with self.assertRaises(TimeoutError):
                gateway.ticker_info("HANG", timeout=0.25)

        self.assertTrue(started.wait(1.0), "worker never ran")
        leaked = [t for t in threading.enumerate() if t.name == "yf-info-HANG"]
        self.assertTrue(leaked, "expected the abandoned worker to still exist")
        for t in leaked:
            self.assertTrue(t.daemon, "abandoned yfinance worker is not a daemon")


class FastTicker:
    @property
    def info(self):
        return {"currency": "SEK", "marketCap": 340_000_000_000}


if __name__ == "__main__":
    unittest.main()
