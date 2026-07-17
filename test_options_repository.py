"""DB round-trip tests for OptionsStore's ATM-snapshot, P/C-history, IV-history
and gamma-wall surfaces (wave 3 coverage — the full-chain paths are pinned in
test_options_contract_tools.py). Runs against the test database only.
"""
import os
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Standard preamble: swap in the test DSN BEFORE quantcore.db is imported.
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.options_repository import OptionsStore  # noqa: E402

TEST_SYMBOL = "ZZOPTREPO"


def iso(days_ago: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def atm_side(iv_pct, strikes=(95.0, 100.0)):
    return {
        "total_open_interest": 1_000,
        "total_volume": 500,
        "avg_iv_pct": iv_pct,
        "atm_contracts": [
            {"strike": s, "last": 2.0, "bid": 1.9, "ask": 2.1, "iv": iv_pct,
             "volume": 10, "open_interest": 100, "in_the_money": s < 100}
            for s in strikes
        ],
    }


def options_payload(pcr=1.2, call_iv=40.0, put_iv=50.0):
    return {
        "expiration": "2026-08-21",
        "put_call_ratio": pcr,
        "calls": atm_side(call_iv),
        "puts": atm_side(put_iv),
    }


class OptionsRepositoryTest(unittest.TestCase):
    def setUp(self):
        self._purge()
        self.addCleanup(self._purge)
        self.store = OptionsStore()

    def _purge(self):
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM options_snapshots WHERE symbol = %s",
                         (TEST_SYMBOL,))
            conn.execute("DELETE FROM gamma_wall_history WHERE symbol = %s",
                         (TEST_SYMBOL,))
            conn.commit()

    def seed(self, days_ago=0.0, price=100.0, pcr=1.2, **payload_kw):
        return self.store.save_snapshot(
            symbol=TEST_SYMBOL,
            price=price,
            bollinger_bands={"upper": 110.0, "middle": 100.0, "lower": 90.0,
                             "period": 20},
            options=options_payload(pcr=pcr, **payload_kw),
            captured_at=iso(days_ago),
        )

    # -- ATM snapshot round trips ----------------------------------------

    def test_snapshot_roundtrip_and_duplicate_rejection(self):
        ts = iso(0.0)
        first = self.store.save_snapshot(
            symbol=TEST_SYMBOL, price=101.5,
            bollinger_bands={"upper": 110, "middle": 100, "lower": 90},
            options=options_payload(), captured_at=ts,
        )
        self.assertIsNotNone(first)
        # Same symbol+timestamp is a duplicate — must return None, not raise.
        self.assertIsNone(self.store.save_snapshot(
            symbol=TEST_SYMBOL, price=999.0,
            bollinger_bands=None, options=options_payload(), captured_at=ts,
        ))
        snap = self.store.get_latest_snapshot(TEST_SYMBOL)
        self.assertEqual(float(snap["price"]), 101.5)

    def test_snapshot_without_options_still_persists(self):
        sid = self.store.save_snapshot(
            symbol=TEST_SYMBOL, price=55.0, bollinger_bands=None,
            options=None, captured_at=iso(0.0),
        )
        self.assertIsNotNone(sid)
        self.assertEqual(self.store.snapshot_count(TEST_SYMBOL), 1)

    def test_symbols_dates_and_counts(self):
        self.seed(days_ago=2.0)
        self.seed(days_ago=1.0)
        self.assertIn(TEST_SYMBOL, self.store.get_symbols())
        self.assertEqual(self.store.snapshot_count(TEST_SYMBOL), 2)
        self.assertEqual(len(self.store.get_snapshot_dates(TEST_SYMBOL)), 2)

    # -- History surfaces ---------------------------------------------------

    def test_pc_history_window_and_values(self):
        self.seed(days_ago=2.0, price=98.0, pcr=1.5)
        self.seed(days_ago=1.0, price=99.0, pcr=1.0)
        self.seed(days_ago=45.0, price=90.0, pcr=3.0)   # outside 30d window
        rows = self.store.get_pc_history(TEST_SYMBOL, days=30)
        self.assertEqual(len(rows), 2)
        pcrs = {round(float(r["put_call_ratio"]), 2) for r in rows}
        self.assertEqual(pcrs, {1.5, 1.0})
        for r in rows:
            self.assertIn("captured_at", r)
            self.assertIn("price", r)

    def test_iv_history_composites_both_sides(self):
        self.seed(days_ago=1.0, call_iv=40.0, put_iv=50.0)
        rows = self.store.get_iv_history(TEST_SYMBOL, days=365)
        self.assertEqual(len(rows), 1)
        composite = rows[0]["composite_iv"]
        self.assertIsNotNone(composite)
        self.assertGreaterEqual(float(composite), 40.0)
        self.assertLessEqual(float(composite), 50.0)

    # -- Gamma wall history --------------------------------------------------

    def daoi_result(self, price=100.0, wall=105.0):
        return {
            "price": price,
            "gamma_wall_strike": wall,
            "gamma_wall_method": "bs_gamma_oi",
            "delta_flip_strike": 100.0,
            "dist_to_flip_pct": 0.0,
            "net_daoi_shares": -12_000.0,
            "call_daoi_shares": 3_000.0,
            "put_daoi_shares": -15_000.0,
            "mm_hedge_bias": "buy_on_rally",
            "signal": "strong",
            "expirations_scanned": ["2026-08-21"],
        }

    def test_gamma_wall_last_write_of_day_wins(self):
        self.store.save_gamma_wall(TEST_SYMBOL, self.daoi_result(price=100.0))
        self.store.save_gamma_wall(TEST_SYMBOL, self.daoi_result(price=104.0,
                                                                 wall=110.0))
        rows = self.store.get_gamma_wall_history(TEST_SYMBOL, since_days=7)
        self.assertEqual(len(rows), 1)                  # one row per calendar day
        self.assertEqual(float(rows[0]["price"]), 104.0)
        self.assertEqual(float(rows[0]["gamma_wall_strike"]), 110.0)

    def test_gamma_wall_history_empty_for_unknown_symbol(self):
        self.assertEqual(self.store.get_gamma_wall_history("ZZNOWALL"), [])


if __name__ == "__main__":
    unittest.main()
