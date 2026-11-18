"""The daily job's book-value snapshot step (#431 plan step 5.1).

The step values every book owed a snapshot at today's close, then alarms on
any book still owed one, reading the database again. `PerformanceService`
is faked here; `tests/test_performance_service.py` runs it against the
database.
"""
import datetime
import unittest
from pathlib import Path
from unittest import mock

import main

TODAY = datetime.date(2026, 7, 14)          # a Tuesday
SATURDAY = datetime.date(2026, 7, 18)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_snapshots_owed_alert(self, book_count, reasons):
        self.sent.append((book_count, reasons))


class _Clock:
    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def owed(book_id, reason="missing", owner="someone"):
    return {"owner": owner, "book_id": book_id, "kind": "paper", "reason": reason}


def fake_performance(due=None, after=None, rows=None, fail=()):
    """`due_snapshots` answers `due` first and `after` on the next call.
    `snapshot_book` answers `rows[book_id]` (default a valued row)."""
    performance = mock.Mock()
    due = due if due is not None else [owed(1), owed(2)]
    performance.due_snapshots.side_effect = [due, after or []]
    rows = rows or {}

    def snapshot(owner, book_id, session):
        if book_id in fail:
            raise RuntimeError("secret detail")
        return rows.get(book_id, {"book_value": 1})

    performance.snapshot_book.side_effect = snapshot
    return performance


class TakeBookSnapshotsTest(unittest.TestCase):
    def test_takes_every_owed_book_for_today(self):
        performance = fake_performance(rows={2: {"book_value": None}, 3: None},
                                       due=[owed(1), owed(2), owed(3)])
        summary = main.take_book_snapshots(performance, budget_seconds=100, today=TODAY)
        performance.due_snapshots.assert_called_once_with(today=TODAY)
        self.assertEqual(
            [c.kwargs for c in performance.snapshot_book.call_args_list],
            [{"owner": "someone", "book_id": n, "session": TODAY} for n in (1, 2, 3)])
        self.assertEqual(
            {k: summary[k] for k in ("due", "attempted", "valued", "incomplete", "empty", "failed")},
            {"due": 3, "attempted": 3, "valued": 1, "incomplete": 1, "empty": 1, "failed": 0})
        self.assertFalse(summary["budget_exhausted"])

    def test_one_bad_book_does_not_stop_the_walk_or_leak_its_text(self):
        performance = fake_performance(fail=(1,))
        with mock.patch("builtins.print") as printed:
            summary = main.take_book_snapshots(performance, budget_seconds=100, today=TODAY)
        self.assertEqual((summary["failed"], summary["valued"]), (1, 1))
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("secret detail", output)
        self.assertNotIn("someone", output)

    def test_stops_at_the_budget(self):
        performance = fake_performance()
        summary = main.take_book_snapshots(performance, budget_seconds=0.5,
                                           clock=_Clock(), today=TODAY)
        self.assertEqual(summary["attempted"], 0)
        self.assertTrue(summary["budget_exhausted"])

    def test_an_unparseable_budget_falls_back_to_the_default(self):
        performance = fake_performance()
        with mock.patch.dict("os.environ", {main.BOOK_SNAPSHOT_BUDGET_SECONDS_ENV: "soon"}), \
                mock.patch("builtins.print") as printed:
            main.take_book_snapshots(performance, today=TODAY)
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn(f"budget {main.DEFAULT_BOOK_SNAPSHOT_BUDGET_SECONDS:.0f}s", output)


    def test_a_marked_book_is_restated_through_today_instead(self):
        """Step 5.2: the rebuild takes today's row too, so the book isn't
        snapshotted a second time."""
        marked = {**owed(2, "restate"), "restate_from": TODAY - datetime.timedelta(days=5)}
        performance = fake_performance(due=[owed(1), marked])
        performance.restate_book.return_value = {
            "restated_from": marked["restate_from"], "restated": 3, "inserted": 0,
            "unvalued": [TODAY]}
        with mock.patch("builtins.print") as printed:
            summary = main.take_book_snapshots(performance, budget_seconds=100, today=TODAY)
        performance.restate_book.assert_called_once_with(owner="someone", book_id=2,
                                                         through=TODAY)
        self.assertEqual([c.kwargs["book_id"] for c in performance.snapshot_book.call_args_list],
                         [1])
        self.assertEqual((summary["restated"], summary["incomplete"], summary["valued"]),
                         (1, 1, 1))
        self.assertNotIn("someone", " ".join(str(c) for c in printed.call_args_list))

    def test_a_failed_restatement_is_counted_and_the_walk_goes_on(self):
        marked = {**owed(1, "restate"), "restate_from": TODAY}
        performance = fake_performance(due=[marked, owed(2)])
        performance.restate_book.side_effect = RuntimeError("secret detail")
        with mock.patch("builtins.print") as printed:
            summary = main.take_book_snapshots(performance, budget_seconds=100, today=TODAY)
        self.assertEqual((summary["failed"], summary["valued"], summary["restated"]), (1, 1, 0))
        self.assertNotIn("secret detail", " ".join(str(c) for c in printed.call_args_list))


