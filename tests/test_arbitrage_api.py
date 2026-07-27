"""Passthrough tests for the arbitrage router.

Every route is exactly one ArbitrageService call deep (arch-v2 Rule 6). With
the service mocked, each route must return the payload verbatim and forward
its query parameters unchanged — pinning that no route grows hidden logic.
"""
import unittest
from unittest.mock import patch

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.routers import arbitrage as arbitrage_router  # noqa: E402

PAYLOAD = {"ok": True, "marker": "passthrough"}


class RecordingService:
    """Records the call it received and returns PAYLOAD."""

    def __init__(self):
        self.calls = []

    def _record(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return PAYLOAD
        return call

    def __getattr__(self, name):
        return self._record(name)


class Bag:
    def __init__(self, service):
        self.arbitrage = service


class ArbitrageRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(), raise_server_exceptions=False)

    def setUp(self):
        self.service = RecordingService()
        patcher = patch.object(arbitrage_router, "services",
                               lambda: Bag(self.service))
        patcher.start()
        self.addCleanup(patcher.stop)

    def last_call(self):
        return self.service.calls[-1]

    def test_universe_route_passes_payload_through(self):
        resp = self.client.get("/api/arbitrage/universe")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), PAYLOAD)
        self.assertEqual(self.last_call()[0], "get_universe")

    def test_scan_route_defaults(self):
        resp = self.client.get("/api/arbitrage/scan")
        self.assertEqual(resp.status_code, 200)
        name, _, kwargs = self.last_call()
        self.assertEqual(name, "scan")
        self.assertIsNone(kwargs["kinds"])
        self.assertEqual(kwargs["top_n"], 20)
        self.assertEqual(kwargs["days"], 365)

    def test_scan_route_splits_the_kinds_list(self):
        self.client.get("/api/arbitrage/scan?kinds=nav_vehicle,producer&top_n=3")
        _, _, kwargs = self.last_call()
        self.assertEqual(kwargs["kinds"], ["nav_vehicle", "producer"])
        self.assertEqual(kwargs["top_n"], 3)

    def test_empty_kinds_becomes_none_not_an_empty_list(self):
        """An empty list would filter everything out; None must mean 'all'."""
        self.client.get("/api/arbitrage/scan?kinds=")
        self.assertIsNone(self.last_call()[2]["kinds"])

    def test_pair_route_uppercases_and_forwards_options(self):
        resp = self.client.get(
            "/api/arbitrage/pairs/mstr?underlying=BTC-USD&days=90&zscore_window=30"
        )
        self.assertEqual(resp.status_code, 200)
        name, args, kwargs = self.last_call()
        self.assertEqual(name, "analyze_pair")
        self.assertEqual(args[0], "MSTR")
        self.assertEqual(kwargs["underlying"], "BTC-USD")
        self.assertEqual(kwargs["days"], 90)
        self.assertEqual(kwargs["zscore_window"], 30)

    def test_discover_route_splits_symbols_and_references(self):
        self.client.get(
            "/api/arbitrage/discover?symbols=GDX,FCX&references=GC=F"
            "&min_abs_correlation=0.6&require_economic_link=false"
        )
        name, _, kwargs = self.last_call()
        self.assertEqual(name, "discover_pairs")
        self.assertEqual(kwargs["symbols"], ["GDX", "FCX"])
        self.assertEqual(kwargs["references"], ["GC=F"])
        self.assertEqual(kwargs["min_abs_correlation"], 0.6)
        self.assertFalse(kwargs["require_economic_link"])

    def test_discover_requires_symbols(self):
        resp = self.client.get("/api/arbitrage/discover")
        self.assertEqual(resp.status_code, 422)

    def test_spread_history_route_uppercases_and_forwards(self):
        resp = self.client.get(
            "/api/arbitrage/pairs/mstr/spread-history?underlying=BTC-USD&days=180"
        )
        self.assertEqual(resp.status_code, 200)
        name, args, kwargs = self.last_call()
        self.assertEqual(name, "get_spread_history")
        self.assertEqual(args[0], "MSTR")
        self.assertEqual(kwargs["underlying"], "BTC-USD")
        self.assertEqual(kwargs["days"], 180)

    def test_premium_history_route_uppercases_and_forwards(self):
        resp = self.client.get("/api/arbitrage/pairs/mstr/premium-history?days=90")
        self.assertEqual(resp.status_code, 200)
        name, args, kwargs = self.last_call()
        self.assertEqual(name, "get_premium_history")
        self.assertEqual(args[0], "MSTR")
        self.assertEqual(kwargs["days"], 90)

    def test_discover_forwards_include_all(self):
        self.client.get("/api/arbitrage/discover?symbols=NEM&include_all=true")
        self.assertTrue(self.last_call()[2]["include_all"])
        self.client.get("/api/arbitrage/discover?symbols=NEM")
        self.assertFalse(self.last_call()[2]["include_all"])


