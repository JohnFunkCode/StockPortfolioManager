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

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.harvester_repository import (  # noqa: E402
    HarvesterPlanDB,
    PlanBuildParams,
)

SYM = "ZZHARVREPO"
TEMPLATE = "zz_harv_repo_template"
OWNER = "zzharvowner"
OTHER = "zzharvowner2"


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


class HarvesterRepoFixture:
    """A clean symbol/template, and the one call that builds a plan on it.

    Deliberately **not** a TestCase: a suite that needs only the fixture must be
    able to take it without also inheriting — and so rerunning — another suite's
    test methods.
    """

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

    def build(self, owner=OWNER, **params_kw):
        return self.db.build_plan(
            symbol=SYM, template_name=TEMPLATE,
            params=PlanBuildParams(**params_kw), bars=bars(),
            owner=owner,
        )


class HarvesterRepoTest(HarvesterRepoFixture, unittest.TestCase):
    # -- build_plan ---------------------------------------------------------

    def test_build_plan_creates_instance_and_ladder(self):
        plan = self.build()
        self.assertEqual(plan["symbol"], SYM)
        self.assertIn("instance_id", plan)
        rungs = self.db.get_rungs_for_plan(plan["instance_id"], owner=OWNER)
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
        plans = [p for p in self.db.display_all_plans(status="ACTIVE", owner=OWNER)
                 if p["symbol"] == SYM]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["instance_id"], second["instance_id"])

    # -- CRUD surfaces --------------------------------------------------------

    def test_get_update_delete_lifecycle(self):
        plan = self.build()
        iid = plan["instance_id"]
        fetched = self.db.get_plan_by_id(iid, owner=OWNER)
        self.assertEqual(fetched["symbol"], SYM)
        self.assertIsNone(self.db.get_plan_by_id(99_999_999, owner=OWNER))

        rung = self.db.get_rungs_for_plan(iid, owner=OWNER)[0]
        self.assertEqual(
            self.db.get_rung_by_id(rung["rung_id"], owner=OWNER)["rung_id"], rung["rung_id"]
        )

        self.assertTrue(self.db.update_plan_metadata(iid, notes="reviewed", owner=OWNER))
        self.assertEqual(self.db.get_plan_by_id(iid, owner=OWNER)["notes"], "reviewed")
        self.assertFalse(self.db.update_plan_metadata(iid, owner=OWNER))   # nothing to update

        # delete_plan is a SOFT delete: the row survives as SUPERSEDED.
        self.assertTrue(self.db.delete_plan(iid, owner=OWNER))
        self.assertEqual(self.db.get_plan_by_id(iid, owner=OWNER)["status"], "SUPERSEDED")
        actives = [p for p in self.db.display_all_plans(status="ACTIVE", owner=OWNER)
                   if p["symbol"] == SYM]
        self.assertEqual(actives, [])

    def test_list_symbols_and_dashboard_stats(self):
        self.build()
        symbols = [s for s in self.db.list_all_symbols(owner=OWNER) if s.get("ticker") == SYM
                   or s.get("symbol") == SYM]
        self.assertEqual(len(symbols), 1)
        stats = self.db.get_dashboard_stats(owner=OWNER)
        self.assertIsInstance(stats, dict)
        self.assertTrue(stats)                            # non-empty aggregate

    # -- Harvest evaluation ---------------------------------------------------

    def test_harvest_points_and_rung_achievement(self):
        plan = self.build()
        iid = plan["instance_id"]
        rungs = self.db.get_rungs_for_plan(iid, owner=OWNER)
        first_target = float(rungs[0]["target_price"])

        # Price above the first rung -> the symbol is at a harvest point.
        hits = self.db.symbols_at_harvest_points(
            price_lookup=lambda s: first_target * 1.01, owner=OWNER
        )
        mine = [h for h in hits if h["symbol"] == SYM]
        self.assertGreaterEqual(len(mine), 1)

        # Direct hit check + marking the rung achieved.
        hit = self.db.harvest_hit_for_symbol(SYM, current_price=first_target * 1.01, owner=OWNER)
        self.assertTrue(hit)
        updated = self.db.mark_rungs_achieved(
            [rungs[0]["rung_id"]], trigger_price=first_target * 1.01, owner=OWNER
        )
        self.assertEqual(updated, 1)
        after = self.db.get_rung_by_id(rungs[0]["rung_id"], owner=OWNER)
        self.assertNotEqual(str(after.get("status", "")).upper(), "PENDING")
        self.assertEqual(self.db.mark_rungs_achieved([], trigger_price=1.0, owner=OWNER), 0)

        # Below every rung -> no hit.
        self.assertFalse(
            self.db.harvest_hit_for_symbol(SYM, current_price=first_target * 0.5, owner=OWNER)
        )

    def test_purge_superseded_plans(self):
        self.build()
        self.build()                                       # supersedes the first
        removed = self.db.purge_superseded_plans(owner=OWNER)
        self.assertIsInstance(removed, (int, list))
        actives = [p for p in self.db.display_all_plans(status="ACTIVE", owner=OWNER)
                   if p["symbol"] == SYM]
        self.assertEqual(len(actives), 1)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# HarvesterService scan path (85%-campaign part 10) — real plans in the test
