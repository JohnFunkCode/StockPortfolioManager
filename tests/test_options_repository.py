"""DB round-trip tests for OptionsStore's ATM-snapshot, P/C-history, IV-history
and gamma-wall surfaces (wave 3 coverage — the full-chain paths are pinned in
test_options_contract_tools.py). Runs against the test database only.
"""
import os
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.analytics.market_time import market_date  # noqa: E402
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
            conn.execute("DELETE FROM options_capture_claims WHERE symbol = %s",
                         (TEST_SYMBOL,))
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

    def test_full_chain_is_idempotent_for_a_market_day(self):
        first = self.store.save_full_chain(
            symbol=TEST_SYMBOL, price=101.0, bollinger_bands=None,
            expirations_data=[], captured_at="2026-08-10T13:00:00Z",
        )
        second = self.store.save_full_chain(
            symbol=TEST_SYMBOL, price=999.0, bollinger_bands=None,
            expirations_data=[], captured_at="2026-08-10T20:00:00Z",
        )
        next_day = self.store.save_full_chain(
            symbol=TEST_SYMBOL, price=102.0, bollinger_bands=None,
            expirations_data=[], captured_at="2026-08-11T13:00:00Z",
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(next_day)
        self.assertEqual(self.store.snapshot_count(TEST_SYMBOL), 2)

    def test_symbols_dates_and_counts(self):
        self.seed(days_ago=2.0)
        self.seed(days_ago=1.0)
        self.assertIn(TEST_SYMBOL, self.store.get_symbols())
        self.assertEqual(self.store.snapshot_count(TEST_SYMBOL), 2)
        self.assertEqual(len(self.store.get_snapshot_dates(TEST_SYMBOL)), 2)

    # -- Bulk latest full chains (the watchlist screen's read) -------------

    def _full_side(self, strikes):
        return {
            "total_open_interest": 1_000, "total_volume": 400, "avg_iv_pct": 45.0,
            "contracts": [
                {"strike": s, "last": 2.0, "bid": 1.9, "ask": 2.1, "iv": 45.0,
                 "volume": 10, "open_interest": 100, "in_the_money": False}
                for s in strikes
            ],
        }

    def _save_full(self, captured_at, price=100.0):
        strikes = (50.0, 80.0, 100.0, 120.0, 150.0)
        return self.store.save_full_chain(
            symbol=TEST_SYMBOL, price=price, bollinger_bands=None,
            captured_at=captured_at,
            expirations_data=[
                {"expiration": exp, "put_call_ratio": 1.1,
                 "calls": self._full_side(strikes), "puts": self._full_side(strikes)}
                for exp in ("2026-08-07", "2026-08-14", "2026-08-21")
            ],
        )

    def test_latest_full_chains_in_three_queries(self):
        self._save_full("2026-08-06T21:00:00Z", price=90.0)
        self._save_full("2026-08-07T21:00:00Z", price=100.0)
        queries = []
        real = self.store._get_connection

        def counting():
            conn = real()
            execute = conn.execute

            def counted(*a, **kw):
                queries.append(a[0])
                return execute(*a, **kw)
            conn.execute = counted
            return conn
        self.store._get_connection = counting

        chains = self.store.get_latest_full_chains(
            [TEST_SYMBOL.lower(), "ZZNOCHAIN"], "2026-08-10")

        self.assertEqual(len(queries), 3)
        self.assertEqual(set(chains), {TEST_SYMBOL})
        snap = chains[TEST_SYMBOL]
        self.assertEqual(float(snap["price"]), 100.0)          # the latest capture
        # The 08-07 expiry has passed by 08-10; the rest come oldest first.
        self.assertEqual([str(e["expiration"])[:10] for e in snap["expirations"]],
                         ["2026-08-14", "2026-08-21"])
        near, mid = snap["expirations"]
        self.assertNotIn("contracts", mid)
        # Only strikes within ±30% of the snapshot price (100) are loaded.
        self.assertEqual(sorted({float(c["strike"]) for c in near["contracts"]}),
                         [80.0, 100.0, 120.0])
        self.assertEqual({c["kind"] for c in near["contracts"]}, {"call", "put"})

    def test_latest_full_chains_keeps_a_symbol_whose_expirations_passed(self):
        self._save_full("2026-08-07T21:00:00Z")
        chains = self.store.get_latest_full_chains([TEST_SYMBOL], "2026-09-01")
        self.assertEqual(chains[TEST_SYMBOL]["expirations"], [])
        self.assertEqual(self.store.get_latest_full_chains([], "2026-09-01"), {})

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

    def test_capture_counts_sees_chain_and_gamma_rows_for_the_day(self):
        # The test DB may hold other symbols' rows, so assert on the delta.
        # Chains are keyed on the Eastern market day, gamma on the UTC date; the
        # two differ late in the evening, so ask each question with its own key.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        et_day = market_date().isoformat()
        before = self.store.capture_counts(today)
        before_et = self.store.capture_counts(et_day)
        self.store.save_full_chain(
            symbol=TEST_SYMBOL, price=101.0, bollinger_bands=None,
            expirations_data=[], captured_at=iso(0),
        )
        self.store.save_gamma_wall(TEST_SYMBOL, self.daoi_result(price=100.0))
        after = self.store.capture_counts(today)
        after_et = self.store.capture_counts(et_day)
        self.assertEqual(after_et["chains"], before_et["chains"] + 1)
        self.assertEqual(after["gamma_wall"], before["gamma_wall"] + 1)
        self.assertEqual(after["gex"], before["gex"])

    def test_gamma_wall_history_empty_for_unknown_symbol(self):
        self.assertEqual(self.store.get_gamma_wall_history("ZZNOWALL"), [])


if __name__ == "__main__":
    unittest.main()