class QueryParameterBoundsTest(ArbitrageRouterTest):
    """Expensive/lossy numeric inputs must be rejected at the edge.

    `top_n=-1` previously reached `candidates[:-1]`, silently dropping the last
    candidate and reporting a negative `returned`; the rest fan out into
    per-symbol network fetches.
    """

    def assert_rejected(self, path):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 422, path)
        self.assertEqual(self.service.calls, [], f"service was reached: {path}")

    def test_non_positive_top_n_is_rejected(self):
        self.assert_rejected("/api/arbitrage/scan?top_n=-1")
        self.assert_rejected("/api/arbitrage/scan?top_n=0")

    def test_oversized_top_n_is_rejected(self):
        self.assert_rejected("/api/arbitrage/scan?top_n=1000")

    def test_days_bounds_are_enforced_on_every_route(self):
        for path in ("/api/arbitrage/scan?days=%s",
                     "/api/arbitrage/pairs/MSTR?days=%s",
                     "/api/arbitrage/discover?symbols=GDX&days=%s"):
            self.assert_rejected(path % 0)
            self.assert_rejected(path % 29)
            self.assert_rejected(path % 99999)

    def test_zscore_window_below_two_is_rejected(self):
        """A z-score needs two observations for a standard deviation, and a
        negative window used to slice from the front of the series."""
        self.assert_rejected("/api/arbitrage/pairs/MSTR?zscore_window=-5")
        self.assert_rejected("/api/arbitrage/pairs/MSTR?zscore_window=1")

    def test_correlation_floor_must_be_a_correlation(self):
        self.assert_rejected("/api/arbitrage/discover?symbols=GDX&min_abs_correlation=1.5")
        self.assert_rejected("/api/arbitrage/discover?symbols=GDX&min_abs_correlation=-0.1")

    def test_discover_symbol_list_is_capped(self):
        many = ",".join(f"SYM{i}" for i in range(30))
        resp = self.client.get(f"/api/arbitrage/discover?symbols={many}")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("limited to 25", resp.json()["message"])
        self.assertEqual(self.service.calls, [])

    def test_discover_reference_list_is_capped(self):
        refs = ",".join(f"R{i}=F" for i in range(15))
        resp = self.client.get(f"/api/arbitrage/discover?symbols=GDX&references={refs}")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("limited to 10", resp.json()["message"])

    def test_blank_symbols_is_rejected(self):
        self.assert_rejected("/api/arbitrage/discover?symbols=%20")
        self.assert_rejected("/api/arbitrage/discover?symbols=,,,")

    def test_history_routes_enforce_the_same_day_bounds(self):
        for path in ("/api/arbitrage/pairs/MSTR/spread-history?days=%s",
                     "/api/arbitrage/pairs/MSTR/premium-history?days=%s"):
            self.assert_rejected(path % 29)
            self.assert_rejected(path % 99999)

    def test_values_inside_the_bounds_still_pass_through(self):
        for path in ("/api/arbitrage/scan?top_n=1&days=30",
                     "/api/arbitrage/scan?top_n=100&days=3650",
                     "/api/arbitrage/pairs/MSTR?zscore_window=2",
                     "/api/arbitrage/discover?symbols=GDX&min_abs_correlation=0",
                     "/api/arbitrage/discover?symbols=GDX&min_abs_correlation=1"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def _failing_client(self, exc):
        class Exploding:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise exc
                return boom

        return patch.object(arbitrage_router, "services",
                            lambda: Bag(Exploding()))

    def test_service_failures_follow_the_app_error_contract(self):
        """api/errors.py maps RuntimeError -> 422 and ValueError -> 400."""
        with self._failing_client(RuntimeError("service exploded")):
            resp = self.client.get("/api/arbitrage/universe")
        self.assertEqual(resp.status_code, 422)
        self.assertIn("service exploded", resp.json()["message"])

        with self._failing_client(ValueError("bad symbol")):
            resp = self.client.get("/api/arbitrage/pairs/XYZ")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "ValidationError")


if __name__ == "__main__":
    unittest.main()
