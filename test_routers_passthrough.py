"""Passthrough tests for the prices/options routers (85%-campaign).

Every route here is exactly one service call deep (arch-v2 Rule 6). These
tests pin that contract mechanically: with the service layer mocked, each
route must return the service payload verbatim on success and the plain
{"error": ...} 500 shape on failure — proving no route grows hidden logic.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Swap in the test DSN BEFORE quantcore.db is imported (frozen at import).
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routers import options as options_router  # noqa: E402
from api.routers import prices as prices_router  # noqa: E402

PAYLOAD = {"ok": True, "marker": "passthrough"}

PRICES_GETS = [
    "/api/securities/TST/ohlcv",
    "/api/securities/TST/technicals",
    "/api/securities/TST/price-summary",
    "/api/securities/TST/rsi",
    "/api/securities/TST/macd",
    "/api/securities/TST/stochastic",
    "/api/securities/TST/volume",
    "/api/securities/TST/obv",
    "/api/securities/TST/vwap",
    "/api/securities/TST/vwap/history",
    "/api/securities/TST/candlestick",
    "/api/securities/TST/higher-lows",
    "/api/securities/TST/gaps",
    "/api/securities/TST/drawdown",
    "/api/securities/TST/signals/technical",
    "/api/securities/TST/signals/risk",
]

OPTIONS_GETS = [
    "/api/securities/TST/options/latest",
    "/api/securities/TST/options/history",
    "/api/securities/TST/options/analytics",
    "/api/securities/TST/options/chain",
    "/api/securities/TST/options/iv-rank",
    "/api/securities/TST/options/full-chain",
    "/api/securities/TST/options/unusual-calls",
    "/api/securities/TST/options/delta-adjusted-oi",
    "/api/securities/TST/options/gamma-wall-history",
    "/api/securities/TST/signals/options-flow",
]


class _UniversalService:
    """Any method call returns PAYLOAD — routes must pass it through verbatim."""

    def __getattr__(self, name):
        return lambda *a, **k: PAYLOAD


class _UniversalBag:
    def __getattr__(self, name):
        return _UniversalService()


def happy_services():
    return _UniversalBag()


class RouterPassthroughTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    def with_services(self, factory):
        p1 = patch.object(prices_router, "services", factory)
        p2 = patch.object(options_router, "services", factory)
        p1.start(); p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_every_get_route_passes_the_service_payload_through(self):
        self.with_services(lambda: happy_services())
        for path in PRICES_GETS + OPTIONS_GETS:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            self.assertEqual(resp.json().get("marker"), "passthrough", path)

    def test_guarded_routes_degrade_to_plain_500(self):
        class ExplodingService:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise RuntimeError("service exploded")
                return boom

        class ExplodingBag:
            def __getattr__(self, name):
                return ExplodingService()

        self.with_services(lambda: ExplodingBag())
        # Routes with the try/except guard return the legacy plain-error shape.
        for path in ("/api/securities/TST/ohlcv", "/api/securities/TST/rsi",
                     "/api/securities/TST/options/latest"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 500, path)
            self.assertIn("service exploded", resp.json()["error"])

    def test_contracts_route_forwards_query_lists(self):
        captured = {}

        class ContractsService:
            def get_option_contracts(self, symbol, expirations, strikes, kind):
                captured.update(symbol=symbol, expirations=expirations,
                                strikes=strikes, kind=kind)
                return PAYLOAD

        class BagWithContracts:
            def __getattr__(self, name):
                return ContractsService()

        self.with_services(lambda: BagWithContracts())
        resp = self.client.get(
            "/api/securities/TST/options/contracts"
            "?expirations=2026-08-21&strikes=120&strikes=125&kind=put"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["kind"], "put")
        self.assertEqual(captured["strikes"], [120.0, 125.0])


if __name__ == "__main__":
    unittest.main()