# DB, price feed stubbed at the _latest_close seam.
# ---------------------------------------------------------------------------

from unittest.mock import Mock, patch  # noqa: E402

from quantcore.services.harvester import HarvesterService  # noqa: E402


class HarvesterScanTest(HarvesterRepoTest):
    def make_service(self):
        return HarvesterService(self.db, yfinance_gateway=Mock())

    def test_scan_fires_on_target_touch_and_marks_the_rung(self):
        plan = self.build()
        rungs = self.db.get_rungs_for_plan(plan["instance_id"], owner=OWNER)
        first = rungs[0]
        service = self.make_service()

        with patch.object(service, "_latest_close",
                          return_value=float(first["target_price"]) * 1.001):
            fired = service.scan_and_fire_alerts(owner=OWNER)

        mine = [f for f in fired if f["symbol"] == SYM]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["rung_id"], first["rung_id"])
        self.assertEqual(mine[0]["target_price"], float(first["target_price"]))
        after = self.db.get_rung_by_id(first["rung_id"], owner=OWNER)
        self.assertNotEqual(str(after.get("status", "")).upper(), "PENDING")

        # Second scan at the same price: rung 1 is no longer pending and rung 2
        # is above the price, so nothing fires for this symbol.
        with patch.object(service, "_latest_close",
                          return_value=float(first["target_price"]) * 1.001):
            again = service.scan_and_fire_alerts(owner=OWNER)
        self.assertEqual([f for f in again if f["symbol"] == SYM], [])

    def test_scan_holds_below_target_or_without_price(self):
        plan = self.build()
        first = self.db.get_rungs_for_plan(plan["instance_id"], owner=OWNER)[0]
        service = self.make_service()
        with patch.object(service, "_latest_close",
                          return_value=float(first["target_price"]) * 0.5):
            self.assertEqual(
                [f for f in service.scan_and_fire_alerts(owner=OWNER) if f["symbol"] == SYM], []
            )
        with patch.object(service, "_latest_close", return_value=None):
            self.assertEqual(
                [f for f in service.scan_and_fire_alerts(owner=OWNER) if f["symbol"] == SYM], []
            )

    def test_latest_close_seam_degrades(self):
        gateway = Mock()
        service = HarvesterService(self.db, yfinance_gateway=gateway)
        gateway.fetch_history.return_value = bars(n=5)
        self.assertAlmostEqual(service.poll_latest_close("ZZX"),
                               float(bars(n=5)["Close"].iloc[-1]))
        gateway.fetch_history.return_value = bars(n=5).iloc[0:0]   # empty
        self.assertIsNone(service.poll_latest_close("ZZX"))
        gateway.fetch_history.side_effect = RuntimeError("yahoo down")
        self.assertIsNone(service.poll_latest_close("ZZX"))


