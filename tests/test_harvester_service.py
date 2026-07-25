import os
import unittest
from contextlib import closing
from pathlib import Path

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.harvester_repository import HarvesterPlanDB, _utc_now_iso  # noqa: E402
from quantcore.services.harvester import HarvesterService  # noqa: E402

# Synthetic ticker + template name that won't collide with real Harvester data.
TEST_SYMBOL = "ZZHARV"
TEST_TEMPLATE = "zz_test_template"


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

    def _rung_status(self, rung_id):
        return self.service.get_rung_by_id(rung_id)["status"]

    # ------------------------------------------------------------------
    def test_get_plan_and_rungs(self):
        instance_id, rung_ids = self._seed_plan()

        plan = self.service.get_plan_by_id(instance_id)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["symbol"], TEST_SYMBOL)
        self.assertEqual(plan["status"], "ACTIVE")

        rungs = self.service.get_rungs_for_plan(instance_id)
        self.assertEqual([r["rung_id"] for r in rungs], rung_ids)
        self.assertEqual([r["rung_index"] for r in rungs], [1, 2])

    def test_display_all_plans_filters_by_status(self):
        instance_id, _ = self._seed_plan(status="ACTIVE")
        active = self.service.display_all_plans(status="ACTIVE")
        self.assertIn(instance_id, [p["instance_id"] for p in active])

        superseded = self.service.display_all_plans(status="SUPERSEDED")
        self.assertNotIn(instance_id, [p["instance_id"] for p in superseded])

    def test_harvest_hit_for_symbol_returns_pending_rungs_at_or_below_price(self):
        instance_id, rung_ids = self._seed_plan()

        # Price reaches only the first rung's target (110).
        hits = self.service.harvest_hit_for_symbol(TEST_SYMBOL, current_price=115.0)
        self.assertEqual([h["rung_id"] for h in hits], [rung_ids[0]])
        self.assertEqual(hits[0]["shares_to_sell"], 10)

        # Price reaches both targets.
        hits = self.service.harvest_hit_for_symbol(TEST_SYMBOL, current_price=125.0)
        self.assertEqual([h["rung_id"] for h in hits], rung_ids)

        # Below the first target — no hits.
        self.assertEqual(self.service.harvest_hit_for_symbol(TEST_SYMBOL, 100.0), [])

    def test_mark_rungs_achieved_updates_status(self):
        _, rung_ids = self._seed_plan()

        updated = self.service.mark_rungs_achieved(
            rung_ids=[rung_ids[0]], trigger_price=111.0
        )
        self.assertEqual(updated, 1)
        self.assertEqual(self._rung_status(rung_ids[0]), "ACHIEVED")
        self.assertEqual(self._rung_status(rung_ids[1]), "PENDING")

        # Re-marking an already-ACHIEVED rung updates nothing (PENDING guard).
        self.assertEqual(
            self.service.mark_rungs_achieved([rung_ids[0]], 111.0), 0
        )

    def test_get_next_actions_returns_first_pending_rung(self):
        instance_id, rung_ids = self._seed_plan()

        actions = [a for a in self.service.get_next_actions() if a["instance_id"] == instance_id]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["rung_id"], rung_ids[0])
        self.assertEqual(actions[0]["rung_index"], 1)

        # Once the first rung is achieved, the next action advances to rung 2.
        self.service.mark_rungs_achieved([rung_ids[0]], 111.0)
        actions = [a for a in self.service.get_next_actions() if a["instance_id"] == instance_id]
        self.assertEqual(actions[0]["rung_id"], rung_ids[1])

    def test_record_execution_marks_rung_executed(self):
        instance_id, rung_ids = self._seed_plan()
        self.service.mark_rungs_achieved([rung_ids[0]], 111.0)

        self.service.record_execution(
            rung_id=rung_ids[0], executed_price=112.0, shares_sold=10, tax_paid=5.0
        )
        rung = self.service.get_rung_by_id(rung_ids[0])
        self.assertEqual(rung["status"], "EXECUTED")
        self.assertEqual(rung["shares_sold_actual"], 10)
        self.assertAlmostEqual(rung["executed_price"], 112.0)
        self.assertAlmostEqual(rung["net_harvest_actual"], 112.0 * 10 - 5.0)

    def test_delete_plan_archives_to_superseded(self):
        instance_id, _ = self._seed_plan()
        self.assertTrue(self.service.delete_plan(instance_id))
        self.assertEqual(self.service.get_plan_by_id(instance_id)["status"], "SUPERSEDED")

    def test_get_alerts_for_plan(self):
        instance_id, rung_ids = self._seed_plan()
        # build_plan would create the alert; seed path doesn't, so refresh it.
        self.service._repo._ensure_next_rung_alert(instance_id)
        alerts = self.service.get_alerts_for_plan(instance_id)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["rung_id"], rung_ids[0])
        self.assertEqual(alerts[0]["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
