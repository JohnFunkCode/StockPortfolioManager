"""DB-backed tests for the Phase-4 OI-change pipeline (issue #93).

Covers OptionsStore.get_oi_timeseries (DISTINCT-ON last-snapshot-per-day
dedupe over TEXT captured_at), OptionsService.get_oi_change_analysis (2×2
classification + put-support/call-wall overlay + graceful <2-dates
degradation), and the Phase-5 save_gex_summary/get_gex_history round-trip
(ladder stripped at the persistence seam).

Runs against the test database only, per test_options_contract_tools.py.
"""

import json
import os
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.options_repository import OptionsStore  # noqa: E402
from quantcore.services.options import OptionsService  # noqa: E402

# Synthetic ticker that won't collide with real cached snapshots.
TEST_SYMBOL = "ZZOICHG"

# The oi-timeseries window is `now() - days`, so seed timestamps relative to
# today: two calendar days inside the default 30-day window.
_NOW = datetime.now(timezone.utc)
DAY1 = (_NOW - timedelta(days=2)).strftime("%Y-%m-%d")
DAY2 = (_NOW - timedelta(days=1)).strftime("%Y-%m-%d")
EXPIRATION = "2099-01-16"


def _contract(strike, oi, iv=50.0):
    return {
        "strike": strike,
        "last": 1.0,
        "bid": 0.9,
        "ask": 1.1,
        "iv": iv,
        "volume": 10,
        "open_interest": oi,
        "in_the_money": False,
    }


def _chain(call_oi: dict, put_oi: dict):
    """expirations_data with one expiration; {strike: oi} per side."""
    return [
        {
            "expiration": EXPIRATION,
            "put_call_ratio": 1.0,
            "calls": {
                "contracts": [_contract(k, v) for k, v in call_oi.items()],
                "total_open_interest": sum(call_oi.values()),
                "total_volume": 10 * len(call_oi),
                "avg_iv_pct": 50.0,
            },
            "puts": {
                "contracts": [_contract(k, v) for k, v in put_oi.items()],
                "total_open_interest": sum(put_oi.values()),
                "total_volume": 10 * len(put_oi),
                "avg_iv_pct": 50.0,
            },
        }
    ]