# ---------------------------------------------------------------------------
# Owner isolation (#147 Part H1) — two owners, one ticker.
#
# Isolation is enforced in SQL rather than in the routes, so these tests drive
# the repository/service directly: if a predicate is missing from a query, it
# shows up here as one owner reading or mutating the other's plan, whatever the
# HTTP layer does.
# ---------------------------------------------------------------------------


class HarvesterOwnerIsolationTest(HarvesterRepoTest):
    def setUp(self):
        super().setUp()
        self.mine = self.build(owner=OWNER)
        self.theirs = self.build(owner=OTHER)
        self.service = HarvesterService(self.db, yfinance_gateway=Mock())

    def test_two_owners_hold_active_plans_on_the_same_symbol(self):
        # The old unique index (one ACTIVE plan per symbol) made this
        # impossible; V7 moved it to (owner, symbol_id).
        self.assertNotEqual(self.mine["instance_id"], self.theirs["instance_id"])
        for owner, expected in ((OWNER, self.mine), (OTHER, self.theirs)):
            actives = [p for p in self.db.display_all_plans(status="ACTIVE", owner=owner)
                       if p["symbol"] == SYM]
            self.assertEqual(len(actives), 1)
            self.assertEqual(actives[0]["instance_id"], expected["instance_id"])
            self.assertEqual(actives[0]["owner"], owner)

    def test_build_supersedes_only_the_builders_own_plan(self):
        rebuilt = self.build(owner=OTHER)
        self.assertEqual(
            self.db.get_plan_by_id(self.theirs["instance_id"], owner=OTHER)["status"],
            "SUPERSEDED",
        )
        self.assertEqual(
            self.db.get_plan_by_id(self.mine["instance_id"], owner=OWNER)["status"],
            "ACTIVE",
        )
        self.assertNotEqual(rebuilt["instance_id"], self.mine["instance_id"])

    def test_wrong_owner_reads_come_back_empty(self):
        iid = self.mine["instance_id"]
        rung_id = self.db.get_rungs_for_plan(iid, owner=OWNER)[0]["rung_id"]

        self.assertIsNone(self.db.get_plan_by_id(iid, owner=OTHER))
        self.assertEqual(self.db.get_rungs_for_plan(iid, owner=OTHER), [])
        self.assertIsNone(self.db.get_rung_by_id(rung_id, owner=OTHER))
        self.assertEqual(self.db.get_alerts_for_plan(iid, owner=OTHER), [])

    def test_wrong_owner_mutations_change_nothing(self):
        iid = self.mine["instance_id"]
        rung_id = self.db.get_rungs_for_plan(iid, owner=OWNER)[0]["rung_id"]
        target = float(self.db.get_rung_by_id(rung_id, owner=OWNER)["target_price"])

        self.assertFalse(
            self.db.update_plan_metadata(iid, notes="not yours", owner=OTHER)
        )
        self.assertFalse(self.db.delete_plan(iid, owner=OTHER))
        self.assertEqual(
            self.db.mark_rungs_achieved([rung_id], trigger_price=target, owner=OTHER), 0
        )
        self.service.record_execution(
            rung_id=rung_id, executed_price=target, shares_sold=1,
            notes="not yours", owner=OTHER,
        )

        plan = self.db.get_plan_by_id(iid, owner=OWNER)
        self.assertEqual(plan["status"], "ACTIVE")
        self.assertIsNone(plan["notes"])
        rung = self.db.get_rung_by_id(rung_id, owner=OWNER)
        self.assertEqual(str(rung["status"]).upper(), "PENDING")
        self.assertNotEqual(rung.get("notes"), "not yours")

    def test_harvest_scan_only_sees_the_scanning_owners_plans(self):
        rungs = self.db.get_rungs_for_plan(self.mine["instance_id"], owner=OWNER)
        price = float(rungs[0]["target_price"]) * 1.01

        hits = self.db.harvest_hit_for_symbol(SYM, current_price=price, owner=OWNER)
        self.assertTrue(hits)
        self.assertTrue(
            all(h["instance_id"] == self.mine["instance_id"] for h in hits)
        )

        at_points = [
            h for h in self.db.symbols_at_harvest_points(
                price_lookup=lambda s: price, owner=OWNER
            )
            if h["symbol"] == SYM
        ]
        self.assertEqual(len(at_points), 1)

        with patch.object(self.service, "_latest_close", return_value=price):
            fired = [f for f in self.service.scan_and_fire_alerts(owner=OWNER)
                     if f["symbol"] == SYM]
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["rung_id"], rungs[0]["rung_id"])
        # The other owner's identically-priced rung is untouched.
        theirs = self.db.get_rungs_for_plan(self.theirs["instance_id"], owner=OTHER)[0]
        self.assertEqual(str(theirs["status"]).upper(), "PENDING")

    def test_next_actions_and_dashboard_are_scoped(self):
        # get_next_actions carries no symbol, so scope is checked on instance ids.
        ids = {a["instance_id"] for a in self.service.get_next_actions(owner=OWNER)}
        self.assertIn(self.mine["instance_id"], ids)
        self.assertNotIn(self.theirs["instance_id"], ids)

        stats = self.db.get_dashboard_stats(owner=OWNER)
        other_stats = self.db.get_dashboard_stats(owner=OTHER)
        self.assertEqual(stats["active_plans"], 1)
        self.assertEqual(other_stats["active_plans"], 1)

    def test_symbol_list_never_links_to_another_owners_plan(self):
        # The symbol row is shared, but active_plan_id must be this owner's --
        # otherwise the UI offers a link that 404s.
        for owner, plan in ((OWNER, self.mine), (OTHER, self.theirs)):
            row = [s for s in self.db.list_all_symbols(owner=owner)
                   if s.get("ticker") == SYM or s.get("symbol") == SYM]
            self.assertEqual(len(row), 1)
            self.assertEqual(row[0]["active_plan_id"], plan["instance_id"])

    # -- the holding invariant's SQL (issue #147 Parts H5/H6) --------------

    def test_active_plan_ids_maps_only_the_owners_active_plans(self):
        self.assertEqual(self.db.active_plan_ids(owner=OWNER)[SYM], self.mine["instance_id"])
        self.assertEqual(self.db.active_plan_ids(owner=OTHER)[SYM], self.theirs["instance_id"])

        # Archiving drops the symbol rather than leaving the chip pointing at a
        # ladder nobody runs; the other owner's is untouched.
        self.db.delete_plan(self.mine["instance_id"], owner=OWNER)
        self.assertNotIn(SYM, self.db.active_plan_ids(owner=OWNER))
        self.assertIn(SYM, self.db.active_plan_ids(owner=OTHER))

    def test_close_active_plan_closes_one_owners_ladder_only(self):
        closed = self.db.close_active_plan_for_symbol(
            SYM.lower(), owner=OWNER, reason="sold out"
        )
        self.assertEqual(closed, 1)

        mine = self.db.get_plan_by_id(self.mine["instance_id"], owner=OWNER)
        # CLOSED, not SUPERSEDED -- nothing replaced this plan.
        self.assertEqual(mine["status"], "CLOSED")
        self.assertIn("sold out", mine["notes"])
        self.assertEqual(
            self.db.get_plan_by_id(self.theirs["instance_id"], owner=OTHER)["status"],
            "ACTIVE",
        )

    def test_close_active_plan_is_a_no_op_when_nothing_is_active(self):
        self.assertEqual(self.db.close_active_plan_for_symbol(SYM, owner=OWNER), 1)
        # Selling the rest of an already-flat position must not keep writing.
        self.assertEqual(self.db.close_active_plan_for_symbol(SYM, owner=OWNER), 0)
        self.assertEqual(self.db.close_active_plan_for_symbol("ZZNOSUCH", owner=OWNER), 0)

    def test_close_active_plan_appends_rather_than_overwrites_notes(self):
        self.db.update_plan_metadata(
            self.mine["instance_id"], notes="hand-tuned", owner=OWNER
        )
        self.db.close_active_plan_for_symbol(SYM, owner=OWNER, reason="position removed")
        notes = self.db.get_plan_by_id(self.mine["instance_id"], owner=OWNER)["notes"]
        self.assertEqual(notes, "hand-tuned | position removed")

    def test_close_active_plan_without_a_reason_leaves_notes_alone(self):
        self.db.update_plan_metadata(
            self.mine["instance_id"], notes="hand-tuned", owner=OWNER
        )
        self.assertEqual(self.db.close_active_plan_for_symbol(SYM, owner=OWNER), 1)
        plan = self.db.get_plan_by_id(self.mine["instance_id"], owner=OWNER)
        self.assertEqual(plan["status"], "CLOSED")
        self.assertEqual(plan["notes"], "hand-tuned")

    def test_purge_leaves_the_other_owners_superseded_plans_alone(self):
        self.build(owner=OWNER)          # supersedes self.mine
        self.build(owner=OTHER)          # supersedes self.theirs
        self.db.purge_superseded_plans(owner=OWNER)
        self.assertIsNone(self.db.get_plan_by_id(self.mine["instance_id"], owner=OWNER))
        self.assertEqual(
            self.db.get_plan_by_id(self.theirs["instance_id"], owner=OTHER)["status"],
            "SUPERSEDED",
        )


