"""OptionsPositionStore without a database: the alert evaluator's branches and
the owner predicate on every statement (#460).

The real-DB lifecycle and wrong-owner tests live in tests/test_repositories_db.py.
These cover what those can't cheaply reach: every expiry-day boundary, puts,
at-the-money, malformed dates, zero cost -- and the SQL shape of each
statement, which the behavioural tests can't see when an earlier statement
already narrowed the rows (the bulk UPDATE in auto_expire_past_positions).
"""

import re
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from quantcore.repositories import options_position_repository as repo
from quantcore.repositories.options_position_repository import (
    ALERT_EXPIRATION_1D,
    ALERT_EXPIRATION_7D,
    ALERT_ITM,
    ALERT_PROFIT_TARGET,
    OptionsPositionStore,
    alerts_for_position,
)

TODAY = date(2026, 8, 14)


def _pos(kind="call", strike=100.0, days=30, cost=1.0, expiration=None):
    return {
        "position_id": 1, "symbol": "ZZALERT", "kind": kind, "strike": strike,
        "expiration": expiration or (TODAY + timedelta(days=days)).isoformat(),
        "contracts": 2, "purchase_price": cost, "target_price": None,
    }


def _types(pos, price):
    return [a["alert_type"] for a in alerts_for_position(pos, price, TODAY)]


class AlertEvaluationTests(unittest.TestCase):
    def test_call_itm_and_otm(self):
        self.assertIn(ALERT_ITM, _types(_pos("call", 100.0), 105.0))
        self.assertNotIn(ALERT_ITM, _types(_pos("call", 100.0), 95.0))

    def test_put_itm_and_otm(self):
        self.assertIn(ALERT_ITM, _types(_pos("put", 100.0), 95.0))
        self.assertNotIn(ALERT_ITM, _types(_pos("put", 100.0), 105.0))
        alert = alerts_for_position(_pos("put", 100.0, cost=1.0), 95.0, TODAY)[0]
        self.assertEqual(alert["intrinsic_value"], 5.0)
        self.assertIn("is below strike", alert["message"])

    def test_at_the_money_counts_as_itm_with_zero_intrinsic(self):
        for kind in ("call", "put"):
            alerts = alerts_for_position(_pos(kind, 100.0), 100.0, TODAY)
            self.assertEqual([a["alert_type"] for a in alerts], [ALERT_ITM], kind)
            self.assertEqual(alerts[0]["intrinsic_value"], 0.0)

    def test_expiration_boundaries(self):
        expected = {
            -1: [], 0: [ALERT_EXPIRATION_1D], 1: [ALERT_EXPIRATION_1D],
            2: [ALERT_EXPIRATION_7D], 7: [ALERT_EXPIRATION_7D], 8: [], 30: [],
        }
        for days, types in expected.items():
            # OTM call so only the expiration alert can fire.
            got = [t for t in _types(_pos("call", 100.0, days=days), 90.0)]
            self.assertEqual(got, types, f"days={days}")

    def test_expiration_labels(self):
        today = alerts_for_position(_pos(days=0), 90.0, TODAY)[0]["message"]
        tomorrow = alerts_for_position(_pos(days=1), 90.0, TODAY)[0]["message"]
        self.assertIn("EXPIRES TODAY", today)
        self.assertIn("OTM — will expire worthless", today)
        self.assertIn("EXPIRES TOMORROW", tomorrow)
        itm = alerts_for_position(_pos(days=1), 110.0, TODAY)
        self.assertIn("ITM — consider closing", itm[1]["message"])

    def test_malformed_expiration_raises_nothing(self):
        self.assertEqual(
            alerts_for_position(_pos(expiration="not-a-date"), 110.0, TODAY), []
        )

    def test_profit_target_threshold(self):
        # cost 1.0: intrinsic 2.0 is exactly 2x -> fires; 1.99 doesn't.
        self.assertIn(ALERT_PROFIT_TARGET, _types(_pos("call", 100.0, cost=1.0), 102.0))
        self.assertNotIn(ALERT_PROFIT_TARGET, _types(_pos("call", 100.0, cost=1.0), 101.99))

    def test_zero_cost_never_hits_profit_target_and_reports_zero_roi(self):
        alerts = alerts_for_position(_pos("call", 100.0, cost=0.0), 150.0, TODAY)
        self.assertEqual([a["alert_type"] for a in alerts], [ALERT_ITM])
        self.assertEqual(alerts[0]["roi_pct"], 0.0)

    def test_several_alerts_fire_together_in_a_fixed_order(self):
        self.assertEqual(
            _types(_pos("call", 100.0, days=1, cost=1.0), 110.0),
            [ALERT_ITM, ALERT_EXPIRATION_1D, ALERT_PROFIT_TARGET],
        )

    def test_payload_values(self):
        alert = alerts_for_position(_pos("call", 100.0, days=5, cost=3.0), 110.0, TODAY)[0]
        self.assertEqual(alert["days_to_expiry"], 5)
        self.assertEqual(alert["total_cost"], 600.0)
        self.assertEqual(alert["total_intrinsic"], 2000.0)
        self.assertEqual(alert["roi_pct"], 233.3)

    def test_store_skips_positions_without_a_price(self):
        store = OptionsPositionStore()
        positions = [_pos(), {**_pos(), "position_id": 2, "symbol": "ZZNOPRICE"}]
        with patch.object(store, "get_active_positions", return_value=positions) as active:
            alerts = store.get_pending_alerts({"ZZALERT": 110.0}, owner="ownera")
        active.assert_called_once_with(owner="ownera")
        self.assertEqual({a["position_id"] for a in alerts}, {1})


