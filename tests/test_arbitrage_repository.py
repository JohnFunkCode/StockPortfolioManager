"""ArbitrageRepository tests — YAML normalisation and NAV snapshot round trips.

The YAML half runs against temp files; the SQL half runs against the test
database only (the ``tests`` package initializer swaps the DSN in before
``quantcore.db`` is imported).
"""
import tempfile
import time
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection, init_schema  # noqa: E402
from quantcore.repositories.arbitrage_repository import (  # noqa: E402
    ArbitrageRepository,
)

SECURITY = "ZZ_ARB_TEST"


def write_yaml(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    handle.write(text)
    handle.close()
    return Path(handle.name)


class LoadUniverseTest(unittest.TestCase):
    def repo_for(self, text: str) -> ArbitrageRepository:
        path = write_yaml(text)
        self.addCleanup(path.unlink, missing_ok=True)
        return ArbitrageRepository(universe_path=path)

    def test_normalises_a_full_entry(self):
        repo = self.repo_for("""
- security: mstr
  name: Strategy
  kind: nav_vehicle
  underlying: BTC-USD
  hedge_instrument: ibit
  convergence_mechanism: BUYBACK
  holdings_units: 843775
  holdings_as_of: 2026-07-14
  senior_claims_usd: 16_500_000_000
  diluted_shares: 350_000_000
  notes: "  spaced  "
""")
        entry = repo.load_universe()[0]
        self.assertEqual(entry["security"], "MSTR")
        self.assertEqual(entry["hedge_instrument"], "IBIT")
        self.assertEqual(entry["convergence_mechanism"], "buyback")
        # PyYAML parses an unquoted date into datetime.date; the repository
        # must hand services an ISO string either way.
        self.assertEqual(entry["holdings_as_of"], "2026-07-14")
        self.assertEqual(entry["senior_claims_usd"], 16_500_000_000.0)
        self.assertEqual(entry["notes"], "spaced")

    def test_defaults_are_applied_for_optional_fields(self):
        repo = self.repo_for("""
- security: GDX
  kind: producer
  underlying: GC=F
""")
        entry = repo.load_universe()[0]
        self.assertIsNone(entry["hedge_instrument"])
        self.assertEqual(entry["convergence_mechanism"], "none")
        self.assertEqual(entry["senior_claims_usd"], 0.0)
        self.assertIsNone(entry["holdings_units"])
        self.assertEqual(entry["name"], "GDX")

    def test_unknown_mechanism_falls_back_to_none(self):
        repo = self.repo_for("""
- security: AAA
  kind: producer
  underlying: GC=F
  convergence_mechanism: wishful_thinking
""")
        self.assertEqual(repo.load_universe()[0]["convergence_mechanism"], "none")

    def test_invalid_entries_are_dropped(self):
        repo = self.repo_for("""
- security: GOOD
  kind: producer
  underlying: GC=F
- security: NOKIND
  underlying: GC=F
- kind: producer
  underlying: GC=F
- security: NOUNDERLYING
  kind: producer
- security: BADKIND
  kind: not_a_kind
  underlying: GC=F
- just-a-string
""")
        self.assertEqual([e["security"] for e in repo.load_universe()], ["GOOD"])

    def test_missing_file_returns_empty_list(self):
        repo = ArbitrageRepository(universe_path=Path("/nonexistent/arb.yaml"))
        self.assertEqual(repo.load_universe(), [])

    def test_malformed_yaml_returns_empty_list(self):
        self.assertEqual(self.repo_for("- [unclosed").load_universe(), [])

    def test_non_list_document_returns_empty_list(self):
        self.assertEqual(self.repo_for("security: MSTR").load_universe(), [])

    def test_empty_file_returns_empty_list(self):
        self.assertEqual(self.repo_for("").load_universe(), [])

    def test_non_numeric_values_degrade_to_defaults(self):
        repo = self.repo_for("""
- security: AAA
  kind: producer
  underlying: GC=F
  holdings_units: "not a number"
  senior_claims_usd: "nope"
""")
        entry = repo.load_universe()[0]
        self.assertIsNone(entry["holdings_units"])
        self.assertEqual(entry["senior_claims_usd"], 0.0)


class GetEntryTest(unittest.TestCase):
    def setUp(self):
        path = write_yaml("""
- security: MSTR
  kind: nav_vehicle
  underlying: BTC-USD
- security: GDX
  kind: producer
  underlying: GC=F
""")
        self.addCleanup(path.unlink, missing_ok=True)
        self.repo = ArbitrageRepository(universe_path=path)

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(self.repo.get_entry("mstr")["security"], "MSTR")
        self.assertEqual(self.repo.get_entry("  GdX  ")["security"], "GDX")

    def test_unknown_security_returns_none(self):
        self.assertIsNone(self.repo.get_entry("NOPE"))
        self.assertIsNone(self.repo.get_entry(""))


class NavSnapshotDbTest(unittest.TestCase):
    """Test database only — round trips through arb_nav_snapshots."""

    @classmethod
    def setUpClass(cls):
        init_schema()

    def setUp(self):
        self.repo = ArbitrageRepository()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM arb_nav_snapshots WHERE security = ?",
                         (SECURITY,))
            conn.commit()

    def test_record_and_read_back_latest(self):
        self.repo.record_nav_snapshot(
            security=SECURITY.lower(), as_of="2026-07-14", underlying="BTC-USD",
            units=843_775, senior_claims=16_500_000_000,
            annual_senior_cost=854_000_000, diluted_shares=350_000_000,
            source="unit-test",
        )
        row = self.repo.latest_nav_snapshot(SECURITY)
        self.assertEqual(row["security"], SECURITY)
        self.assertEqual(row["units"], 843_775.0)
        # Whole-balance-sheet figures must survive the round trip exactly;
        # float4 would have rounded this by hundreds of dollars.
        self.assertEqual(row["senior_claims"], 16_500_000_000.0)
        self.assertEqual(row["source"], "unit-test")

    def test_latest_wins_over_older_snapshots(self):
        for as_of, units in (("2026-07-01", 800_000), ("2026-07-20", 900_000),
                             ("2026-07-10", 850_000)):
            self.repo.record_nav_snapshot(SECURITY, as_of, "BTC-USD", units)
        self.assertEqual(self.repo.latest_nav_snapshot(SECURITY)["units"],
                         900_000.0)

    def test_same_day_rerecord_upserts(self):
        self.repo.record_nav_snapshot(SECURITY, "2026-07-14", "BTC-USD", 1000,
                                      source="first")
        self.repo.record_nav_snapshot(SECURITY, "2026-07-14", "BTC-USD", 2000,
                                      source="second")
        history = self.repo.nav_snapshot_history(SECURITY)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["units"], 2000.0)
        self.assertEqual(history[0]["source"], "second")

    def test_history_is_newest_first_and_respects_limit(self):
        for day in range(1, 6):
            self.repo.record_nav_snapshot(SECURITY, f"2026-07-0{day}",
                                          "BTC-USD", day * 100)
        history = self.repo.nav_snapshot_history(SECURITY, limit=3)
        self.assertEqual([h["as_of"] for h in history],
                         ["2026-07-05", "2026-07-04", "2026-07-03"])

    def test_date_objects_are_accepted_for_as_of(self):
        self.repo.record_nav_snapshot(SECURITY, date(2026, 7, 14), "BTC-USD", 500)
        self.assertEqual(self.repo.latest_nav_snapshot(SECURITY)["as_of"],
                         "2026-07-14")

    def test_defaults_applied_for_optional_columns(self):
        self.repo.record_nav_snapshot(SECURITY, "2026-07-14", "BTC-USD", 500)
        row = self.repo.latest_nav_snapshot(SECURITY)
        self.assertEqual(row["senior_claims"], 0.0)
        self.assertEqual(row["annual_senior_cost"], 0.0)
        self.assertIsNone(row["diluted_shares"])

    def test_unknown_security_has_no_snapshot(self):
        self.assertIsNone(self.repo.latest_nav_snapshot("ZZ_NEVER_RECORDED"))
        self.assertEqual(self.repo.nav_snapshot_history("ZZ_NEVER_RECORDED"), [])

    def test_ingested_at_is_stamped(self):
        before = int(time.time())
        self.repo.record_nav_snapshot(SECURITY, "2026-07-14", "BTC-USD", 500)
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT ingested_at FROM arb_nav_snapshots WHERE security = ?",
                (SECURITY,),
            ).fetchone()
        self.assertGreaterEqual(row["ingested_at"], before)


if __name__ == "__main__":
    unittest.main()
