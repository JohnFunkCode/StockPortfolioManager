"""The orphan-bar repair, and the upsert behaviour that makes it necessary.

Test database only. Rows use synthetic symbols; the manifest is only read.
"""
import datetime as dt
import importlib.util
import time
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from quantcore.db_safety import assert_not_production

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.ohlcv_repository import store_bars  # noqa: E402

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "repair_ohlcv_orphan_bars.py"
_spec = importlib.util.spec_from_file_location("repair_ohlcv_orphan_bars", _SCRIPT)
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)

VICTIM = "ZZORPHAN"
SOURCE = "ZZSOURCE"
HOLIDAY = "2026-05-25"


def _frame(*dates, close=100.0, volume=1000):
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": volume},
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in dates]),
    )


def _purge():
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM ohlcv WHERE symbol = ANY(%s)", ([VICTIM, SOURCE],))
        conn.execute("DELETE FROM fetch_log WHERE symbol = ANY(%s)", ([VICTIM, SOURCE],))
        conn.commit()


def _dates(symbol):
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT ts FROM ohlcv WHERE symbol = %s ORDER BY ts", (symbol,)
        ).fetchall()
    return [dt.datetime.fromtimestamp(r["ts"], dt.timezone.utc).date().isoformat() for r in rows]


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.rows = repair.load_manifest()
        self.keys = {(r["symbol"], r["date"]) for r in self.rows}

    def test_reported_rows_are_in_it(self):
        self.assertIn(("JCI", HOLIDAY), self.keys)
        self.assertIn(("DELL", HOLIDAY), self.keys)

    def test_each_row_names_another_symbol_as_its_source(self):
        self.assertEqual(len(self.keys), len(self.rows))
        for row in self.rows:
            self.assertTrue(row["source"])
            self.assertNotEqual(row["source"], row["symbol"])

    def test_a_source_bar_is_never_listed_for_removal(self):
        # ASSA-B.ST really traded on Memorial Day. Deleting it would lose a real bar.
        self.assertNotIn(("ASSA-B.ST", HOLIDAY), self.keys)

    def test_rows_that_are_not_cross_writes_stay_out(self):
        # A zero-volume holiday placeholder, and a session newer than Yahoo's reply.
        self.assertNotIn(("5E2.SI", "2026-05-27"), self.keys)
        self.assertNotIn(("2208.HK", "2026-11-06"), self.keys)

    def test_every_row_falls_in_the_incident_window(self):
        # The incidents fetched 90 days back from 2026-06-30 and ran until 07-02.
        self.assertGreaterEqual(min(r["date"] for r in self.rows), "2026-04-01")
        self.assertLessEqual(max(r["date"] for r in self.rows), "2026-07-02")


class TestClassify(unittest.TestCase):
    EXPECTED = {"close": 341.1, "volume": 1064447}

    def test_no_row_is_gone(self):
        self.assertEqual(repair.classify(self.EXPECTED, None), repair.GONE)

    def test_float4_noise_still_matches(self):
        # REAL stores 341.1 as 341.100006...; that is the same row.
        stored = {"close": 341.100006, "volume": 1064447.0}
        self.assertEqual(repair.classify(self.EXPECTED, stored), repair.MATCH)

    def test_the_manifests_rounding_still_matches_at_a_low_price(self):
        # The manifest came through the API's 4-decimal rounding. Seen on prod:
        # 19.943926 stored, 19.9439 listed, which a relative tolerance alone rejects.
        expected = {"close": 19.9439, "volume": 4329200}
        stored = {"close": 19.943926, "volume": 4329200.0}
        self.assertEqual(repair.classify(expected, stored), repair.MATCH)
        stored = {"close": 19.95, "volume": 4329200.0}
        self.assertEqual(repair.classify(expected, stored), repair.CHANGED)

    def test_different_values_are_changed(self):
        self.assertEqual(
            repair.classify(self.EXPECTED, {"close": 137.59, "volume": 1064447}), repair.CHANGED
        )
        self.assertEqual(
            repair.classify(self.EXPECTED, {"close": 341.1, "volume": 2000000}), repair.CHANGED
        )


class TestRepairAgainstDatabase(unittest.TestCase):
    def setUp(self):
        _purge()
        self.addCleanup(_purge)
        store_bars(VICTIM, "1d", _frame("2026-05-22", "2026-05-26"))
        store_bars(VICTIM, "1d", _frame(HOLIDAY, close=341.1, volume=1064447))
        self.manifest = [
            {"symbol": VICTIM, "date": HOLIDAY, "close": 341.1, "volume": 1064447, "source": SOURCE}
        ]

    def test_a_refetch_cannot_remove_a_cross_written_bar(self):
        # The reason this repair exists: the real symbol has no bar that day, so
        # an upsert of its true history never touches the row.
        store_bars(VICTIM, "1d", _frame("2026-05-22", "2026-05-26", close=101.0))
        self.assertIn(HOLIDAY, _dates(VICTIM))

    def test_apply_removes_only_the_listed_row(self):
        with closing(get_connection()) as conn:
            plan = repair.read_rows(conn, self.manifest)
            self.assertEqual(plan[0][2], repair.MATCH)
            self.assertEqual(repair.apply_plan(conn, plan), 1)
        self.assertEqual(_dates(VICTIM), ["2026-05-22", "2026-05-26"])

    def test_second_run_is_a_no_op(self):
        for expected in (1, 0):
            with closing(get_connection()) as conn:
                plan = repair.read_rows(conn, self.manifest)
                self.assertEqual(repair.apply_plan(conn, plan), expected)

    def test_a_changed_row_stops_the_whole_repair(self):
        store_bars(VICTIM, "1d", _frame(HOLIDAY, close=140.0))
        store_bars(SOURCE, "1d", _frame(HOLIDAY, close=50.0))
        manifest = self.manifest + [
            {"symbol": SOURCE, "date": HOLIDAY, "close": 50.0, "volume": 1000, "source": VICTIM}
        ]
        with closing(get_connection()) as conn:
            plan = repair.read_rows(conn, manifest)
            with self.assertRaises(RuntimeError):
                repair.apply_plan(conn, plan)
            conn.rollback()
        self.assertIn(HOLIDAY, _dates(VICTIM))
        self.assertIn(HOLIDAY, _dates(SOURCE))

    def test_a_refresh_after_the_read_rolls_the_repair_back(self):
        with closing(get_connection()) as conn:
            plan = repair.read_rows(conn, self.manifest)
            with closing(get_connection()) as other:
                other.execute(
                    "UPDATE ohlcv SET ingested_at = %s WHERE symbol = %s",
                    (int(time.time()) + 100, VICTIM),
                )
                other.commit()
            with self.assertRaises(RuntimeError):
                repair.apply_plan(conn, plan)
        self.assertIn(HOLIDAY, _dates(VICTIM))

    def test_duplicates_outside_the_manifest_are_reported(self):
        store_bars(SOURCE, "1d", _frame("2026-05-26"))
        with closing(get_connection()) as conn:
            groups = repair.identical_bars_outside(conn, self.manifest)
        self.assertTrue([g for g in groups if set(g["symbols"]) == {VICTIM, SOURCE}])

    def test_a_listed_victim_and_its_source_are_not_reported(self):
        store_bars(SOURCE, "1d", _frame(HOLIDAY, close=341.1, volume=1064447))
        with closing(get_connection()) as conn:
            groups = repair.identical_bars_outside(conn, self.manifest)
        self.assertFalse([g for g in groups if set(g["symbols"]) == {VICTIM, SOURCE}])


if __name__ == "__main__":
    unittest.main()
