"""The nightly fundamentals warmer and its staleness alarm (#147, Part E).

The warmer is the daily job's only budgeted step and its last one, which is
exactly the shape of the defect issue #147 was filed about: a long-running tail
that can fail the job and take the useful side effects — notifications, options
capture — down with it. So the isolation is what these tests pin, alongside the
alarm that keeps the swallowed failure from being silent.
"""
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import main
from notifier import Notifier


class _FakeClock:
    """Advances a fixed amount per read, so budget tests need no sleeping."""

    def __init__(self, step=0.0):
        self.now = 0.0
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


def _freshness(rows, **overrides):
    symbols = [
        {"symbol": sym, "age_seconds": age, "stale": stale, "fetched_at": None}
        for sym, age, stale in rows
    ]
    stale_rows = [r for r in symbols if r["stale"]]
    ages = [r["age_seconds"] for r in symbols if r["age_seconds"] is not None]
    result = {
        "requested": len(symbols),
        "fresh_count": len(symbols) - len(stale_rows),
        "stale_count": len(stale_rows),
        "never_fetched_count": len([r for r in symbols if r["age_seconds"] is None]),
        "coverage": 1.0 if not symbols else (len(symbols) - len(stale_rows)) / len(symbols),
        "oldest_age_seconds": max(ages) if ages else None,
        "symbols": symbols,
    }
    result.update(overrides)
    return result


