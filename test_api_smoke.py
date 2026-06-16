"""Smoke tests for the FastAPI REST tier (Phase 2).

Runs the FastAPI app through Starlette's TestClient against the *test* DB and
exercises the Harvester CRUD + dashboard surface. Plans are seeded directly
via SQL (bypassing build_plan / yfinance) so the tests stay offline and
deterministic, mirroring test_harvester_service.py.

The key parity assertion is that the API response carries the *exact same key
set* as the underlying service dict — proving the response_model documentation
does not strip keys (handlers return QuantCoreJSONResponse verbatim).
"""

import os
import unittest
from contextlib import closing
from pathlib import Path

# Swap in the test DSN BEFORE quantcore.db is imported (it freezes DB_DSN at
# import time), then let the guard abort if this process would reach prod.
for _line in (Path(__file__).parent / ".env").read_text().splitlines():
    if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
        os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
        break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.harvester_repository import _utc_now_iso  # noqa: E402
from quantcore.services.registry import get_services  # noqa: E402

from api.main import create_app  # noqa: E402

TEST_SYMBOL = "ZZAPISMOKE"
TEST_TEMPLATE = "zz_api_smoke_template"


class ApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app())

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM plan_instances WHERE symbol_id IN "
                "(SELECT symbol_id FROM symbols WHERE ticker = %s)",
                (TEST_SYMBOL,),
            )
            conn.execute("DELETE FROM symbols WHERE ticker = %s", (TEST_SYMBOL,))
            conn.execute("DELETE FROM plan_templates WHERE name = %s", (TEST_TEMPLATE,))
            conn.commit()

    def _seed_plan(self, status="ACTIVE", rungs=((1, 110.0, 10), (2, 120.0, 10))):
        now = _utc_now_iso()
        with closing(get_connection()) as conn:
            template_id = conn.execute(
                """
                INSERT INTO plan_templates
                  (name, is_dynamic_h, history_window_days, n_iterations, created_at)
                VALUES (:name, 1, 360, 4, :now)
                RETURNING template_id
                """,
                {"name": TEST_TEMPLATE, "now": now},
            ).fetchone()["template_id"]

            conn.execute(
                "INSERT INTO symbols (ticker, created_at) VALUES (:t, :now) "
                "ON CONFLICT(ticker) DO NOTHING",
                {"t": TEST_SYMBOL, "now": now},
            )
            symbol_id = conn.execute(
                "SELECT symbol_id FROM symbols WHERE ticker = :t", {"t": TEST_SYMBOL}
            ).fetchone()["symbol_id"]

            instance_id = conn.execute(
                """
                INSERT INTO plan_instances (
                  template_id, symbol_id, status, created_at, asof_date,
                  price_asof, shares_initial, v0_floor, capital_at_risk,
                  history_end_date, history_window_days,
                  r_daily, annual_vol, h_threshold, n_iterations
                ) VALUES (
                  :template_id, :symbol_id, :status, :now, :now,
                  100.0, 20, 2000.0, 2000.0,
                  '2026-06-01', 360,
                  0.0005, 0.25, 0.1, 4
                )
                RETURNING instance_id
                """,
                {
                    "template_id": template_id,
                    "symbol_id": symbol_id,
                    "status": status,
                    "now": now,
                },
            ).fetchone()["instance_id"]

            rung_ids = []
            shares_before = 20
            for rung_index, target_price, shares_sold in rungs:
                shares_after = shares_before - shares_sold
                rung_id = conn.execute(
                    """
                    INSERT INTO plan_rungs (
                      instance_id, rung_index, target_price,
                      shares_before, shares_sold_planned, shares_after_planned,
                      gross_harvest_planned, cumulative_harvest_planned,
                      remaining_value_planned, total_wealth_planned, total_return_planned,
                      status
                    ) VALUES (
                      :instance_id, :rung_index, :target_price,
                      :shares_before, :shares_sold, :shares_after,
                      0.0, 0.0, 0.0, 0.0, 0.0, 'PENDING'
                    )
                    RETURNING rung_id
                    """,
                    {
                        "instance_id": instance_id,
                        "rung_index": rung_index,
                        "target_price": target_price,
                        "shares_before": shares_before,
                        "shares_sold": shares_sold,
                        "shares_after": shares_after,
                    },
                ).fetchone()["rung_id"]
                rung_ids.append(int(rung_id))
                shares_before = shares_after
            conn.commit()
        return int(instance_id), rung_ids

    # ------------------------------------------------------------------

    def test_health(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["db_connected"])

    def test_dashboard_stats_shape(self):
        resp = self.client.get("/api/dashboard/stats")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("total_plans", "symbols_tracked", "active_alerts"):
            self.assertIn(key, body)

    def test_plan_round_trip_preserves_key_set(self):
        instance_id, rung_ids = self._seed_plan()
        svc = get_services().harvester

        # list — the seeded plan is present under ACTIVE
        resp = self.client.get("/api/plans?status=ACTIVE")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(instance_id, [p["instance_id"] for p in resp.json()["plans"]])

        # get — full plan + rungs, with the EXACT key set the service returns
        resp = self.client.get(f"/api/plans/{instance_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        expected_plan = svc.get_plan_by_id(instance_id)
        expected_rungs = svc.get_rungs_for_plan(instance_id)
        self.assertEqual(set(body["plan"].keys()), set(expected_plan.keys()))
        self.assertEqual(set(body["rungs"][0].keys()), set(expected_rungs[0].keys()))
        self.assertEqual([r["rung_id"] for r in body["rungs"]], rung_ids)

        # rungs sub-resource
        resp = self.client.get(f"/api/plans/{instance_id}/rungs")
        self.assertEqual([r["rung_id"] for r in resp.json()["rungs"]], rung_ids)

        # single rung envelope
        resp = self.client.get(f"/api/rungs/{rung_ids[0]}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rung"]["rung_id"], rung_ids[0])

        # delete — archives to SUPERSEDED
        resp = self.client.delete(f"/api/plans/{instance_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"instance_id": instance_id, "deleted": True})
        self.assertEqual(svc.get_plan_by_id(instance_id)["status"], "SUPERSEDED")

    def test_create_plan_missing_symbol_returns_legacy_400(self):
        resp = self.client.post("/api/plans", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "symbol is required", "status": 400})

    def test_invalid_status_filter_returns_400(self):
        resp = self.client.get("/api/plans?status=BOGUS")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Invalid status filter", "status": 400})

    def test_get_missing_plan_returns_404(self):
        resp = self.client.get("/api/plans/999999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "Plan not found", "status": 404})

    # --- Step 2: portfolio / watchlist / securities ---------------------

    def test_portfolio_get_shape(self):
        resp = self.client.get("/api/portfolio")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("securities", resp.json())
        self.assertIsInstance(resp.json()["securities"], list)

    def test_watchlist_get_shape(self):
        resp = self.client.get("/api/watchlist")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json()["securities"], list)

    def test_securities_combined_shape(self):
        resp = self.client.get("/api/securities")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json()["securities"], list)

    def test_add_position_missing_symbol_returns_plain_400(self):
        # Note the bare {"error": ...} body (no "status" key) — distinct from
        # the harvester routes.
        resp = self.client.post("/api/portfolio", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "symbol is required"})

    def test_add_watchlist_missing_symbol_returns_plain_400(self):
        resp = self.client.post("/api/watchlist", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "symbol is required"})

    def test_lookup_missing_symbol_returns_plain_400(self):
        resp = self.client.get("/api/securities/lookup")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "symbol is required"})

    def test_remove_missing_position_returns_404(self):
        resp = self.client.delete("/api/portfolio/ZZNOSUCH?owner=zz_api_import_test")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "ZZNOSUCH not found in portfolio"})

    def test_import_missing_path_returns_400(self):
        resp = self.client.post("/api/portfolio/import", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "a CSV file upload or 'path' is required"})

    def test_import_multipart_round_trip(self):
        owner = "zz_api_import_test"
        csv = (
            "name,symbol,purchase_price,quantity,purchase_date,currency,"
            "sale_price,sale_date,current_price\n"
            "Smoke Co,ZZSMOKE,10.0,3,2026-06-01,USD,,,\n"
        )
        try:
            resp = self.client.post(
                f"/api/portfolio/import?owner={owner}",
                files={"file": ("p.csv", csv, "text/csv")},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"owner": owner, "imported": 1})

            got = self.client.get(f"/api/portfolio?owner={owner}").json()["securities"]
            self.assertEqual([s["symbol"] for s in got], ["ZZSMOKE"])
        finally:
            self.client.delete(f"/api/portfolio/ZZSMOKE?owner={owner}")


if __name__ == "__main__":
    unittest.main()