class AlertIfSnapshotsOwedTest(unittest.TestCase):
    def test_quiet_when_nothing_is_owed(self):
        performance = mock.Mock()
        performance.due_snapshots.return_value = []
        n = FakeNotifier()
        self.assertFalse(main.alert_if_snapshots_owed(performance, n, today=TODAY))
        self.assertEqual(n.sent, [])

    def test_counts_by_reason_and_names_no_owner(self):
        performance = mock.Mock()
        performance.due_snapshots.return_value = [
            owed(1, "missing"), owed(2, "stale"), owed(3, "missing")]
        n = FakeNotifier()
        with mock.patch("builtins.print") as printed:
            self.assertTrue(main.alert_if_snapshots_owed(performance, n, today=TODAY))
        self.assertEqual(n.sent, [(3, {"missing": 2, "stale": 1})])
        self.assertNotIn("someone", " ".join(str(c) for c in printed.call_args_list))

    def test_a_dead_webhook_does_not_raise(self):
        performance = mock.Mock()
        performance.due_snapshots.return_value = [owed(1)]
        notifier = mock.Mock()
        notifier.send_snapshots_owed_alert.side_effect = RuntimeError("webhook")
        with mock.patch("builtins.print"):
            self.assertTrue(main.alert_if_snapshots_owed(performance, notifier, today=TODAY))


class RunBookSnapshotsTest(unittest.TestCase):
    def test_snapshots_then_alarms_on_what_the_database_still_owes(self):
        performance = fake_performance(due=[owed(1)], after=[owed(1, "incomplete")])
        n = FakeNotifier()
        main.run_book_snapshots(performance, n, budget_seconds=100, today=TODAY)
        self.assertEqual([c[0] for c in performance.method_calls],
                         ["due_snapshots", "snapshot_book", "due_snapshots"])
        self.assertEqual(n.sent, [(1, {"incomplete": 1})])

    def test_never_raises_and_still_checks(self):
        performance = mock.Mock()
        performance.due_snapshots.side_effect = [RuntimeError("secret detail"), []]
        with mock.patch("builtins.print") as printed:
            main.run_book_snapshots(performance, FakeNotifier(), today=TODAY)
        self.assertEqual(performance.due_snapshots.call_count, 2)
        output = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("secret detail", output)

    def test_does_nothing_on_a_closed_day(self):
        performance = fake_performance()
        n = FakeNotifier()
        main.run_book_snapshots(performance, n, today=SATURDAY)
        self.assertEqual(performance.method_calls, [])
        self.assertEqual(n.sent, [])


class NotifierSnapshotsAlertTest(unittest.TestCase):
    def test_embed_says_why_and_names_no_book(self):
        from notifier import Notifier

        notifier = Notifier.__new__(Notifier)
        notifier.send_notifications = mock.Mock()
        notifier.send_snapshots_owed_alert(3, {"missing": 2, "stale": 1})
        embed = notifier.send_notifications.call_args.args[0]["embeds"][0]
        self.assertIn("3 book(s) have no complete value", embed["description"])
        self.assertIn("2 book(s) have no snapshot", embed["description"])
        self.assertIn("1 book(s) priced a holding from an earlier session", embed["description"])

    def test_a_book_still_to_restate_is_named_as_such(self):
        from notifier import Notifier

        notifier = Notifier.__new__(Notifier)
        notifier.send_notifications = mock.Mock()
        notifier.send_snapshots_owed_alert(1, {"restate": 1})
        embed = notifier.send_notifications.call_args.args[0]["embeds"][0]
        self.assertIn("1 book(s) have a change to their history still to restate",
                      embed["description"])


class MainOrderTest(unittest.TestCase):
    def test_the_snapshot_step_runs_after_the_close_out(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        body = source[source.index('if __name__ == "__main__":'):]
        self.assertLess(body.index("run_ohlcv_closeout("), body.index("run_book_snapshots("))
        self.assertLess(body.index("run_stop_checks("), body.index("run_book_snapshots("))


if __name__ == "__main__":
    unittest.main()
