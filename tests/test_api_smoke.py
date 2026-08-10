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

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastapi.testclient import TestClient  # noqa: E402

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.harvester_repository import _utc_now_iso  # noqa: E402
from quantcore.repositories.owner_identity_repository import (  # noqa: E402
    OwnerIdentityRepository,
)
from quantcore.services.registry import get_services  # noqa: E402

from api.auth import Principal, require_principal  # noqa: E402
from api.main import create_app  # noqa: E402

TEST_SYMBOL = "ZZAPISMOKE"
TEST_TEMPLATE = "zz_api_smoke_template"

# The watchlist is global (issue #83), so its tests get their own symbol and
# clean up after themselves rather than snapshotting the table.
WATCHLIST_SYMBOL = "ZZWLAPI"

# The exact key set api/deps.load_watchlist() produced from watchlist.yaml.
# GET /api/securities merges watchlist rows with portfolio rows into one list,
# so the DB-backed rows have to keep carrying the None placeholders.
LEGACY_SECURITY_KEYS = {
    "name", "symbol", "currency", "purchase_price", "quantity",
    "purchase_date", "sale_price", "sale_date", "source", "tags",
}

# Owner-scoping (issue #126 PR 2): a principal must resolve through
# IdentityService.resolve_owner, so tests map a fake identity to the owner
# handle rather than passing ?owner= directly.
TEST_IDENTITY = "zz_api_smoke_identity@example.com"
TEST_OWNER = "zz_api_import_test"
UNMAPPED_IDENTITY = "zz_api_smoke_unmapped@example.com"


class ApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        OwnerIdentityRepository().upsert(TEST_IDENTITY, TEST_OWNER)

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM plan_instances WHERE symbol_id IN "
                "(SELECT symbol_id FROM symbols WHERE ticker = %s)",
                (TEST_SYMBOL,),
            )
            # Watchlist and positions rows both FK to symbols, so they go first
            # or the symbols delete below fails.
            conn.execute(
                "DELETE FROM watchlist WHERE symbol_id IN "
                "(SELECT symbol_id FROM symbols WHERE ticker = %s)",
                (WATCHLIST_SYMBOL,),
            )
            conn.execute(
                "DELETE FROM positions WHERE symbol_id IN "
                "(SELECT symbol_id FROM symbols WHERE ticker = %s)",
                (WATCHLIST_SYMBOL,),
            )
            conn.execute("DELETE FROM symbols WHERE ticker = %s", (WATCHLIST_SYMBOL,))
            conn.execute("DELETE FROM symbols WHERE ticker = %s", (TEST_SYMBOL,))
            conn.execute("DELETE FROM plan_templates WHERE name = %s", (TEST_TEMPLATE,))
            conn.execute("DELETE FROM owner_identities WHERE identity = %s", (TEST_IDENTITY,))
            conn.execute(
                "DELETE FROM owner_identities WHERE identity = %s", (UNMAPPED_IDENTITY,)
            )
            conn.commit()

    def _override_principal(self, identity):
        """Swap in a non-local principal for one test, resolved for real through
        IdentityService via require_owner (only require_principal is stubbed)."""
        principal = Principal(subject=identity, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal
        self.addCleanup(self.app.dependency_overrides.pop, require_principal, None)

    def _route_dependencies(self, method, path):
        """Every dependency callable reachable from one route's handler.

        Walks nested routers: create_app() includes each module's router, and
        this FastAPI version keeps them as lazy container objects in
        app.routes, so the endpoints hang off `original_router` rather than
        being flattened into the app.
        """
        pending = list(self.app.routes)
        while pending:
            route = pending.pop()
            nested = getattr(route, "routes", None) or getattr(
                getattr(route, "original_router", None), "routes", None
            )
            if nested:
                pending.extend(nested)
                continue
            if getattr(route, "path", None) != path or method not in getattr(
                route, "methods", ()
            ):
                continue
            found, queue = set(), list(route.dependant.dependencies)
            while queue:
                dep = queue.pop()
                found.add(dep.call)
                queue.extend(dep.dependencies)
            return found
        raise AssertionError(f"no route for {method} {path}")

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

    # --- Watchlist writes (issue #83) -----------------------------------
    #
    # The YAML-backed watchlist appended to a file inside the container image,
    # so on Cloud Run an add survived until the next request hit a different
    # instance. These go through the API against the real table.

    def test_add_watchlist_round_trips_through_the_database(self):
        resp = self.client.post("/api/watchlist", json={
            "symbol": WATCHLIST_SYMBOL.lower(),
            "name": "Watchlist Smoke Co",
            "currency": "usd",
            "tags": ["ai", ""],
        })
        self.assertEqual(resp.status_code, 201)
        # The currency in the response is what was *stored*, which the service
        # resolves off the exchange rather than taking from the body. ZZWLAPI
        # is not a real ticker, so the lookup misses and the posted value is
        # the fallback — that fallback path is the one this asserts.
        self.assertEqual(
            resp.json(),
            {"symbol": WATCHLIST_SYMBOL, "destination": "watchlist",
             "currency": "USD"},
        )

        listed = self.client.get("/api/watchlist").json()["securities"]
        [entry] = [s for s in listed if s["symbol"] == WATCHLIST_SYMBOL]
        self.assertEqual(set(entry), LEGACY_SECURITY_KEYS)
        self.assertEqual(entry["source"], "watchlist")
        self.assertEqual(entry["name"], "Watchlist Smoke Co")
        self.assertEqual(entry["currency"], "USD", "currency is normalized")
        self.assertEqual(entry["tags"], ["ai"], "blank tags are dropped")
        for key in ("purchase_price", "quantity", "purchase_date",
                    "sale_price", "sale_date"):
            self.assertIsNone(entry[key])

        resp = self.client.delete(f"/api/watchlist/{WATCHLIST_SYMBOL.lower()}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"symbol": WATCHLIST_SYMBOL, "removed": True})

        listed = self.client.get("/api/watchlist").json()["securities"]
        self.assertEqual([s for s in listed if s["symbol"] == WATCHLIST_SYMBOL], [])

    def test_add_watchlist_duplicate_returns_409(self):
        self.client.post("/api/watchlist", json={"symbol": WATCHLIST_SYMBOL})
        resp = self.client.post("/api/watchlist", json={"symbol": WATCHLIST_SYMBOL})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.json(), {"error": f"{WATCHLIST_SYMBOL} is already in the watchlist"}
        )

    def test_remove_missing_watchlist_symbol_returns_404(self):
        resp = self.client.delete("/api/watchlist/ZZNOSUCH")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "ZZNOSUCH not found in watchlist"})

    def test_watchlist_writes_are_authenticated_but_not_owner_scoped(self):
        """Plan decision 5: the list is global, so its writes take
        ``require_principal`` (any authenticated caller) rather than
        ``require_owner``, which would 403 an identity with no owner mapping.

        Asserted on the dependency graph rather than by calling the routes
        unauthenticated: with no signing key configured ``require_principal``
        hands back a local principal, so a 401 never surfaces in this suite —
        tests/test_auth.py covers the rejection itself.
        """
        from api.auth import require_owner

        for method, path in (("POST", "/api/watchlist"),
                             ("DELETE", "/api/watchlist/{ticker}")):
            deps = self._route_dependencies(method, path)
            self.assertIn(require_principal, deps, f"{method} {path}")
            self.assertNotIn(require_owner, deps, f"{method} {path}")

        # The contrast that gives the assertion above its meaning: adding to
        # the portfolio *is* owner-scoped. (require_principal alone proves
        # little — create_app applies it to every business router.)
        self.assertIn(
            require_owner, self._route_dependencies("POST", "/api/portfolio")
        )

    def test_securities_marks_a_symbol_in_both_lists_as_both(self):
        self._override_principal(TEST_IDENTITY)
        self.client.post("/api/portfolio", json={
            "symbol": WATCHLIST_SYMBOL, "purchase_price": 10.0, "quantity": 2,
            "purchase_date": "2026-06-01",
        })
        self.addCleanup(self.client.delete, f"/api/portfolio/{WATCHLIST_SYMBOL}")
        self.client.post("/api/watchlist", json={
            "symbol": WATCHLIST_SYMBOL, "tags": ["ai"],
        })

        combined = self.client.get("/api/securities").json()["securities"]
        [entry] = [s for s in combined if s["symbol"] == WATCHLIST_SYMBOL]
        self.assertEqual(entry["source"], "both")
        self.assertEqual(entry["tags"], ["ai"], "watchlist tags win on a merge")
        self.assertEqual(entry["quantity"], 2, "portfolio fields survive")

    # --- Watchlist fundamentals (issue #147) -----------------------------

    def test_watchlist_fundamentals_returns_the_envelope_not_a_ticker(self):
        """The page's payload, and the only guard on the route ordering.

        ``/watchlist/fundamentals`` sits next to the ``/watchlist/{ticker}``
        path template. Nothing makes them collide today — ``{ticker}`` is
        DELETE-only, and a method mismatch is a *partial* match Starlette
        scans past — so the declaration order in the router is habit, not
        enforcement. Adding a GET on ``/watchlist/{ticker}`` above this one
        would quietly turn the page's endpoint into a lookup for a symbol
        nobody holds. Asserting on the body means that lands here as a
        failure rather than in the browser as an empty table.
        """
        self.client.post("/api/watchlist", json={
            "symbol": WATCHLIST_SYMBOL, "name": "Watchlist Smoke Co",
        })

        resp = self.client.get("/api/watchlist/fundamentals")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            set(body),
            {"generated_at", "as_of", "count", "stale_count",
             "unscored_count", "rows"},
        )
        self.assertEqual(body["count"], len(body["rows"]))

        [row] = [r for r in body["rows"] if r["symbol"] == WATCHLIST_SYMBOL]
        self.assertEqual(row["name"], "Watchlist Smoke Co")
        # A symbol this suite invented has no bars and no cached fundamentals:
        # the row still comes back fully keyed, with nulls, and counts as
        # unscored rather than as stale (nothing was ever fetched to age).
        self.assertIsNone(row["price"])
        self.assertIsNone(row["composite_score"])
        self.assertIsNone(row["fundamentals_stale"])
        self.assertEqual(row["market_cap_currency"], "USD")
        self.assertGreaterEqual(body["unscored_count"], 1)

    def test_lookup_missing_symbol_returns_plain_400(self):
        resp = self.client.get("/api/securities/lookup")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "symbol is required"})

    def test_remove_missing_position_returns_404(self):
        self._override_principal(TEST_IDENTITY)
        resp = self.client.delete("/api/portfolio/ZZNOSUCH")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "ZZNOSUCH not found in portfolio"})

    def test_import_missing_path_returns_400(self):
        resp = self.client.post("/api/portfolio/import", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "a CSV file upload or 'path' is required"})

    def test_import_multipart_round_trip(self):
        self._override_principal(TEST_IDENTITY)
        csv = (
            "name,symbol,purchase_price,quantity,purchase_date,currency,"
            "sale_price,sale_date,current_price\n"
            "Smoke Co,ZZSMOKE,10.0,3,2026-06-01,USD,,,\n"
        )
        try:
            resp = self.client.post(
                "/api/portfolio/import",
                files={"file": ("p.csv", csv, "text/csv")},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"owner": TEST_OWNER, "imported": 1})

            got = self.client.get("/api/portfolio").json()["securities"]
            self.assertEqual([s["symbol"] for s in got], ["ZZSMOKE"])
        finally:
            self.client.delete("/api/portfolio/ZZSMOKE")

    def test_unmapped_principal_returns_403_not_provisioned(self):
        # No owner_identities row exists for this identity (issue #126 decision
        # #2) — it must 403, not fall back to using the identity as the owner.
        self._override_principal(UNMAPPED_IDENTITY)
        resp = self.client.get("/api/portfolio")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json(),
            {"error": "not_provisioned", "message": "not_provisioned", "status": 403},
        )

    def test_unmapped_principal_403_is_identical_for_all_owner_scoped_routes(self):
        # decision #4 — the same 403/not_provisioned shape regardless of route.
        self._override_principal(UNMAPPED_IDENTITY)
        for resp in (
            self.client.get("/api/portfolio"),
            self.client.get("/api/securities"),
            self.client.get("/api/portfolio/delta-exposure"),
        ):
            self.assertEqual(resp.status_code, 403)
            self.assertEqual(
                resp.json(),
                {"error": "not_provisioned", "message": "not_provisioned", "status": 403},
            )