class OiChangeDbTest(unittest.TestCase):
    def _purge_test_symbol(self):
        with closing(get_connection()) as conn:
            conn.execute(
                "DELETE FROM options_snapshots WHERE symbol = %s", (TEST_SYMBOL,)
            )
            conn.execute(
                "DELETE FROM gex_history WHERE symbol = %s", (TEST_SYMBOL,)
            )
            conn.commit()

    def _store(self):
        self._purge_test_symbol()
        self.addCleanup(self._purge_test_symbol)
        return OptionsStore()

    def _service(self, store):
        return OptionsService(Mock(), Mock(), store, Mock(), Mock())

    def _seed_two_days(self, store):
        """Day 1 spot 100 → day 2 spot 105 (price up), with OI shifts:
        call 120: 100→600 (+500 build, above spot → call wall)
        call 110: 1000→400 (−600 drain → short_covering on an up move)
        call 130: absent→400 (new listing, oi_before 0)
        put   90: 200→550 (+350 build, below spot → put-writing support)
        put   80: 300→350 (+50, below min_oi → excluded)
        """
        first = store.save_full_chain(
            symbol=TEST_SYMBOL, price=100.0, bollinger_bands=None,
            expirations_data=_chain({110.0: 1000, 120.0: 100},
                                    {80.0: 300, 90.0: 200}),
            captured_at=f"{DAY1}T20:00:00Z",
        )
        second = store.save_full_chain(
            symbol=TEST_SYMBOL, price=105.0, bollinger_bands=None,
            expirations_data=_chain({110.0: 400, 120.0: 600, 130.0: 400},
                                    {80.0: 350, 90.0: 550}),
            captured_at=f"{DAY2}T20:00:00Z",
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

    # ------------------------------------------------------------------
    # OptionsStore.get_oi_timeseries
    # ------------------------------------------------------------------

    def test_oi_timeseries_returns_one_row_per_day_and_contract(self):
        store = self._store()
        self._seed_two_days(store)

        rows = store.get_oi_timeseries(TEST_SYMBOL, days=30)
        dates = sorted({r["snap_date"] for r in rows})
        self.assertEqual(dates, [DAY1, DAY2])
        # 4 contracts day 1 + 5 contracts day 2
        self.assertEqual(len(rows), 9)
        day1_call_110 = [r for r in rows
                         if r["snap_date"] == DAY1 and r["kind"] == "call"
                         and float(r["strike"]) == 110.0]
        self.assertEqual(len(day1_call_110), 1)
        self.assertEqual(day1_call_110[0]["open_interest"], 1000)
        self.assertEqual(float(day1_call_110[0]["underlying_price"]), 100.0)

    def test_oi_timeseries_distinct_on_keeps_last_snapshot_of_day(self):
        store = self._store()
        # Two snapshots the SAME day: 12:00 stale OI, 18:00 revised OI.
        store.save_full_chain(
            symbol=TEST_SYMBOL, price=100.0, bollinger_bands=None,
            expirations_data=_chain({120.0: 111}, {90.0: 222}),
            captured_at=f"{DAY1}T12:00:00Z",
        )
        store.save_full_chain(
            symbol=TEST_SYMBOL, price=101.0, bollinger_bands=None,
            expirations_data=_chain({120.0: 333}, {90.0: 444}),
            captured_at=f"{DAY1}T18:00:00Z",
        )

        rows = store.get_oi_timeseries(TEST_SYMBOL, days=30)
        self.assertEqual(len(rows), 2)  # one call + one put, deduped to 18:00
        by_kind = {r["kind"]: r for r in rows}
        self.assertEqual(by_kind["call"]["open_interest"], 333)
        self.assertEqual(by_kind["put"]["open_interest"], 444)
        self.assertEqual(float(by_kind["call"]["underlying_price"]), 101.0)

    def test_oi_timeseries_expiration_filter(self):
        store = self._store()
        self._seed_two_days(store)
        self.assertEqual(
            store.get_oi_timeseries(TEST_SYMBOL, days=30, expiration="1970-01-01"),
            [],
        )
        rows = store.get_oi_timeseries(TEST_SYMBOL, days=30, expiration=EXPIRATION)
        self.assertEqual(len(rows), 9)

    # ------------------------------------------------------------------
    # OptionsService.get_oi_change_analysis (end-to-end against the DB)
    # ------------------------------------------------------------------

    def test_oi_change_analysis_classifies_and_ranks_movers(self):
        store = self._store()
        self._seed_two_days(store)

        result = self._service(store).get_oi_change_analysis(TEST_SYMBOL, days=30)

        self.assertEqual(result["snapshot_dates"], [DAY1, DAY2])
        self.assertEqual(result["snapshot_dates_used"],
                         {"earliest": DAY1, "latest": DAY2, "previous": DAY1})
        self.assertEqual(result["underlying_change_pct"], 5.0)

        builds = result["top_oi_builds"]
        self.assertEqual([(m["kind"], m["strike"], m["oi_change"]) for m in builds],
                         [("call", 120.0, 500), ("call", 130.0, 400), ("put", 90.0, 350)])
        # Price rose → every build is fresh long positioning.
        self.assertTrue(all(m["classification"] == "new_longs" for m in builds))
        self.assertEqual(builds[0]["oi_change_pct"], 500.0)
        # Contract absent from the earliest snapshot: baseline 0, pct undefined.
        self.assertEqual(builds[1]["oi_before"], 0)
        self.assertIsNone(builds[1]["oi_change_pct"])

        drains = result["top_oi_drains"]
        self.assertEqual([(m["kind"], m["strike"], m["oi_change"]) for m in drains],
                         [("call", 110.0, -600)])
        self.assertEqual(drains[0]["classification"], "short_covering")

        # put 80 (+50) stays below min_oi=100 everywhere.
        all_strikes = {m["strike"] for m in builds + drains}
        self.assertNotIn(80.0, all_strikes)

    def test_oi_change_analysis_support_resistance_overlay(self):
        store = self._store()
        self._seed_two_days(store)

        result = self._service(store).get_oi_change_analysis(TEST_SYMBOL, days=30)

        # Put builds below spot (105) → support; call builds above → resistance.
        self.assertEqual(result["put_oi_support_strikes"],
                         [{"strike": 90.0, "oi_build": 350}])
        self.assertEqual(result["call_oi_resistance_strikes"],
                         [{"strike": 120.0, "oi_build": 500},
                          {"strike": 130.0, "oi_build": 400}])

        # With 2 snapshot days, latest-vs-previous == latest-vs-earliest.
        self.assertEqual(result["latest_day_change"], {
            "from_date": DAY1,
            "to_date": DAY2,
            "call_oi_change": (600 - 100) + (400 - 1000) + 400,
            "put_oi_change": (550 - 200) + (350 - 300),
            "net_oi_change": 700,
        })
        self.assertIn(TEST_SYMBOL, result["summary"])

    # ------------------------------------------------------------------
    # Phase 5: gex_history round-trip (save_gex_summary / get_gex_history)
    # ------------------------------------------------------------------

    def test_gex_summary_round_trip_strips_ladder_and_upserts(self):
        store = self._store()
        profile = {
            "price": 105.0,
            "net_gex": 1_234_567.0,
            "zero_gamma_level": 101.5,
            "regime": "positive_gamma",
            "gex_ladder": [{"strike": 100.0, "net_gex": 999.0}],
        }
        store.save_gex_summary(TEST_SYMBOL, profile)
        # Same-day second write must upsert (last write of the day wins).
        store.save_gex_summary(TEST_SYMBOL, {**profile, "net_gex": 2_000_000.0,
                                             "regime": "negative_gamma"})

        rows = store.get_gex_history(TEST_SYMBOL, since_days=7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["net_gex"], 2_000_000.0)
        self.assertEqual(rows[0]["regime"], "negative_gamma")
        self.assertEqual(rows[0]["zero_gamma_level"], 101.5)
        self.assertEqual(rows[0]["price"], 105.0)

        # The per-strike ladder is deliberately NOT persisted.
        with closing(get_connection()) as conn:
            payload = conn.execute(
                "SELECT payload FROM gex_history WHERE symbol = %s",
                (TEST_SYMBOL,),
            ).fetchone()["payload"]
        self.assertNotIn("gex_ladder", json.loads(payload))


class OiChangeDegradationTest(unittest.TestCase):
    """<2 snapshot dates must return the note payload, never raise (no DB)."""

    def _service(self, rows):
        store = Mock()
        store.get_oi_timeseries.return_value = rows
        return OptionsService(Mock(), Mock(), store, Mock(), Mock())

    def test_no_snapshots_returns_note(self):
        result = self._service([]).get_oi_change_analysis("ZZEMPTY")
        self.assertEqual(result["symbol"], "ZZEMPTY")
        self.assertEqual(result["oi_changes"], [])
        self.assertEqual(result["snapshot_dates"], [])
        self.assertIn("get_full_options_chain", result["note"])

    def test_single_snapshot_date_returns_note(self):
        rows = [{"snap_date": "2026-07-14", "underlying_price": 100.0,
                 "expiration": EXPIRATION, "kind": "call", "strike": 110.0,
                 "open_interest": 500, "volume": 10, "implied_vol": 50.0}]
        result = self._service(rows).get_oi_change_analysis("ZZONE")
        self.assertEqual(result["oi_changes"], [])
        self.assertEqual(result["snapshot_dates"], ["2026-07-14"])
        self.assertIn("note", result)


if __name__ == "__main__":
    unittest.main()
