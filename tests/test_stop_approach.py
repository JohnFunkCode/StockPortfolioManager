"""The approach alert: a holding that closed near its stop (#221; plan step 4.4).

Bottom up: the rule in `analytics.stored_stops` (`approach`, `atr_distance`);
the approach columns `StopsRepository.record_check` writes and the rule V25
puts on them; `mark_approach_alerted` with its wrong-owner and wrong-book
cases; `StopsService.check_stop` recording an approach and clearing it,
and `unalerted_approaches`; the step `report_approaching_stops` and the
setting in `main.py`; and the Discord message.
"""

import datetime
import os
import unittest
from contextlib import closing
from decimal import Decimal
from unittest import mock

import psycopg2

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

import main  # noqa: E402
from notifier import Notifier, _stop_approach_line  # noqa: E402
from quantcore.analytics import stored_stops as sm  # noqa: E402
from quantcore.db import get_connection  # noqa: E402
from tests.test_stop_checks import (  # noqa: E402
    JOB_NOW,
    MONDAY,
    OTHER,
    OWNER,
    SYMBOL,
    TUESDAY,
    WEDNESDAY,
    FakeNotifier,
    _Base,
    bar,
)

D = Decimal


def calm(until, n=20):
    """`n` closed bars on the days before `until`, each with a range of 2
    around a close of 100, so a 14-session ATR is exactly 2 going in."""
    return [bar(until - datetime.timedelta(days=k), 100, 101, 99, close=100)
            for k in range(n, 0, -1)]


# A bar that closes at 91.50, 1.50 above a stop at 90. Its true range is 9
# (100 down to 91), so the ATR becomes 2 + (9 - 2) / 14 = 2.50, and the close
# is 0.6 ATR above the stop.
NEAR = (92, 93, 91, 91.5)
# A bar that closes at 99, 4.5 ATR above a stop at 90.
FAR = (99, 100, 98.5, 99)


def on(session, prices):
    open_, high, low, close = prices
    return bar(session, open_, high, low, close=close)


# --------------------------------------------------------------------------
# 1. The rule
# --------------------------------------------------------------------------

class ApproachRuleTest(unittest.TestCase):
    def judge(self, bars, last_bar=TUESDAY, level=D("90"), multiple=D("1")):
        return sm.approach(bars=bars, last_bar=last_bar, stop_level=level, multiple=multiple)

    def test_a_close_within_the_distance_is_near_and_carries_its_atr(self):
        found = self.judge(calm(TUESDAY) + [on(TUESDAY, NEAR)])
        self.assertEqual(found, sm.Approach(
            session=TUESDAY, close=D("91.50"), atr=D("2.500000"), near=True))
        self.assertEqual(sm.atr_distance(found.close, D("90"), found.atr), D("0.6"))

    def test_a_close_further_away_is_not_near(self):
        found = self.judge(calm(TUESDAY) + [on(TUESDAY, FAR)])
        self.assertEqual((found.session, found.close, found.near), (TUESDAY, D("99.00"), False))

    def test_the_distance_is_the_multiple_and_its_edge_counts_as_near(self):
        bars = calm(TUESDAY) + [on(TUESDAY, NEAR)]
        self.assertFalse(self.judge(bars, multiple=D("0.5")).near)
        self.assertTrue(self.judge(bars, multiple=D("0.6")).near)
        self.assertTrue(self.judge(bars, level=D("89"), multiple=D("1")).near)
        self.assertFalse(self.judge(bars, level=D("88.99"), multiple=D("1")).near)

    def test_too_little_history_is_no_answer_not_far(self):
        self.assertIsNone(self.judge(calm(TUESDAY, n=sm.APPROACH_MIN_BARS - 2)
                                     + [on(TUESDAY, NEAR)]))
        self.assertIsNotNone(self.judge(calm(TUESDAY, n=sm.APPROACH_MIN_BARS - 1)
                                        + [on(TUESDAY, NEAR)]))

    def test_it_is_about_the_bar_just_tested_and_nothing_after_it(self):
        bars = calm(TUESDAY) + [on(TUESDAY, NEAR), on(WEDNESDAY, FAR)]
        self.assertTrue(self.judge(bars, last_bar=TUESDAY).near)
        self.assertIsNone(self.judge(calm(TUESDAY), last_bar=TUESDAY))
        self.assertIsNone(self.judge(bars, last_bar=None))

    def test_the_atr_is_wilders_and_a_symbol_that_never_moved_has_none(self):
        self.assertEqual(sm._atr(calm(TUESDAY) + [on(TUESDAY, NEAR)]), D("2.500000"))
        flat = [bar(TUESDAY - datetime.timedelta(days=k), 100, 100, 100, close=100)
                for k in range(20, -1, -1)]
        self.assertIsNone(sm._atr(flat))
        self.assertIsNone(self.judge(flat))

    def test_a_bar_with_a_bad_price_is_left_out(self):
        bars = calm(TUESDAY) + [on(TUESDAY, NEAR)]
        bars[3] = bar(bars[3]["session"], None, 101, 99, close=100)
        self.assertTrue(self.judge(bars).near)
        bars[-1] = bar(TUESDAY, 92, 93, 0, close=91.5)
        self.assertIsNone(self.judge(bars))