# Takes the fixture, not HarvesterRepoTest: added to that class these two cases
# would run three times (the scan and owner suites subclass it), and subclassing
# it here would instead rerun its whole suite under this name.
class HarvesterAdjCloseGapTest(HarvesterRepoFixture, unittest.TestCase):
    """A read window that reaches rows the fetch never covered (issue: the
    Create Plan dialog died on `float() argument must be ... not 'NoneType'`).
    """

    def setUp(self):
        super().setUp()
        self.addCleanup(self._purge_bars)
        self._purge_bars()

    def _purge_bars(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM ohlcv WHERE symbol = %s AND interval = '1d'", (SYM,)
            )
            conn.commit()

    def _seed_unadjusted_rows(self, n=40, first_date="2024-01-02"):
        """Rows as the prices cache writes them: a close, and adj_close NULL.

        Older than anything `bars()` supplies, so they only enter the picture
        when the read window is wider than the fetch reached.
        """
        with closing(get_connection()) as conn:
            for day in pd.bdate_range(start=first_date, periods=n):
                conn.execute(
                    "INSERT INTO ohlcv (symbol, interval, ts, open, high, low, "
                    "close, volume, status, adj_close, data_vendor, ingested_at) "
                    "VALUES (%s,'1d',%s,%s,%s,%s,%s,%s,'CLOSED',NULL,'yfinance',0) "
                    "ON CONFLICT (symbol, interval, ts) DO NOTHING",
                    (SYM, int(day.timestamp()), 10.0, 10.5, 9.5, 10.0, 1_000_000),
                )
            conn.commit()

    def test_a_read_window_reaching_unadjusted_rows_says_so(self):
        # Splicing `close` in instead would be worse than useless: an
        # unadjusted bar next to adjusted ones is a fake step, and it inflates
        # the volatility that sizes the whole ladder. So this must be a legible
        # refusal — which POST /api/plans already maps to 422.
        self._seed_unadjusted_rows()
        with self.assertRaises(RuntimeError) as ctx:
            self.build(history_window_days=520)      # bars() supplies only 500
        message = str(ctx.exception)
        self.assertIn("adjusted close", message)
        self.assertIn(SYM, message)

    def test_unadjusted_rows_outside_the_window_are_harmless(self):
        self._seed_unadjusted_rows()
        plan = self.build(history_window_days=360)
        self.assertIn("instance_id", plan)