class LotRoutesTest(unittest.TestCase):
    """Issue #126 Step 4.5 — REST routes for lots, including the mandatory
    cross-owner isolation test (owner A cannot read/patch/delete/close owner
    B's lot). Two distinct owners: the default local principal resolves to
    "john"; TEST_IDENTITY resolves to TEST_OWNER via the override helper."""

    SYMBOL = "ZZLOTSMOKE"

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.client = TestClient(cls.app)

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        OwnerIdentityRepository().upsert(TEST_IDENTITY, TEST_OWNER)

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM positions WHERE symbol_id IN "
                "(SELECT symbol_id FROM symbols WHERE ticker = %s)",
                (self.SYMBOL,),
            )
            conn.execute("DELETE FROM symbols WHERE ticker = %s", (self.SYMBOL,))
            conn.execute("DELETE FROM owner_identities WHERE identity = %s", (TEST_IDENTITY,))
            conn.commit()

    def _override_principal(self, identity):
        principal = Principal(subject=identity, is_local=False)
        self.app.dependency_overrides[require_principal] = lambda: principal
        self.addCleanup(self.app.dependency_overrides.pop, require_principal, None)

    def _create_lot(self, **overrides):
        body = {
            "symbol": self.SYMBOL,
            "name": "Lot Smoke Co",
            "currency": "USD",
            "purchase_price": "10.00",
            "quantity": "5",
            "trade_date": "2026-06-01",
            **overrides,
        }
        resp = self.client.post("/api/portfolio/lots", json=body)
        self.assertEqual(resp.status_code, 201, resp.text)
        lots = self.client.get("/api/portfolio/lots").json()["lots"]
        return max((l for l in lots if l["symbol"] == self.SYMBOL), key=lambda l: l["lot_id"])

    def test_create_lot_missing_symbol_returns_plain_400(self):
        resp = self.client.post("/api/portfolio/lots", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "symbol is required"})

    def test_create_lot_missing_purchase_price_returns_422(self):
        resp = self.client.post(
            "/api/portfolio/lots",
            json={
                "symbol": self.SYMBOL, "name": "Lot Smoke Co", "currency": "USD",
                "quantity": "5", "trade_date": "2026-06-01",
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_create_lot_zero_quantity_returns_422(self):
        resp = self.client.post(
            "/api/portfolio/lots",
            json={
                "symbol": self.SYMBOL, "name": "Lot Smoke Co", "currency": "USD",
                "purchase_price": "10.00", "quantity": "0", "trade_date": "2026-06-01",
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_create_lot_returns_symbol_only(self):
        resp = self.client.post(
            "/api/portfolio/lots",
            json={
                "symbol": self.SYMBOL, "name": "Lot Smoke Co", "currency": "USD",
                "purchase_price": "10.00", "quantity": "5", "trade_date": "2026-06-01",
            },
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json(), {"symbol": self.SYMBOL})

    def test_lots_and_symbols_round_trip(self):
        lot = self._create_lot()
        self.assertEqual(lot["symbol"], self.SYMBOL)
        self.assertEqual(lot["status"], "OPEN")

        body = self.client.get("/api/portfolio/symbols").json()
        rows = body["symbols"]
        row = next(r for r in rows if r["symbol"] == self.SYMBOL)
        self.assertEqual([l["lot_id"] for l in row["lots"]], [lot["lot_id"]])
        self.assertIn("total_investment", row)
        self.assertIn("total_investment", body["totals"])

    def test_update_lot_patches_fields(self):
        lot = self._create_lot()
        resp = self.client.patch(
            f"/api/portfolio/lots/{lot['lot_id']}", json={"notes": "corrected"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"lot_id": lot["lot_id"], "updated": True})

    def test_update_lot_no_fields_returns_plain_400(self):
        lot = self._create_lot()
        resp = self.client.patch(f"/api/portfolio/lots/{lot['lot_id']}", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "no fields to update"})

    def test_update_missing_lot_returns_404(self):
        resp = self.client.patch("/api/portfolio/lots/999999999", json={"notes": "x"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "lot 999999999 not found"})

    def test_delete_lot_round_trip(self):
        lot = self._create_lot()
        resp = self.client.delete(f"/api/portfolio/lots/{lot['lot_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"lot_id": lot["lot_id"], "deleted": True})

    def test_delete_missing_lot_returns_404(self):
        resp = self.client.delete("/api/portfolio/lots/999999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "lot 999999999 not found"})

    def test_close_lot_round_trip(self):
        lot = self._create_lot()
        resp = self.client.post(
            f"/api/portfolio/lots/{lot['lot_id']}/close",
            json={"shares": "2", "sale_price": "12.00", "sale_trade_date": "2026-07-01"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["symbol"], self.SYMBOL)
        self.assertEqual(len(body["allocations"]), 1)

    def test_close_missing_lot_returns_404(self):
        resp = self.client.post(
            "/api/portfolio/lots/999999999/close",
            json={"shares": "1", "sale_price": "10.00", "sale_trade_date": "2026-07-01"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.json()["error"])

    def test_close_lot_oversell_returns_422(self):
        lot = self._create_lot()
        resp = self.client.post(
            f"/api/portfolio/lots/{lot['lot_id']}/close",
            json={"shares": "999", "sale_price": "12.00", "sale_trade_date": "2026-07-01"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_cross_owner_read_patch_delete_close_all_404(self):
        # john creates a lot ...
        lot = self._create_lot()
        lot_id = lot["lot_id"]

        # ... then a different owner (TEST_OWNER) must not see, patch, delete,
        # or close it — the repository's owner-scoped WHERE clause yields no
        # match, which every route surfaces as a plain 404.
        self._override_principal(TEST_IDENTITY)

        lots = self.client.get("/api/portfolio/lots").json()["lots"]
        self.assertNotIn(lot_id, [l["lot_id"] for l in lots])

        resp = self.client.patch(f"/api/portfolio/lots/{lot_id}", json={"notes": "hijack"})
        self.assertEqual(resp.status_code, 404)

        resp = self.client.post(
            f"/api/portfolio/lots/{lot_id}/close",
            json={"shares": "1", "sale_price": "1.00", "sale_trade_date": "2026-07-01"},
        )
        self.assertEqual(resp.status_code, 404)

        resp = self.client.delete(f"/api/portfolio/lots/{lot_id}")
        self.assertEqual(resp.status_code, 404)


class Phase3SurfaceGapRouteTest(unittest.TestCase):
    """Phase 3 Step 1 — assert every tool→endpoint coverage-gap route is
    registered on the app (offline; proves assembly + route ordering without
    hitting yfinance/Polygon). The MCP wrappers (Steps 2-3) rewrite their bodies
    to call exactly these paths, so a missing registration would silently break a
    tool after conversion.
    """

    # (method, path) pairs added in Step 1 to close the granular-tool gaps.
    EXPECTED = [
        # prices.py — granular per-indicator analytics
        ("GET", "/api/securities/{ticker}/price-summary"),
        ("GET", "/api/securities/{ticker}/rsi"),
        ("GET", "/api/securities/{ticker}/macd"),
        ("GET", "/api/securities/{ticker}/stochastic"),
        ("GET", "/api/securities/{ticker}/volume"),
        ("GET", "/api/securities/{ticker}/obv"),
        ("GET", "/api/securities/{ticker}/vwap/history"),
        ("GET", "/api/securities/{ticker}/vwap"),
        ("GET", "/api/securities/{ticker}/candlestick"),
        ("GET", "/api/securities/{ticker}/higher-lows"),
        ("GET", "/api/securities/{ticker}/gaps"),
        ("GET", "/api/securities/{ticker}/drawdown"),
        # options.py — full-chain / unusual / delta-OI / gamma-wall / screeners
        ("GET", "/api/securities/{ticker}/options/full-chain"),
        ("GET", "/api/securities/{ticker}/options/unusual-calls"),
        ("GET", "/api/securities/{ticker}/options/delta-adjusted-oi"),
        ("GET", "/api/securities/{ticker}/options/gamma-wall-history"),
        ("GET", "/api/securities/{ticker}/options/screen"),
        ("GET", "/api/options/screen-watchlist"),
        # fundamentals.py — collection-level rankings/cache + batch + calendar
        ("POST", "/api/securities/fundamentals/scores-batch"),
        ("GET", "/api/securities/fundamentals/top"),
        ("GET", "/api/securities/fundamentals/upcoming-earnings"),
        ("GET", "/api/securities/fundamentals/cache-stats"),
        ("GET", "/api/securities/fundamentals/sector-breakdown"),
        ("GET", "/api/securities/fundamentals/score-changes"),
        ("GET", "/api/securities/{ticker}/earnings-calendar"),
        # sentiment.py — symbols list + collect/score + windowed signal + trend
        ("GET", "/api/securities/news/symbols"),
        ("POST", "/api/securities/{ticker}/news/collect"),
        ("GET", "/api/securities/{ticker}/news/sentiment"),
        ("GET", "/api/securities/{ticker}/news/trend"),
        # microstructure.py — three signals exposed individually
        ("GET", "/api/securities/{ticker}/short-interest"),
        ("GET", "/api/securities/{ticker}/dark-pool"),
        ("GET", "/api/securities/{ticker}/bid-ask-spread"),
    ]

    @classmethod
    def setUpClass(cls):
        # The OpenAPI spec is the authoritative registered-route list. (Iterating
        # app.routes is unreliable here: FastAPI's include_router consumes the
        # source routers by reference, so only the module-level create_app() at
        # import time carries the full set — a second call yields a bare app.)
        spec = TestClient(create_app()).get("/openapi.json").json()
        cls.registered = {
            (method.upper(), path)
            for path, ops in spec["paths"].items()
            for method in ops
        }

    def test_all_surface_gap_routes_registered(self):
        missing = [(m, p) for (m, p) in self.EXPECTED if (m, p) not in self.registered]
        self.assertEqual(missing, [], f"unregistered Step 1 routes: {missing}")

    def test_literal_subpaths_precede_templated_routes(self):
        # /vwap/history must be declared before /vwap, and the literal news/
        # fundamentals collection paths before /{ticker}/... — otherwise the
        # template would shadow them. FastAPI matches in declaration order, so
        # verify the order each route was registered on its own router.
        from api.routers import fundamentals, prices, sentiment

        def _order(router, path):
            for i, r in enumerate(router.routes):
                if getattr(r, "path", None) == path:
                    return i
            self.fail(f"{path} not declared on {router}")

        def _before(router, a, b):
            self.assertLess(_order(router, a), _order(router, b), f"{a} must precede {b}")

        _before(
            prices.router,
            "/api/securities/{ticker}/vwap/history",
            "/api/securities/{ticker}/vwap",
        )
        _before(
            sentiment.router,
            "/api/securities/news/symbols",
            "/api/securities/{ticker}/news",
        )
        _before(
            fundamentals.router,
            "/api/securities/fundamentals/top",
            "/api/securities/{ticker}/earnings",
        )


if __name__ == "__main__":
    unittest.main()


class VerticalSpreadCurvesRouteTest(unittest.TestCase):
    """Issue #79: the include_curves flag must round-trip router -> service.
    Seeds a snapshot directly (offline) and exercises the real POST route."""

    SYMBOL = "ZZAPICURVES"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app())

    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        from quantcore.repositories.options_repository import OptionsStore

        OptionsStore().save_full_chain(
            symbol=self.SYMBOL,
            price=166.6,
            bollinger_bands=None,
            expirations_data=[{
                "expiration": "2026-05-22",
                "put_call_ratio": 1.0,
                "calls": {
                    "contracts": [
                        {"strike": 150.0, "last": 14.5, "bid": 13.4, "ask": 15.8,
                         "iv": 70.0, "volume": 90, "open_interest": 740,
                         "in_the_money": True},
                        {"strike": 170.0, "last": 4.1, "bid": 3.5, "ask": 4.7,
                         "iv": 79.0, "volume": 621, "open_interest": 263,
                         "in_the_money": False},
                    ],
                    "total_open_interest": 1003, "total_volume": 711,
                    "avg_iv_pct": 74.5,
                },
                "puts": {"contracts": [], "total_open_interest": 0,
                         "total_volume": 0, "avg_iv_pct": None},
            }],
            captured_at="2026-05-19T12:00:00Z",
        )

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM options_snapshots WHERE symbol = %s", (self.SYMBOL,)
            )
            conn.commit()

    def _post(self, **extra):
        body = {
            "expiration": "2026-05-22",
            "long_strike": 150.0,
            "short_strike": 170.0,
            "kind": "call",
            "max_snapshot_age_minutes": 10_000_000,
            "allow_live_fetch": False,
            **extra,
        }
        return self.client.post(
            f"/api/securities/{self.SYMBOL}/options/vertical-spread", json=body
        )

    def test_default_response_has_no_curves(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNotNone(data["legs"]["long"])
        self.assertNotIn("curves", data)

    def test_include_curves_round_trips_through_the_route(self):
        resp = self._post(include_curves=True)
        self.assertEqual(resp.status_code, 200)
        curves = resp.json()["curves"]
        self.assertEqual(len(curves["prices"]), 121)
        self.assertEqual(len(curves["expiry"]), len(curves["prices"]))
        self.assertEqual(len(curves["now"]), len(curves["prices"]))
        self.assertEqual(curves["params"]["r"], 0.045)
