"""GET /api/portfolio/check: owner from the principal, bounded parameters, redacted failures.

The portfolio router is mounted on a bare FastAPI app with a stand-in service, so
this runs without a database (create_app() calls ensure_schema()). The full app's
version of the failure check is in test_error_redaction.
"""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import require_owner
from api.deps import INTERNAL_ERROR
from api.routers import portfolio as portfolio_router
from quantcore.analytics.portfolio_check import CheckParams

SECRET = "postgresql://quantcore:s3cr3t-pw@10.0.0.5:5432/quantcore"
REPORT = {"as_of": "2026-11-17", "params": {}, "market_state": {}, "total_value": 0.0,
          "holdings": [], "ideas": [], "industries": [], "unpriced": []}


class StubCheck:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result or REPORT, exc, []

    def check(self, owner, *, params):
        self.calls.append((owner, params))
        if self.exc:
            raise self.exc
        return self.result


class RouteTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(portfolio_router.router)
        app.dependency_overrides[require_owner] = lambda: "alice"
        self.client = TestClient(app, raise_server_exceptions=False)

    def use(self, stub):
        bag = type("Bag", (), {"portfolio_check": stub})()
        p = patch.object(portfolio_router, "services", lambda: bag)
        p.start()
        self.addCleanup(p.stop)
        return stub

    def test_owner_comes_from_the_principal_and_defaults_apply(self):
        stub = self.use(StubCheck())
        resp = self.client.get("/api/portfolio/check?owner=bob")   # an ?owner= is ignored
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), REPORT)
        self.assertEqual(stub.calls, [("alice", CheckParams())])

    def test_parameters_pass_through(self):
        stub = self.use(StubCheck())
        resp = self.client.get("/api/portfolio/check?st_rate=0.35&lt_rate=0.2&position_cap=0.05"
                               "&industry_cap=0.3&wait_days=30")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(stub.calls[0][1], CheckParams(st_rate=0.35, lt_rate=0.2, position_cap=0.05,
                                                       industry_cap=0.3, wait_days=30))

    def test_out_of_range_parameters_are_422(self):
        stub = self.use(StubCheck())
        for q in ("st_rate=1.5", "lt_rate=-0.1", "position_cap=0", "industry_cap=2", "wait_days=400", "wait_days=x"):
            with self.subTest(q=q):
                self.assertEqual(self.client.get(f"/api/portfolio/check?{q}").status_code, 422)
        self.assertEqual(stub.calls, [])

    def test_a_failure_is_redacted(self):
        self.use(StubCheck(exc=RuntimeError(f"could not connect {SECRET}")))
        with self.assertLogs("api.routers.portfolio", level="WARNING") as logs:
            resp = self.client.get("/api/portfolio/check")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": INTERNAL_ERROR})
        joined = "\n".join(logs.output)
        self.assertNotIn("s3cr3t", joined)
        self.assertIn("get_portfolio_check failed", joined)
        self.assertIn("RuntimeError", joined)

    def test_a_bad_market_state_names_the_problem(self):
        # The service's ValueError is a validation message, which may reach the caller.
        self.use(StubCheck(exc=ValueError("portfolio check market state: 3 industries, expected at least 40")))
        with self.assertLogs("api.routers.portfolio", level="WARNING"):
            resp = self.client.get("/api/portfolio/check")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("3 industries", resp.json()["error"])


if __name__ == "__main__":
    unittest.main()