class WarmFundamentalsCacheTest(unittest.TestCase):
    def setUp(self):
        self.fundamentals = MagicMock()

    def test_only_stale_symbols_are_warmed(self):
        """Fresh symbols would be TTL no-ops, but they would still spend budget
        the tail of the list needs."""
        self.fundamentals.cache_freshness.return_value = _freshness([
            ("AMD", None, True), ("AAPL", 100, False), ("MSFT", 999, True),
        ])

        summary = main.warm_fundamentals_cache(
            ["AMD", "AAPL", "MSFT"], self.fundamentals, budget_seconds=60
        )

        warmed = [c.args[0] for c in self.fundamentals.get_full_fundamental_profile.call_args_list]
        self.assertEqual(warmed, ["AMD", "MSFT"])
        self.assertEqual(summary["warmed"], 2)
        self.assertEqual(summary["candidates"], 2)

    def test_symbols_are_warmed_in_the_order_freshness_returned_them(self):
        """cache_freshness sorts oldest-first; the warmer must not resort."""
        self.fundamentals.cache_freshness.return_value = _freshness([
            ("MSFT", 999, True), ("AMD", None, True), ("NVDA", 500, True),
        ])

        main.warm_fundamentals_cache(["x"], self.fundamentals, budget_seconds=60)

        warmed = [c.args[0] for c in self.fundamentals.get_full_fundamental_profile.call_args_list]
        self.assertEqual(warmed, ["MSFT", "AMD", "NVDA"])

    def test_one_bad_symbol_degrades_one_row(self):
        self.fundamentals.cache_freshness.return_value = _freshness([
            ("AMD", 999, True), ("MSFT", 999, True), ("NVDA", 999, True),
        ])
        self.fundamentals.get_full_fundamental_profile.side_effect = [
            {"symbol": "AMD"}, RuntimeError("yahoo said no"), {"symbol": "NVDA"},
        ]

        summary = main.warm_fundamentals_cache(
            ["AMD", "MSFT", "NVDA"], self.fundamentals, budget_seconds=60
        )

        self.assertEqual(summary["warmed"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(self.fundamentals.get_full_fundamental_profile.call_count, 3)

    def test_the_budget_stops_the_pass_partway(self):
        """The whole point of oldest-first: an unfinished pass is the normal
        case, and the universe converges over a few nights."""
        self.fundamentals.cache_freshness.return_value = _freshness([
            ("A", 999, True), ("B", 998, True), ("C", 997, True), ("D", 996, True),
        ])

        summary = main.warm_fundamentals_cache(
            ["A", "B", "C", "D"], self.fundamentals,
            budget_seconds=2.5, clock=_FakeClock(step=1.0),
        )

        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["skipped"], 2)
        self.assertTrue(summary["budget_exhausted"])

    def test_a_finished_pass_does_not_report_an_exhausted_budget(self):
        self.fundamentals.cache_freshness.return_value = _freshness([("A", 999, True)])

        summary = main.warm_fundamentals_cache(
            ["A"], self.fundamentals, budget_seconds=60, clock=_FakeClock(step=0.1)
        )

        self.assertFalse(summary["budget_exhausted"])
        self.assertEqual(summary["skipped"], 0)

    def test_the_budget_comes_from_the_environment_when_not_passed(self):
        """The knob is documented in CLAUDE.md, the readme and the Cloud Run
        env; resolving it inside the function is what makes it real for the
        one caller that passes nothing."""
        self.fundamentals.cache_freshness.return_value = _freshness([
            ("A", 999, True), ("B", 998, True), ("C", 997, True), ("D", 996, True),
        ])

        with patch.dict("os.environ", {main.WARM_BUDGET_SECONDS_ENV: "2.5"}):
            summary = main.warm_fundamentals_cache(
                ["A", "B", "C", "D"], self.fundamentals, clock=_FakeClock(step=1.0)
            )

        self.assertEqual(summary["budget_seconds"], 2.5)
        self.assertEqual(summary["attempted"], 2)
        self.assertTrue(summary["budget_exhausted"])

    def test_an_explicit_budget_still_beats_the_environment(self):
        self.fundamentals.cache_freshness.return_value = _freshness([("A", 999, True)])

        with patch.dict("os.environ", {main.WARM_BUDGET_SECONDS_ENV: "2.5"}):
            summary = main.warm_fundamentals_cache(
                ["A"], self.fundamentals, budget_seconds=60
            )

        self.assertEqual(summary["budget_seconds"], 60)

    def test_nothing_stale_means_no_fetches_at_all(self):
        self.fundamentals.cache_freshness.return_value = _freshness([("A", 10, False)])

        summary = main.warm_fundamentals_cache(["A"], self.fundamentals, budget_seconds=60)

        self.fundamentals.get_full_fundamental_profile.assert_not_called()
        self.assertEqual(summary["candidates"], 0)


class AlertIfFundamentalsStaleTest(unittest.TestCase):
    def setUp(self):
        self.notifier = MagicMock()

    def test_healthy_coverage_stays_quiet(self):
        fired = main.alert_if_fundamentals_stale(
            _freshness([("A", 100, False), ("B", 100, False)]),
            self.notifier, coverage_floor=0.8, max_age_hours=168,
        )

        self.assertFalse(fired)
        self.notifier.send_stale_fundamentals_alert.assert_not_called()

    def test_coverage_below_the_floor_alarms(self):
        fired = main.alert_if_fundamentals_stale(
            _freshness([("A", 100, False), ("B", 100, True), ("C", 100, True)]),
            self.notifier, coverage_floor=0.8, max_age_hours=168,
        )

        self.assertTrue(fired)
        kwargs = self.notifier.send_stale_fundamentals_alert.call_args.kwargs
        self.assertEqual(kwargs["stale_count"], 2)
        self.assertEqual(kwargs["requested"], 3)

    def test_an_old_tail_alarms_even_when_coverage_looks_healthy(self):
        """The two triggers catch different failures: coverage catches a warmer
        that is not keeping up at all, the age ceiling catches a few symbols
        that fail every single night while the average stays fine."""
        rows = [("A", 100, False)] * 9 + [("Z", 60 * 60 * 24 * 30, True)]
        fresh = _freshness([(f"{s}{i}", age, stale) for i, (s, age, stale) in enumerate(rows)])

        fired = main.alert_if_fundamentals_stale(
            fresh, self.notifier, coverage_floor=0.8, max_age_hours=168
        )

        self.assertTrue(fired)
        self.assertAlmostEqual(
            self.notifier.send_stale_fundamentals_alert.call_args.kwargs["oldest_age_hours"],
            720.0,
        )

    def test_a_failing_webhook_does_not_abort_the_run(self):
        self.notifier.send_stale_fundamentals_alert.side_effect = RuntimeError("discord is down")

        fired = main.alert_if_fundamentals_stale(
            _freshness([("A", 100, True)]), self.notifier,
            coverage_floor=0.8, max_age_hours=168,
        )

        self.assertTrue(fired)

    def test_thresholds_come_from_the_environment_when_not_passed(self):
        with patch.dict("os.environ", {
            main.STALE_COVERAGE_FLOOR_ENV: "0.1",
            main.STALE_MAX_AGE_HOURS_ENV: "100000",
        }):
            fired = main.alert_if_fundamentals_stale(
                _freshness([("A", 100, False), ("B", 100, True)]), self.notifier
            )

        self.assertFalse(fired)  # 50% coverage clears a 10% floor

    def test_an_unparseable_threshold_falls_back_to_the_default(self):
        """A typo in a Cloud Run env var must not silently disarm the alarm."""
        with patch.dict("os.environ", {main.STALE_COVERAGE_FLOOR_ENV: "eighty percent"}):
            self.assertEqual(
                main._env_float(main.STALE_COVERAGE_FLOOR_ENV, 0.8), 0.8
            )


class RunFundamentalsWarmingIsolationTest(unittest.TestCase):
    """The outer guard. This step runs last, after the notifications and the
    options capture have already succeeded; letting it raise would mark that
    run as failed."""

    def setUp(self):
        self.notifier = MagicMock()
        self.fundamentals = MagicMock()

    def test_a_failure_outside_any_symbol_loop_does_not_propagate(self):
        self.fundamentals.cache_freshness.side_effect = RuntimeError("db is gone")

        main.run_fundamentals_warming(["A"], self.fundamentals, self.notifier)  # must not raise

    def test_a_failing_alarm_does_not_propagate_either(self):
        self.fundamentals.cache_freshness.return_value = _freshness([("A", 100, True)])
        self.notifier.send_stale_fundamentals_alert.side_effect = RuntimeError("boom")

        main.run_fundamentals_warming(["A"], self.fundamentals, self.notifier)

    def test_the_env_budget_reaches_the_warmer_through_this_caller(self):
        """The regression the review caught: this is the only production call
        site and it passes no budget, so a budget resolved at the call site
        would leave the env var documented but dead."""
        self.fundamentals.cache_freshness.return_value = _freshness([("A", 999, True)])

        with patch.dict("os.environ", {main.WARM_BUDGET_SECONDS_ENV: "1234"}), \
                patch.object(main, "warm_fundamentals_cache") as warm:
            warm.return_value = {"warmed": 0, "failed": 0, "skipped": 0,
                                 "attempted": 0, "candidates": 0,
                                 "budget_seconds": 1234.0, "budget_exhausted": False}
            main.run_fundamentals_warming(["A"], self.fundamentals, self.notifier)

        self.assertIsNone(warm.call_args.kwargs.get("budget_seconds"))

        # ...and unpatched, that omission resolves to the env value.
        self.fundamentals.cache_freshness.return_value = _freshness([("A", 999, True)])
        with patch.dict("os.environ", {main.WARM_BUDGET_SECONDS_ENV: "1234"}):
            summary = main.warm_fundamentals_cache(["A"], self.fundamentals)
        self.assertEqual(summary["budget_seconds"], 1234.0)

    def test_the_alarm_reads_the_state_the_run_left_behind(self):
        """Re-query after warming, not before -- alarming on the pre-warm
        snapshot would fire every night the warmer worked exactly as intended."""
        self.fundamentals.cache_freshness.side_effect = [
            _freshness([("A", 999, True), ("B", 999, True)]),  # before
            _freshness([("A", 1, False), ("B", 1, False)]),    # after
        ]

        main.run_fundamentals_warming(["A", "B"], self.fundamentals, self.notifier)

        self.notifier.send_stale_fundamentals_alert.assert_not_called()


class StaleFundamentalsAlertEmbedTest(unittest.TestCase):
    """The Discord payload itself — an alarm nobody can act on is not an alarm."""

    def setUp(self):
        self.notifier = Notifier(portfolio=MagicMock())

    def test_alert_names_the_scope_and_a_lever_to_pull(self):
        with patch.object(Notifier, "send_notifications") as send:
            self.notifier.send_stale_fundamentals_alert(
                coverage=0.42, stale_count=58, requested=100, oldest_age_hours=300.0
            )

        body = send.call_args.args[0]["embeds"][0]
        self.assertIn("58 of 100", body["description"])
        self.assertIn("42%", body["description"])
        self.assertIn("300h", body["description"])
        self.assertIn("FUNDAMENTALS_WARM_BUDGET_SECONDS", body["description"])

    def test_a_missing_oldest_age_is_simply_omitted(self):
        with patch.object(Notifier, "send_notifications") as send:
            self.notifier.send_stale_fundamentals_alert(
                coverage=0.0, stale_count=3, requested=3, oldest_age_hours=None
            )

        self.assertIn("3 of 3", send.call_args.args[0]["embeds"][0]["description"])

    def test_title_carries_the_date_so_it_alarms_again_tomorrow(self):
        """send_notifications() dedupes on the title against notification.log,
        so a title without a date would alarm once and then go quiet while the
        cache stayed stale."""
        with patch.object(Notifier, "send_notifications") as send:
            self.notifier.send_stale_fundamentals_alert(0.1, 9, 10, 200.0)

        title = send.call_args.args[0]["embeds"][0]["title"]
        self.assertIn(f"{date.today():%Y-%m-%d}", title)


if __name__ == "__main__":
    unittest.main()
