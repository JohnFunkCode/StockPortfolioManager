"""Tests for the harvester's fetch seam (issue #74): HarvesterService fetches
price data via YFinanceGateway and passes it into the repository — the
repository itself never touches yfinance. All mocked; no DB, no network.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

# Standard test preamble: swap in the test DSN BEFORE quantcore.db is imported
# (harvester_repository imports it transitively and freezes DB_DSN).
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

from quantcore.repositories.harvester_repository import PlanBuildParams  # noqa: E402
from quantcore.services.harvester import HarvesterService  # noqa: E402


def bars_df(include_adj=True):
    data = {
        "Open": [10.0, 11.0],
        "High": [10.5, 11.5],
        "Low": [9.5, 10.5],
        "Close": [10.2, 11.2],
        "Volume": [1000, 1100],
    }
    if include_adj:
        data["Adj Close"] = [10.1, 11.1]
    return pd.DataFrame(data, index=pd.to_datetime(["2026-07-10", "2026-07-13"]))


class TestBuildPlanFetchSeam(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.gateway = Mock()
        self.service = HarvesterService(self.repo, yfinance_gateway=self.gateway)

    def test_build_plan_fetches_raw_history_and_passes_bars(self):
        self.gateway.fetch_history.return_value = bars_df()
        params = PlanBuildParams()
        self.service.build_plan(symbol="intc", template_name="tmpl", params=params)

        args, kwargs = self.gateway.fetch_history.call_args
        self.assertEqual(args[0], "INTC")
        self.assertEqual(args[1], "1d")
        self.assertGreaterEqual(args[2], 420)
        self.assertFalse(kwargs["auto_adjust"])          # Adj Close required
        self.assertTrue(kwargs["include_adj_close"])

        _, repo_kwargs = self.repo.build_plan.call_args
        self.assertIn("bars", repo_kwargs)
        self.assertIn("Adj Close", repo_kwargs["bars"].columns)

    def test_missing_adj_close_falls_back_to_close(self):
        self.gateway.fetch_history.return_value = bars_df(include_adj=False)
        self.service.build_plan(symbol="INTC", template_name="t", params=PlanBuildParams())
        bars = self.repo.build_plan.call_args.kwargs["bars"]
        self.assertIn("Adj Close", bars.columns)
        self.assertEqual(list(bars["Adj Close"]), list(bars["Close"]))


class TestLatestClose(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.gateway = Mock()
        self.service = HarvesterService(self.repo, yfinance_gateway=self.gateway)

    def test_latest_close_from_gateway(self):
        self.gateway.fetch_history.return_value = bars_df()
        self.assertEqual(self.service.poll_latest_close("INTC"), 11.2)
        # The repository is never asked to fetch anything.
        self.assertFalse(
            any("poll" in str(c) for c in self.repo.method_calls),
            self.repo.method_calls,
        )

    def test_latest_close_none_on_empty(self):
        self.gateway.fetch_history.return_value = pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"]
        )
        self.assertIsNone(self.service.poll_latest_close("ZZNONE"))

    def test_symbols_at_harvest_points_injects_price_lookup(self):
        self.service.symbols_at_harvest_points()
        _, kwargs = self.repo.symbols_at_harvest_points.call_args
        self.assertIn("price_lookup", kwargs)
        self.assertTrue(callable(kwargs["price_lookup"]))


if __name__ == "__main__":
    unittest.main()