# --------------------------------------------------------------------------
# 2. The rows
# --------------------------------------------------------------------------

class ApproachRowsTest(_Base):
    def record(self, stop, approach=None, **overrides):
        args = {"owner": OWNER, "stop_id": stop["stop_id"], "last_bar_checked": TUESDAY,
                "stop_level": D("90"), "high_water_mark": None, "book_id": stop["book_id"]}
        args.update(approach or {})
        args.update(overrides)
        return self.repo.record_check(**args)

    NEAR_ROW = {"approach_known": True, "approached_session": TUESDAY,
                "approach_close": D("91.5"), "approach_atr": D("2.5")}

    def approach_of(self, stop):
        row = self.stored(stop)
        return (row["approached_session"], row["approach_close"], row["approach_atr"],
                row["approach_alerted_at"])

    def test_a_near_close_is_recorded_and_an_unknown_one_leaves_it(self):
        stop = self.set_stop()
        self.assertTrue(self.record(stop, self.NEAR_ROW))
        self.assertEqual(self.approach_of(stop),
                         (TUESDAY, D("91.500000"), D("2.500000"), None))
        self.assertTrue(self.record(stop, last_bar_checked=WEDNESDAY))
        self.assertEqual(self.approach_of(stop)[0], TUESDAY)

    def test_staying_near_keeps_the_alert_mark_and_moving_away_clears_it(self):
        stop = self.set_stop()
        self.record(stop, self.NEAR_ROW)
        self.assertTrue(self.repo.mark_approach_alerted(
            owner=OWNER, stop_id=stop["stop_id"], alerted_at=JOB_NOW))
        self.record(stop, {**self.NEAR_ROW, "approached_session": WEDNESDAY},
                    last_bar_checked=WEDNESDAY)
        self.assertEqual(self.approach_of(stop)[::3], (WEDNESDAY, JOB_NOW))

        self.record(stop, {"approach_known": True}, last_bar_checked=WEDNESDAY)
        self.assertEqual(self.approach_of(stop), (None, None, None, None))

    def test_the_alert_is_marked_once_and_only_on_an_active_near_stop(self):
        stop = self.set_stop()
        mark = lambda: self.repo.mark_approach_alerted(  # noqa: E731
            owner=OWNER, stop_id=stop["stop_id"], alerted_at=JOB_NOW)
        self.assertFalse(mark())
        self.record(stop, self.NEAR_ROW)
        self.assertTrue(mark())
        self.assertFalse(mark())

    def test_wrong_owner_and_wrong_book_change_nothing(self):
        real = self.set_stop()
        paper = self.set_stop(book_id=self.paper)
        for stop in (real, paper):
            self.record(stop, self.NEAR_ROW)
        self.assertFalse(self.repo.record_check(
            owner=OTHER, stop_id=real["stop_id"], last_bar_checked=WEDNESDAY,
            stop_level=D("1"), high_water_mark=None, approach_known=True))
        self.assertFalse(self.repo.mark_approach_alerted(
            owner=OTHER, stop_id=real["stop_id"], alerted_at=JOB_NOW))
        # The real book's id named with the paper book: no row.
        self.assertFalse(self.repo.mark_approach_alerted(
            owner=OWNER, stop_id=real["stop_id"], alerted_at=JOB_NOW, book_id=self.paper))
        self.assertFalse(self.repo.mark_approach_alerted(
            owner=OWNER, stop_id=paper["stop_id"], alerted_at=JOB_NOW))
        for stop in (real, paper):
            self.assertEqual(self.approach_of(stop),
                             (TUESDAY, D("91.500000"), D("2.500000"), None))

    def test_the_database_refuses_half_an_approach(self):
        stop = self.set_stop()
        for sets in ("approached_session = '2026-06-16'",
                     "approached_session = '2026-06-16', approach_close = 91.5",
                     "approach_alerted_at = now()"):
            with self.subTest(sets=sets), closing(get_connection()) as conn:
                with self.assertRaises(psycopg2.errors.CheckViolation):
                    conn.execute(f"UPDATE stops SET {sets} WHERE stop_id = %s",
                                 (stop["stop_id"],))
                conn.rollback()


