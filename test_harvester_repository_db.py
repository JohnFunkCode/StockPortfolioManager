"""Seeded-test-DB tests for HarvesterPlanDB's build/CRUD/harvest surfaces
(85%-campaign; the service-level scan paths are pinned in
test_harvester_service.py — this file exercises the repository directly,
including the real build_plan with injected bars per the issue-#74 seam).
"""
import os
import unittest
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

# Swap in the test DSN BEFORE quantcore.db is imported (frozen at import).
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.harvester_repository import (  # noqa: E402
    HarvesterPlanDB,
    PlanBuildParams,
)

SYM = "ZZHARVREPO"
TEMPLATE = "zz_harv_repo_template"


def bars(n=500, start=50.0, end=150.0):
    idx = pd.bdate_range(end="2026-07-17", periods=n)  # a Friday — bdate-safe
    closes = np.linspace(start, end, n) + 2.0 * np.sin(np.arange(n) / 7.0)
    return pd.DataFrame(
        {
            "Open": closes * 0.999,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Adj Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


class HarvesterRepoTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.db = HarvesterPlanDB()

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM plan_instances WHERE symbol_id IN "
                "(SELECT symbol_id FROM symbols WHERE ticker = %s)", (SYM,)
            )
            conn.execute("DELETE FROM symbols WHERE ticker = %s", (SYM,))
            conn.execute("DELETE FROM plan_templates WHERE name = %s", (TEMPLATE,))
            conn.commit()

    def build(self, **params_kw):
        return self.db.build_plan(
            symbol=SYM, template_name=TEMPLATE,
            params=PlanBuildParams(**params_kw), bars=bars(),
        )

    # -- build_plan ---------------------------------------------------------

    def test_build_plan_creates_instance_and_ladder(self):
        plan = self.build()
        self.assertEqual(plan["symbol"], SYM)
        self.assertIn("instance_id", plan)
        rungs = self.db.get_rungs_for_plan(plan["instance_id"])
        self.assertGreaterEqual(len(rungs), 1)
        prices = [float(r["target_price"]) for r in rungs]
        self.assertEqual(prices, sorted(prices))          # ascending ladder
        # H respects the configured bounds.
        self.assertGreaterEqual(float(plan["H"]), 0.05)
        self.assertLessEqual(float(plan["H"]), 0.30)

    def test_rebuild_supersedes_previous_active_plan(self):
        first = self.build()
        second = self.build()
        self.assertNotEqual(first["instance_id"], second["instance_id"])
        plans = [p for p in self.db.display_all_plans(status="ACTIVE")
                 if p["symbol"] == SYM]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["instance_id"], second["instance_id"])

    # -- CRUD surfaces --------------------------------------------------------

    def test_get_update_delete_lifecycle(self):
        plan = self.build()
        iid = plan["instance_id"]
        fetched = self.db.get_plan_by_id(iid)
        self.assertEqual(fetched["symbol"], SYM)
        self.assertIsNone(self.db.get_plan_by_id(99_999_999))

        rung = self.db.get_rungs_for_plan(iid)[0]
        self.assertEqual(
            self.db.get_rung_by_id(rung["rung_id"])["rung_id"], rung["rung_id"]
        )

        self.assertTrue(self.db.update_plan_metadata(iid, notes="reviewed"))
        self.assertEqual(self.db.get_plan_by_id(iid)["notes"], "reviewed")
        self.assertFalse(self.db.update_plan_metadata(iid))   # nothing to update

        # delete_plan is a SOFT delete: the row survives as SUPERSEDED.
        self.assertTrue(self.db.delete_plan(iid))
        self.assertEqual(self.db.get_plan_by_id(iid)["status"], "SUPERSEDED")
        actives = [p for p in self.db.display_all_plans(status="ACTIVE")
                   if p["symbol"] == SYM]
        self.assertEqual(actives, [])

    def test_list_symbols_and_dashboard_stats(self):
        self.build()
        symbols = [s for s in self.db.list_all_symbols() if s.get("ticker") == SYM
                   or s.get("symbol") == SYM]
        self.assertEqual(len(symbols), 1)
        stats = self.db.get_dashboard_stats()
        self.assertIsInstance(stats, dict)
        self.assertTrue(stats)                            # non-empty aggregate

    # -- Harvest evaluation ---------------------------------------------------

    def test_harvest_points_and_rung_achievement(self):
        plan = self.build()
        iid = plan["instance_id"]
        rungs = self.db.get_rungs_for_plan(iid)
        first_target = float(rungs[0]["target_price"])

        # Price above the first rung -> the symbol is at a harvest point.
        hits = self.db.symbols_at_harvest_points(
            price_lookup=lambda s: first_target * 1.01
        )
        mine = [h for h in hits if h["symbol"] == SYM]
        self.assertGreaterEqual(len(mine), 1)

        # Direct hit check + marking the rung achieved.
        hit = self.db.harvest_hit_for_symbol(SYM, current_price=first_target * 1.01)
        self.assertTrue(hit)
        updated = self.db.mark_rungs_achieved(
            [rungs[0]["rung_id"]], trigger_price=first_target * 1.01
        )
        self.assertEqual(updated, 1)
        after = self.db.get_rung_by_id(rungs[0]["rung_id"])
        self.assertNotEqual(str(after.get("status", "")).upper(), "PENDING")
        self.assertEqual(self.db.mark_rungs_achieved([], trigger_price=1.0), 0)

        # Below every rung -> no hit.
        self.assertFalse(
            self.db.harvest_hit_for_symbol(SYM, current_price=first_target * 0.5)
        )

    def test_purge_superseded_plans(self):
        self.build()
        self.build()                                       # supersedes the first
        removed = self.db.purge_superseded_plans()
        self.assertIsInstance(removed, (int, list))
        actives = [p for p in self.db.display_all_plans(status="ACTIVE")
                   if p["symbol"] == SYM]
        self.assertEqual(len(actives), 1)


if __name__ == "__main__":
    unittest.main()
