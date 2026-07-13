"""Tests for YFinanceGateway.fetch_history — the single yf.download seam.

Issue #74/#75: all yfinance downloads go through the gateway, which owns the
serialization lock (yf.download shares module-global state and is not
thread-safe — the July 2026 corruption vector), the MultiIndex column
flattening, the fetch-window cap, and provider-internal cache cleanup.
"""
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


if __name__ == "__main__":
    unittest.main()