class _RecordingConnection:
    """Stands in for get_connection(); records each (sql, params)."""

    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=()):
        self.log.append((" ".join(sql.split()), list(params)))
        return self

    def fetchall(self):
        return [{"position_id": 7}]

    def fetchone(self):
        # Serves fetchone()[0] (INSERT … RETURNING, COUNT) and dict(row).
        return {0: 7, "position_id": 7}

    def commit(self):
        pass

    def close(self):
        pass


class OwnerPredicateShapeTests(unittest.TestCase):
    """Every statement restricts (or, for INSERT, binds) the owner. This is the
    guard for the bulk UPDATE in auto_expire_past_positions, whose missing
    predicate no behavioural test notices: the SELECT before it already
    narrowed the ids to the owner's."""

    OWNER = "zz-shape-owner"
    RESTRICTS = re.compile(r"\bWHERE\b.*\bowner = %s", re.IGNORECASE)

    def _run(self, call):
        log = []
        with patch.object(repo, "get_connection", lambda: _RecordingConnection(log)):
            call(OptionsPositionStore())
        return log

    def test_every_read_and_mutation_restricts_to_the_owner(self):
        calls = {
            "close_position": lambda s: s.close_position(7, owner=self.OWNER),
            "expire_position": lambda s: s.expire_position(7, owner=self.OWNER),
            "auto_expire_past_positions": lambda s: s.auto_expire_past_positions(owner=self.OWNER),
            "get_active_positions": lambda s: s.get_active_positions(owner=self.OWNER),
            "get_position": lambda s: s.get_position(7, owner=self.OWNER),
            "position_count": lambda s: s.position_count(owner=self.OWNER),
        }
        seen = 0
        for name, call in calls.items():
            for sql, params in self._run(call):
                seen += 1
                with self.subTest(method=name, sql=sql):
                    self.assertRegex(sql, self.RESTRICTS)
                    self.assertIn(self.OWNER, params)
        # auto-expire's SELECT *and* UPDATE, plus one each for the other five.
        self.assertEqual(seen, 7)

    def test_bulk_expiry_update_restricts_to_the_owner(self):
        log = self._run(lambda s: s.auto_expire_past_positions(owner=self.OWNER))
        updates = [(sql, p) for sql, p in log if sql.startswith("UPDATE")]
        self.assertEqual(len(updates), 1)
        sql, params = updates[0]
        self.assertRegex(sql, r"WHERE position_id = ANY\(%s\) AND owner = %s")
        self.assertEqual(params[-1], self.OWNER)

    def test_insert_binds_the_owner_rather_than_using_the_default(self):
        log = self._run(lambda s: s.add_position(
            symbol="zz", kind="call", strike=1.0, expiration="2026-08-20",
            contracts=1, purchase_price=1.0, purchase_date="2026-08-14",
            owner=self.OWNER,
        ))
        (sql, params), = log
        self.assertRegex(sql, r"INSERT INTO options_positions \( ?owner,")
        self.assertEqual(params[0], self.OWNER)


if __name__ == "__main__":
    unittest.main()
