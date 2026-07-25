"""Adapter-layer tests for the support-level tools (issue #93, Phases 1–6):
the FastAPI routes and MCP wrapper tools for ATR bands, Anchored VWAP,
Volume Profile, OI-change analysis, the signed GEX profile, and the
composite Support Confluence.

Both adapters must stay exactly one call deep (architectural standard v2), so
these tests assert pure pass-through: the route forwards its params to the
service and ships the dict verbatim (or the plain ``{"error": str}`` 500), and
the MCP wrapper translates a tool call into a single ``rest_client.get``.
No network, no DB — the service registry and REST client are patched.
"""
import os
from pathlib import Path

import unittest  # noqa: E402
from unittest.mock import Mock, patch  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import options as options_router  # noqa: E402
from api.routers import prices as prices_router  # noqa: E402
from api.routers import recommendations as recommendations_router  # noqa: E402
from fastMCPTest import stock_price_server  # noqa: E402


def make_client():
    app = FastAPI()
    app.include_router(prices_router.router)
    app.include_router(options_router.router)
    app.include_router(recommendations_router.router)
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

    def test_volume_profile_passes_params_and_ships_dict_verbatim(self):
        payload = {"symbol": "NVDA", "poc": 101.0, "hvns": []}
        self.services.prices.get_volume_profile.return_value = payload

        resp = self.client.get(
            "/api/securities/NVDA/volume-profile",
            params={"days": 100, "interval": "1h", "bins": 30, "value_area_pct": 0.6},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        self.services.prices.get_volume_profile.assert_called_once_with(
            "NVDA", 100, "1h", 30, 0.6
        )

    def test_volume_profile_defaults_and_error_path(self):
        self.services.prices.get_volume_profile.side_effect = ValueError("bad interval")
        resp = self.client.get("/api/securities/NVDA/volume-profile")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "bad interval"})
        self.services.prices.get_volume_profile.assert_called_once_with(
            "NVDA", 365, "1d", 50, 0.7
        )


class TestOptionsSupportToolRoutes(unittest.TestCase):
    """Phase 4+5 routes live in the options router."""

    def setUp(self):
        self.services = Mock()
        patcher = patch.object(options_router, "services", return_value=self.services)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = make_client()

    def test_oi_change_passes_params_and_ships_dict_verbatim(self):
        payload = {"symbol": "NVDA", "top_oi_builds": [], "summary": "quiet"}
        self.services.options.get_oi_change_analysis.return_value = payload

        resp = self.client.get(
            "/api/securities/NVDA/options/oi-change",
            params={"days": 14, "top_n": 5, "min_oi": 250, "expiration": "2026-08-21"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        self.services.options.get_oi_change_analysis.assert_called_once_with(
            "NVDA", days=14, top_n=5, min_oi=250, expiration="2026-08-21"
        )

    def test_oi_change_defaults_and_error_path(self):
        self.services.options.get_oi_change_analysis.side_effect = ValueError("db down")
        resp = self.client.get("/api/securities/NVDA/options/oi-change")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "db down"})
        self.services.options.get_oi_change_analysis.assert_called_once_with(
            "NVDA", days=30, top_n=10, min_oi=100, expiration=None
        )

    def test_gex_profile_passes_params_and_ships_dict_verbatim(self):
        payload = {"symbol": "NVDA", "net_gex": 1.0, "regime": "positive_gamma"}
        self.services.options.get_gex_profile.return_value = payload

        resp = self.client.get(
            "/api/securities/NVDA/options/gex-profile",
            params={"max_expirations": 3, "risk_free_rate": 0.05},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        self.services.options.get_gex_profile.assert_called_once_with("NVDA", 3, 0.05)

    def test_gex_profile_defaults_and_error_path(self):
        self.services.options.get_gex_profile.side_effect = ValueError("no price")
        resp = self.client.get("/api/securities/NVDA/options/gex-profile")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "no price"})
        self.services.options.get_gex_profile.assert_called_once_with("NVDA", 6, 0.045)


class TestSupportConfluenceRoute(unittest.TestCase):
    """Phase 6 route lives in the recommendations router."""

    def setUp(self):
        self.services = Mock()
        patcher = patch.object(
            recommendations_router, "services", return_value=self.services
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = make_client()

    def test_support_confluence_passes_params_and_ships_dict_verbatim(self):
        payload = {"symbol": "NVDA", "support_zones": [], "strongest_support": None}
        self.services.recommendations.get_support_confluence.return_value = payload

        resp = self.client.get(
            "/api/securities/NVDA/support-confluence",
            params={"tolerance_pct": 2.0, "max_expirations": 6, "max_zones": 3},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        self.services.recommendations.get_support_confluence.assert_called_once_with(
            "NVDA", tolerance_pct=2.0, max_expirations=6, max_zones=3
        )

    def test_support_confluence_defaults(self):
        self.services.recommendations.get_support_confluence.return_value = {}
        resp = self.client.get("/api/securities/NVDA/support-confluence")
        self.assertEqual(resp.status_code, 200)
        self.services.recommendations.get_support_confluence.assert_called_once_with(
            "NVDA", tolerance_pct=1.0, max_expirations=4, max_zones=5
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

    def test_get_volume_profile_is_one_rest_call(self):
        result = stock_price_server.get_volume_profile(
            "NVDA", days=100, interval="1h", bins=30, value_area_pct=0.6
        )
        self.assertEqual(result, {"ok": True})
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/volume-profile",
            days=100, interval="1h", bins=30, value_area_pct=0.6,
        )

    def test_get_oi_change_analysis_omits_absent_expiration(self):
        result = stock_price_server.get_oi_change_analysis("NVDA")
        self.assertEqual(result, {"ok": True})
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/options/oi-change",
            days=30, top_n=10, min_oi=100,
        )

    def test_get_oi_change_analysis_forwards_expiration(self):
        stock_price_server.get_oi_change_analysis(
            "NVDA", days=14, top_n=5, min_oi=250, expiration="2026-08-21"
        )
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/options/oi-change",
            days=14, top_n=5, min_oi=250, expiration="2026-08-21",
        )

    def test_get_gex_profile_is_one_rest_call(self):
        result = stock_price_server.get_gex_profile(
            "NVDA", max_expirations=3, risk_free_rate=0.05
        )
        self.assertEqual(result, {"ok": True})
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/options/gex-profile",
            max_expirations=3, risk_free_rate=0.05,
        )

    def test_get_support_confluence_is_one_rest_call(self):
        result = stock_price_server.get_support_confluence(
            "NVDA", tolerance_pct=2.0, max_expirations=6, max_zones=3
        )
        self.assertEqual(result, {"ok": True})
        self.rest_get.assert_called_once_with(
            "/api/securities/NVDA/support-confluence",
            tolerance_pct=2.0, max_expirations=6, max_zones=3,
        )


if __name__ == "__main__":
    unittest.main()
