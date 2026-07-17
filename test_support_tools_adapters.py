"""Adapter-layer tests for the support-level tools (issue #93, Phases 1–2):
the FastAPI routes and MCP wrapper tools for ATR bands and Anchored VWAP.

Both adapters must stay exactly one call deep (architectural standard v2), so
these tests assert pure pass-through: the route forwards its params to the
service and ships the dict verbatim (or the plain ``{"error": str}`` 500), and
the MCP wrapper translates a tool call into a single ``rest_client.get``.
No network, no DB — the service registry and REST client are patched.
"""
import os
from pathlib import Path

# Swap in the test DSN BEFORE quantcore.db is imported transitively (DB_DSN
# freezes at import time). This module sorts ahead of the DB-backed suites in
# unittest discovery, so freezing the prod DSN here would trip db_safety's
# guard in every one of them (local runs only — CI has no .env).
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

import unittest  # noqa: E402
from unittest.mock import Mock, patch  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import prices as prices_router  # noqa: E402
from fastMCPTest import stock_price_server  # noqa: E402


def make_client():
    app = FastAPI()
    app.include_router(prices_router.router)
    return TestClient(app, raise_server_exceptions=False)


class TestSupportToolRoutes(unittest.TestCase):
    def setUp(self):
        self.services = Mock()
        patcher = patch.object(prices_router, "services", return_value=self.services)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = make_client()

    def test_atr_bands_passes_params_and_ships_dict_verbatim(self):
        payload = {"symbol": "NVDA", "atr": 4.2, "chandelier_stop": 118.6}
        self.services.prices.get_atr_bands.return_value = payload

        resp = self.client.get(
            "/api/securities/NVDA/atr-bands",
            params={"period": 21, "band_mult": 1.5, "stop_mult": 2.5,
                    "interval": "1h", "lookback": 100},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        self.services.prices.get_atr_bands.assert_called_once_with(
            "NVDA", 21, 1.5, 2.5, "1h", 100
        )

    def test_atr_bands_error_becomes_plain_500(self):
        self.services.prices.get_atr_bands.side_effect = ValueError("bad interval")
        resp = self.client.get("/api/securities/NVDA/atr-bands")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "bad interval"})

    def test_anchored_vwap_passes_params_and_ships_dict_verbatim(self):
        payload = {"symbol": "NVDA", "anchors": [], "nearest_support": None}
        self.services.prices.get_anchored_vwap.return_value = payload

        resp = self.client.get(
            "/api/securities/NVDA/anchored-vwap",
            params={"anchor_date": "2026-05-01", "lookback_days": 200, "swing_bars": 3},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        self.services.prices.get_anchored_vwap.assert_called_once_with(
            "NVDA", "2026-05-01", 200, 3
        )

    def test_anchored_vwap_defaults_and_error_path(self):
        self.services.prices.get_anchored_vwap.side_effect = ValueError("out of range")
        resp = self.client.get("/api/securities/NVDA/anchored-vwap")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "out of range"})
        self.services.prices.get_anchored_vwap.assert_called_once_with(
            "NVDA", None, 365, 5
        )


class TestSupportToolMcpWrappers(unittest.TestCase):
    def setUp(self):
        self.rest_get = Mock(return_value={"ok": True})
        patcher = patch.object(stock_price_server.rest_client, "get", self.rest_get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_atr_bands_is_one_rest_call(self):
        result = stock_price_server.get_atr_bands(
            "NVDA", period=21, band_mult=1.5, stop_mult=2.5, interval="1wk", lookback=100
        )
        self.assertEqual(result, {"ok": True})
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/atr-bands",
            period=21, band_mult=1.5, stop_mult=2.5, interval="1wk", lookback=100,
        )

    def test_get_anchored_vwap_omits_absent_anchor_date(self):
        result = stock_price_server.get_anchored_vwap("NVDA")
        self.assertEqual(result, {"ok": True})
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/anchored-vwap", lookback_days=365
        )

    def test_get_anchored_vwap_forwards_anchor_date(self):
        stock_price_server.get_anchored_vwap(
            "NVDA", anchor_date="2026-05-01", lookback_days=200
        )
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/anchored-vwap",
            lookback_days=200, anchor_date="2026-05-01",
        )


if __name__ == "__main__":
    unittest.main()
