import os
import unittest
from contextlib import closing
from pathlib import Path

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.harvester_repository import (  # noqa: E402
    HarvesterPlanDB,
    PlanBuildParams,
    _utc_now_iso,
)
from quantcore.services.harvester import HarvesterService  # noqa: E402

# Synthetic ticker + template name that won't collide with real Harvester data.
TEST_SYMBOL = "ZZHARV"
TEST_TEMPLATE = "zz_test_template"
# Plans are owned since #147 Part H1; the suite runs as a synthetic owner so it
# can never read or supersede a real one.
OWNER = "zzharvsvcowner"


class HarvesterServiceTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.service = HarvesterService(HarvesterPlanDB())

    def _purge(self):
        with closing(get_connection()) as conn:
            # Deleting the symbol cascades to plan_instances -> rungs -> alerts.
            conn.execute(
                "DELETE FROM plan_instances WHERE symbol_id IN "
                "(SELECT symbol_id FROM symbols WHERE ticker = %s)",
                (TEST_SYMBOL,),
            )
            conn.execute("DELETE FROM symbols WHERE ticker = %s", (TEST_SYMBOL,))
            conn.execute("DELETE FROM plan_templates WHERE name = %s", (TEST_TEMPLATE,))
            conn.commit()

    def _seed_plan(self, status="ACTIVE", rungs=((1, 110.0, 10), (2, 120.0, 10))):
        """Insert a template + symbol + plan_instance + PENDING rungs directly.

        Returns (instance_id, [rung_id, ...]) ordered by rung_index. Bypasses
        build_plan() so the test stays deterministic and offline (no yfinance).
        """
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
                  template_id, symbol_id, owner, status, created_at, asof_date,
                  price_asof, shares_initial, v0_floor, capital_at_risk,
                  history_end_date, history_window_days,
                  r_daily, annual_vol, h_threshold, n_iterations
                ) VALUES (
                  :template_id, :symbol_id, :owner, :status, :now, :now,
                  100.0, 20, 2000.0, 2000.0,
                  '2026-06-01', 360,
                  0.0005, 0.25, 0.1, 4
                )
                RETURNING instance_id
                """,
                {
                    "template_id": template_id,
                    "symbol_id": symbol_id,
                    "owner": OWNER,
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

    def _rung_status(self, rung_id):
        return self.service.get_rung_by_id(rung_id, owner=OWNER)["status"]

    # ------------------------------------------------------------------
    def test_get_plan_and_rungs(self):
        instance_id, rung_ids = self._seed_plan()

        plan = self.service.get_plan_by_id(instance_id, owner=OWNER)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["symbol"], TEST_SYMBOL)
        self.assertEqual(plan["status"], "ACTIVE")

        rungs = self.service.get_rungs_for_plan(instance_id, owner=OWNER)
        self.assertEqual([r["rung_id"] for r in rungs], rung_ids)
        self.assertEqual([r["rung_index"] for r in rungs], [1, 2])

    def test_display_all_plans_filters_by_status(self):
        instance_id, _ = self._seed_plan(status="ACTIVE")
        active = self.service.display_all_plans(status="ACTIVE", owner=OWNER)
        self.assertIn(instance_id, [p["instance_id"] for p in active])

        superseded = self.service.display_all_plans(status="SUPERSEDED", owner=OWNER)
        self.assertNotIn(instance_id, [p["instance_id"] for p in superseded])

    def test_harvest_hit_for_symbol_returns_pending_rungs_at_or_below_price(self):
        instance_id, rung_ids = self._seed_plan()

        # Price reaches only the first rung's target (110).
        hits = self.service.harvest_hit_for_symbol(TEST_SYMBOL, current_price=115.0, owner=OWNER)
        self.assertEqual([h["rung_id"] for h in hits], [rung_ids[0]])
        self.assertEqual(hits[0]["shares_to_sell"], 10)

        # Price reaches both targets.
        hits = self.service.harvest_hit_for_symbol(TEST_SYMBOL, current_price=125.0, owner=OWNER)
        self.assertEqual([h["rung_id"] for h in hits], rung_ids)

        # Below the first target — no hits.
        self.assertEqual(self.service.harvest_hit_for_symbol(TEST_SYMBOL, 100.0, owner=OWNER), [])

    def test_mark_rungs_achieved_updates_status(self):
        _, rung_ids = self._seed_plan()

        updated = self.service.mark_rungs_achieved(
            rung_ids=[rung_ids[0]], trigger_price=111.0, owner=OWNER
        )
        self.assertEqual(updated, 1)
        self.assertEqual(self._rung_status(rung_ids[0]), "ACHIEVED")
        self.assertEqual(self._rung_status(rung_ids[1]), "PENDING")

        # Re-marking an already-ACHIEVED rung updates nothing (PENDING guard).
        self.assertEqual(
            self.service.mark_rungs_achieved([rung_ids[0]], 111.0, owner=OWNER), 0
        )

    def test_get_next_actions_returns_first_pending_rung(self):
        instance_id, rung_ids = self._seed_plan()

        actions = [a for a in self.service.get_next_actions(owner=OWNER) if a["instance_id"] == instance_id]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["rung_id"], rung_ids[0])
        self.assertEqual(actions[0]["rung_index"], 1)

        # Once the first rung is achieved, the next action advances to rung 2.
        self.service.mark_rungs_achieved([rung_ids[0]], 111.0, owner=OWNER)
        actions = [a for a in self.service.get_next_actions(owner=OWNER) if a["instance_id"] == instance_id]
        self.assertEqual(actions[0]["rung_id"], rung_ids[1])

    def test_record_execution_marks_rung_executed(self):
        instance_id, rung_ids = self._seed_plan()
        self.service.mark_rungs_achieved([rung_ids[0]], 111.0, owner=OWNER)

        self.service.record_execution(
            rung_id=rung_ids[0], executed_price=112.0, shares_sold=10, tax_paid=5.0,
            owner=OWNER,
        )
        rung = self.service.get_rung_by_id(rung_ids[0], owner=OWNER)
        self.assertEqual(rung["status"], "EXECUTED")
        self.assertEqual(rung["shares_sold_actual"], 10)
        self.assertAlmostEqual(rung["executed_price"], 112.0)
        self.assertAlmostEqual(rung["net_harvest_actual"], 112.0 * 10 - 5.0)

    def test_delete_plan_archives_to_superseded(self):
        instance_id, _ = self._seed_plan()
        self.assertTrue(self.service.delete_plan(instance_id, owner=OWNER))
        self.assertEqual(self.service.get_plan_by_id(instance_id, owner=OWNER)["status"], "SUPERSEDED")

    def test_get_alerts_for_plan(self):
        instance_id, rung_ids = self._seed_plan()
        # build_plan would create the alert; seed path doesn't, so refresh it.
        self.service._repo._ensure_next_rung_alert(instance_id, owner=OWNER)
        alerts = self.service.get_alerts_for_plan(instance_id, owner=OWNER)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["rung_id"], rung_ids[0])
        self.assertEqual(alerts[0]["status"], "ACTIVE")

    def test_ensure_next_rung_alert_writes_nothing_for_another_owner(self):
        """The helper's own predicate, not just its callers, is what stops the write.

        Every caller reaches it with an already-scoped instance_id, so this
        asserts the containment directly rather than through a route.
        """
        instance_id, _ = self._seed_plan()
        self.service._repo._ensure_next_rung_alert(instance_id, owner="zznotthisowner")
        self.assertEqual(self.service.get_alerts_for_plan(instance_id, owner=OWNER), [])


class FakePortfolioRepo:
    """Stand-in for PortfolioRepository: canned OPEN lots, no DB."""

    def __init__(self, by_owner):
        self._by_owner = by_owner
        self.calls = []

    def list_positions(self, owner, status="OPEN"):
        self.calls.append((owner, status))
        return [{"symbol": s} for s in self._by_owner.get(owner, [])]


class FakePlanRepo:
    """Stand-in for HarvesterPlanDB, recording what the service asked for."""

    def __init__(self, plans=()):
        self._plans = [dict(p) for p in plans]
        self.built = []
        self.closed = []

    def build_plan(self, **kwargs):
        self.built.append(kwargs)
        return {"instance_id": 1, "symbol": kwargs["symbol"]}

    def display_all_plans(self, status="ACTIVE", *, owner):
        return [dict(p) for p in self._plans]

    def close_active_plan_for_symbol(self, ticker, *, owner, reason=None):
        self.closed.append((ticker, owner, reason))
        return 1

    def active_plan_ids(self, *, owner):
        return {"ZZHELD": 5}


class ExplodingGateway:
    """A fetch is a failure here: the invariant has to refuse *before* paying
    for two years of bars, not after."""

    def fetch_history(self, *args, **kwargs):
        raise AssertionError("fetched history for a symbol the owner does not hold")


class EmptyBarsGateway:
    def __init__(self):
        self.calls = []

    def fetch_history(self, symbol, *args, **kwargs):
        import pandas as pd

        self.calls.append(symbol)
        return pd.DataFrame()


class HoldingInvariantTest(unittest.TestCase):
    """Issue #147 Part H5/H7 — the entry half of the holding invariant and the
    orphan flag. Repository-faked: what is being pinned is the service's
    decision, and the SQL underneath is covered in test_harvester_repository_db.
    """

    HOLDER = "zzholder"
    OTHER = "zzotherholder"

    def _service(self, held=None, gateway=None, plans=(), with_portfolio=True):
        self.plan_repo = FakePlanRepo(plans)
        self.portfolio_repo = FakePortfolioRepo(held or {}) if with_portfolio else None
        return HarvesterService(
            self.plan_repo,
            yfinance_gateway=gateway or EmptyBarsGateway(),
            portfolio_repository=self.portfolio_repo,
        )

    # -- entry half ---------------------------------------------------------
    def test_build_plan_refuses_a_symbol_with_no_open_lot(self):
        service = self._service(held={self.HOLDER: ["ZZOTHER"]}, gateway=ExplodingGateway())
        with self.assertRaises(RuntimeError) as ctx:
            service.build_plan("ZZHELD", TEST_TEMPLATE, PlanBuildParams(), owner=self.HOLDER)
        # The message reaches the user through the route's 422, so it has to
        # name both the symbol and whose portfolio was checked.
        self.assertIn("ZZHELD", str(ctx.exception))
        self.assertIn(self.HOLDER, str(ctx.exception))
        self.assertEqual(self.plan_repo.built, [])

    def test_build_plan_refuses_when_only_another_owner_holds_it(self):
        service = self._service(
            held={self.OTHER: ["ZZHELD"]}, gateway=ExplodingGateway()
        )
        with self.assertRaises(RuntimeError):
            service.build_plan("ZZHELD", TEST_TEMPLATE, PlanBuildParams(), owner=self.HOLDER)

    def test_build_plan_proceeds_when_the_owner_holds_the_symbol(self):
        gateway = EmptyBarsGateway()
        service = self._service(held={self.HOLDER: ["ZZHELD"]}, gateway=gateway)
        service.build_plan("  zzheld  ", TEST_TEMPLATE, PlanBuildParams(), owner=self.HOLDER)
        # Normalised once, up front — the guard and the fetch see the same symbol.
        self.assertEqual(gateway.calls, ["ZZHELD"])
        self.assertEqual(self.plan_repo.built[0]["symbol"], "ZZHELD")
        self.assertEqual(self.portfolio_repo.calls, [(self.HOLDER, "OPEN")])

    def test_without_a_portfolio_repository_the_invariant_is_not_enforced(self):
        # A bare unit-test construction must not start refusing every build;
        # the registry is what wires the real check.
        gateway = EmptyBarsGateway()
        service = self._service(gateway=gateway, with_portfolio=False)
        service.build_plan("ZZHELD", TEST_TEMPLATE, PlanBuildParams(), owner=self.HOLDER)
        self.assertEqual(gateway.calls, ["ZZHELD"])

    # -- orphan flag --------------------------------------------------------
    def test_display_all_plans_flags_only_the_ladders_with_nothing_under_them(self):
        service = self._service(
            held={self.HOLDER: ["ZZHELD"]},
            plans=[{"symbol": "ZZHELD"}, {"symbol": "ZZGONE"}],
        )
        flags = {p["symbol"]: p["in_portfolio"] for p in service.display_all_plans(owner=self.HOLDER)}
        self.assertEqual(flags, {"ZZHELD": True, "ZZGONE": False})

    def test_display_all_plans_says_nothing_when_it_cannot_tell(self):
        # None, not False: the page flags orphans on an explicit False, and
        # "no portfolio repository" is not evidence the shares are gone.
        service = self._service(plans=[{"symbol": "ZZHELD"}], with_portfolio=False)
        self.assertIsNone(service.display_all_plans(owner=self.HOLDER)[0]["in_portfolio"])

    def test_display_all_plans_matches_holdings_case_insensitively(self):
        service = self._service(
            held={self.HOLDER: ["zzheld"]}, plans=[{"symbol": "ZZHELD"}]
        )
        self.assertTrue(service.display_all_plans(owner=self.HOLDER)[0]["in_portfolio"])

    # -- passthroughs the portfolio service and page depend on --------------
    def test_close_and_active_ids_pass_through_to_the_repository(self):
        service = self._service()
        self.assertEqual(
            service.close_active_plan_for_symbol("ZZHELD", owner=self.HOLDER, reason="sold"), 1
        )
        self.assertEqual(self.plan_repo.closed, [("ZZHELD", self.HOLDER, "sold")])
        self.assertEqual(service.active_plan_ids(owner=self.HOLDER), {"ZZHELD": 5})


if __name__ == "__main__":
    unittest.main()