# --------------------------------------------------------------------------
# 3. The service
# --------------------------------------------------------------------------

class ApproachServiceTest(_Base):
    def test_a_stop_that_held_and_closed_near_records_the_approach(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = calm(TUESDAY) + [on(TUESDAY, NEAR)]

        result = self.check(stop)

        self.assertEqual(
            (result["outcome"], result["approached_session"], result["approach_close"],
             result["approach_atr"]),
            ("checked", TUESDAY, D("91.500000"), D("2.500000")))

    def test_the_multiple_is_the_callers(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = calm(TUESDAY) + [on(TUESDAY, NEAR)]
        result = self.check(stop, approach_atr_multiple=D("0.5"))
        self.assertIsNone(result["approached_session"])

    def test_a_trailing_stop_is_measured_from_its_raised_level(self):
        # A 10% trail from 100. The bar's high of 110 raises the level to 99,
        # and the close of 101 is then 0.8 ATR above it, not 4.3 above 90.
        stop = self.set_stop(kind="trailing", stop_level=None, trail_pct="10")
        self.prices.bars[SYMBOL] = calm(TUESDAY) + [bar(TUESDAY, 101, 110, 100, close=101)]
        result = self.check(stop)
        self.assertEqual((result["stop_level"], result["approached_session"]),
                         (D("99.000000"), TUESDAY))

    def test_too_little_history_leaves_the_approach_as_it_was(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = calm(TUESDAY) + [on(TUESDAY, NEAR)]
        self.check(stop)
        self.prices.bars[SYMBOL] = [on(WEDNESDAY, FAR)]
        result = self.check(stop, today=WEDNESDAY)
        self.assertEqual((result["last_bar_checked"], result["approached_session"]),
                         (WEDNESDAY, TUESDAY))

    def test_a_triggered_stop_is_not_an_approach(self):
        stop = self.set_stop()
        self.prices.bars[SYMBOL] = calm(TUESDAY) + [on(TUESDAY, (92, 93, 89, 91.5))]
        result = self.check(stop)
        self.assertEqual((result["outcome"], result["approached_session"]), ("triggered", None))
        self.assertEqual(self.service.unalerted_approaches(owner=OWNER), [])

    def test_only_the_real_books_unalerted_approaches_are_owed_nearest_first(self):
        self.lots.add_position(OWNER, {**self.second_lot()})
        real = self.set_stop()
        second = self.set_stop(symbol="ZZSTOPCHK2", stop_level="90.5")
        paper = self.set_stop(book_id=self.paper)
        self.prices.bars[SYMBOL] = calm(TUESDAY) + [on(TUESDAY, NEAR)]
        self.prices.bars["ZZSTOPCHK2"] = calm(TUESDAY) + [on(TUESDAY, NEAR)]
        for stop in (real, second, paper):
            self.check(stop)

        owed = self.service.unalerted_approaches(owner=OWNER)
        self.assertEqual([(s["stop_id"], s["atr_distance"]) for s in owed],
                         [(second["stop_id"], D("0.4")), (real["stop_id"], D("0.6"))])
        self.assertEqual(self.service.unalerted_approaches(owner=OTHER), [])

        self.assertEqual(self.service.mark_approach_alerted(
            owner=OWNER, stop_ids=[real["stop_id"], second["stop_id"], paper["stop_id"]],
            now=JOB_NOW), 2)
        self.assertEqual(self.service.unalerted_approaches(owner=OWNER), [])
        self.assertIsNone(self.stored(paper)["approach_alerted_at"])

    def test_a_holding_that_moves_away_and_comes_back_is_owed_again(self):
        stop = self.set_stop()
        bars = calm(TUESDAY) + [on(TUESDAY, NEAR)]
        self.prices.bars[SYMBOL] = bars
        self.check(stop)
        self.service.mark_approach_alerted(owner=OWNER, stop_ids=[stop["stop_id"]])

        # Still near on Wednesday: nothing owed.
        bars = bars + [on(WEDNESDAY, NEAR)]
        self.prices.bars[SYMBOL] = bars
        self.check(stop, today=WEDNESDAY)
        self.assertEqual(self.service.unalerted_approaches(owner=OWNER), [])

        # Away on Thursday, near again on Friday (the 19th is a holiday, but
        # the rule only looks at the bars it is given).
        thursday, friday = WEDNESDAY + datetime.timedelta(1), WEDNESDAY + datetime.timedelta(2)
        self.prices.bars[SYMBOL] = bars + [on(thursday, (99, 105, 98, 104))]
        self.check(stop, today=thursday)
        self.assertEqual(self.stored(stop)["approached_session"], None)
        self.prices.bars[SYMBOL] = self.prices.bars[SYMBOL] + [on(friday, (92, 93, 91, 91.5))]
        self.check(stop, today=friday)
        self.assertEqual([s["stop_id"] for s in self.service.unalerted_approaches(owner=OWNER)],
                         [stop["stop_id"]])

    def second_lot(self):
        return {"name": "Stop Co 2", "symbol": "ZZSTOPCHK2", "purchase_price": 80.0,
                "quantity": 5, "purchase_date": "2026-01-02", "currency": "USD"}


# --------------------------------------------------------------------------
# 4. The step in main.py
# --------------------------------------------------------------------------

class ApproachNotifier(FakeNotifier):
    def __init__(self, accepted=True):
        super().__init__(accepted)
        self.approaches = []

    def send_stop_approach_alert(self, stops, stop_count, day, multiple):
        self.approaches.append((stops, stop_count, day, multiple))
        return self.accepted


def near(n):
    return [{"stop_id": i + 1, "symbol": f"S{i}"} for i in range(n)]


class ReportApproachingStopsTest(unittest.TestCase):
    def test_nothing_owed_sends_nothing(self):
        stops, n = mock.Mock(), ApproachNotifier()
        stops.unalerted_approaches.return_value = []
        self.assertEqual(main.report_approaching_stops(stops, n, "john", today=TUESDAY), 0)
        self.assertEqual(n.approaches, [])
        stops.mark_approach_alerted.assert_not_called()

    def test_sends_one_message_then_marks_every_stop_it_covers(self):
        stops, n = mock.Mock(), ApproachNotifier()
        many = near(main.STOP_ALERT_MESSAGE_LINES + 2)
        stops.unalerted_approaches.return_value = many
        self.assertEqual(
            main.report_approaching_stops(stops, n, "john", today=TUESDAY), len(many))
        stops.unalerted_approaches.assert_called_once_with(owner="john")
        self.assertEqual(n.approaches, [(many[:main.STOP_ALERT_MESSAGE_LINES], len(many),
                                         "2026-06-16", D("1.0"))])
        stops.mark_approach_alerted.assert_called_once_with(
            owner="john", stop_ids=[s["stop_id"] for s in many])

    def test_a_refused_or_failed_send_leaves_it_owed_and_does_not_raise(self):
        stops = mock.Mock()
        stops.unalerted_approaches.return_value = near(1)
        self.assertEqual(main.report_approaching_stops(
            stops, ApproachNotifier(accepted=False), "john", today=TUESDAY), 0)
        broken = mock.Mock()
        broken.send_stop_approach_alert.side_effect = RuntimeError("webhook down")
        self.assertEqual(main.report_approaching_stops(stops, broken, "john", today=TUESDAY), 0)
        stops.mark_approach_alerted.assert_not_called()

    def test_a_failed_read_does_not_raise(self):
        stops = mock.Mock()
        stops.unalerted_approaches.side_effect = RuntimeError("db down")
        self.assertEqual(main.report_approaching_stops(stops, ApproachNotifier(), "john"), 0)


class ApproachSettingTest(unittest.TestCase):
    def multiple(self, raw):
        with mock.patch.dict("os.environ"), mock.patch("builtins.print"):
            os.environ.pop(main.STOP_APPROACH_ATR_MULTIPLE_ENV, None)
            if raw is not None:
                os.environ[main.STOP_APPROACH_ATR_MULTIPLE_ENV] = raw
            return main.approach_atr_multiple()

    def test_the_default_is_one_atr(self):
        self.assertEqual(self.multiple(None), D("1.0"))
        self.assertEqual(sm.DEFAULT_APPROACH_ATR_MULTIPLE, D("1"))

    def test_a_set_value_is_used(self):
        self.assertEqual(self.multiple("1.5"), D("1.5"))

    def test_an_unparseable_or_non_positive_value_falls_back_to_the_default(self):
        for raw in ("close", "0", "-1", "nan", " "):
            with self.subTest(raw=raw):
                self.assertEqual(self.multiple(raw), D("1.0"))

    def test_the_walk_passes_it_to_every_check(self):
        stops = mock.Mock()
        stops.close_flat_stops.return_value = 0
        stops.due_stops.return_value = [{"owner": "o", "book_id": 1, "stop_id": 1,
                                         "symbol": "AAA", "last_bar_checked": None}]
        stops.check_stop.return_value = {"outcome": "checked"}
        with mock.patch.dict("os.environ", {main.STOP_APPROACH_ATR_MULTIPLE_ENV: "0.75"}), \
                mock.patch("builtins.print"):
            main.check_stops(stops, budget_seconds=10, today=MONDAY)
        self.assertEqual(stops.check_stop.call_args.kwargs["approach_atr_multiple"], D("0.75"))


# --------------------------------------------------------------------------
# 5. The message
# --------------------------------------------------------------------------

def approached(**overrides):
    stop = {"symbol": "INTC", "kind": "fixed", "stop_level": D("20.000000"), "trail_pct": None,
            "approach_close": D("20.600000"), "approached_session": TUESDAY,
            "atr_distance": D("0.6")}
    stop.update(overrides)
    return stop


class ApproachMessageTest(unittest.TestCase):
    def setUp(self):
        self.notifier = Notifier.__new__(Notifier)
        self.notifier.discord_webhook_url = None
        self.sent = []
        self.answer = True
        self.notifier.send_notifications = (
            lambda embed, webhook_url=None: self.sent.append(embed) or self.answer)

    def test_a_line_names_the_close_the_distance_and_the_stop(self):
        self.assertEqual(
            _stop_approach_line(approached()),
            "INTC: closed at $20.60 on 2026-06-16, 0.6 ATR above its stop at $20.00 (fixed)")
        self.assertIn("(trailing 8%)", _stop_approach_line(
            approached(kind="trailing", trail_pct=D("8.0000"))))

    def test_the_message_lists_them_and_says_the_stops_were_not_reached(self):
        self.assertTrue(self.notifier.send_stop_approach_alert(
            [approached()], 3, "2026-06-16", D("1.0")))
        embed = self.sent[0]["embeds"][0]
        self.assertEqual(embed["title"], "3 holding(s) closed near their stop, reported 2026-06-16")
        self.assertIn("- INTC: closed at $20.60", embed["description"])
        self.assertIn("- and 2 more", embed["description"])
        self.assertIn("within 1.0 ATR", embed["description"])
        self.assertIn("have not been reached", embed["description"])

    def test_it_is_unsent_only_when_the_send_says_so(self):
        self.answer = False
        self.assertFalse(self.notifier.send_stop_approach_alert(
            [approached()], 1, "2026-06-16", D("1")))
        for answer in (True, None):  # None: a duplicate that went out earlier
            self.answer = answer
            self.assertTrue(self.notifier.send_stop_approach_alert(
                [approached()], 1, "2026-06-16", D("1")))


if __name__ == "__main__":
    unittest.main()
