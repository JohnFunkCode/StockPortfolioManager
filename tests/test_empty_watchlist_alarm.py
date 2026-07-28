"""The empty-watchlist alarm (issue #83, plan decision 8).

The watchlist moved out of a file in the container image and into the
database, and there is deliberately no fallback to `watchlist.yaml` when the
table reads back empty — a silent fallback would re-hide the very persistence
failure the issue is about. That makes the empty case something the daily job
has to shout about instead, which is what these tests pin down: the alert
fires, it carries enough context to act on, and the run continues either way.
"""
import unittest
from unittest.mock import MagicMock, patch

import main
from notifier import Notifier


class _FakeList:
    def __init__(self, stocks):
        self._stocks = stocks

    def list_stocks(self):
        return list(self._stocks)


class AlertIfWatchlistEmptyTest(unittest.TestCase):
    def setUp(self):
        self.notifier = MagicMock()

    def test_empty_watchlist_fires_the_alert_with_the_portfolio_count(self):
        fired = main.alert_if_watchlist_empty(
            _FakeList([]), _FakeList(["a", "b", "c"]), self.notifier
        )

        self.assertTrue(fired)
        self.notifier.send_empty_watchlist_alert.assert_called_once_with(3)

    def test_populated_watchlist_stays_quiet(self):
        fired = main.alert_if_watchlist_empty(
            _FakeList(["ZS"]), _FakeList(["a"]), self.notifier
        )

        self.assertFalse(fired)
        self.notifier.send_empty_watchlist_alert.assert_not_called()

    def test_a_failing_webhook_does_not_abort_the_run(self):
        """Degrade, don't abort. This call sits in front of the options-chain
        capture loop, so letting a Discord outage propagate would cost a day of
        open-interest history to report a missing watchlist.
        """
        self.notifier.send_empty_watchlist_alert.side_effect = RuntimeError(
            "discord is down"
        )

        fired = main.alert_if_watchlist_empty(
            _FakeList([]), _FakeList(["a"]), self.notifier
        )

        self.assertTrue(fired)


class EmptyWatchlistAlertEmbedTest(unittest.TestCase):
    """The Discord payload itself — an alarm nobody can act on is not an alarm."""

    def setUp(self):
        self.notifier = Notifier(portfolio=MagicMock())

    def test_alert_names_the_fix_and_the_degraded_scope(self):
        with patch.object(Notifier, "send_notifications") as send:
            self.notifier.send_empty_watchlist_alert(7)

        body = send.call_args.args[0]["embeds"][0]
        self.assertIn("Watchlist is empty", body["title"])
        self.assertIn("7 portfolio symbol", body["description"])
        self.assertIn("scripts/import_watchlist.py", body["description"])

    def test_title_carries_the_date_so_it_alarms_again_tomorrow(self):
        """send_notifications() dedupes on the title against notification.log,
        so a title without a date would alarm once and then go quiet while the
        watchlist stayed empty.
        """
        from datetime import date

        with patch.object(Notifier, "send_notifications") as send:
            self.notifier.send_empty_watchlist_alert(0)

        title = send.call_args.args[0]["embeds"][0]["title"]
        self.assertIn(f"{date.today():%Y-%m-%d}", title)


if __name__ == "__main__":
    unittest.main()
