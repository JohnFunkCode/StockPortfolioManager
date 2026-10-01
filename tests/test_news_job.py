"""Tests for news_job.py — the nightly news collector (issue #68)."""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import news_job


class FakeSentiment:
    def __init__(self, fail=(), scored=3, score_raises=False, new=2, fetched=None):
        self.fail, self.scored, self.score_raises, self.new = set(fail), scored, score_raises, new
        self.fetched = fetched
        self.calls, self.score_calls = [], 0

    def collect_news(self, sym, score=True):
        self.calls.append((sym, score))
        if sym in self.fail:
            raise RuntimeError("boom")
        out = {"new_articles": self.new}
        if self.fetched is not None:
            out["fetched_articles"] = self.fetched
        return out

    def score_unscored(self, limit=200):
        self.score_calls += 1
        if self.score_raises:
            raise RuntimeError("no model")
        return self.scored


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_news_gap_alert(self, problems):
        self.sent.append(problems)


class Clock:
    def __init__(self, step=1.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class CollectNewsTest(unittest.TestCase):
    def test_collects_unscored_per_symbol_then_scores_once(self):
        s = FakeSentiment()
        out = news_job.collect_news(["A", "B"], s, budget_seconds=100)
        self.assertEqual(s.calls, [("A", False), ("B", False)])
        self.assertEqual((out["collected"], out["new_articles"], out["scored"]), (2, 4, 3))
        self.assertEqual(s.score_calls, 1)

    def test_one_failing_symbol_does_not_stop_the_walk(self):
        s = FakeSentiment(fail={"A"})
        out = news_job.collect_news(["A", "B"], s, budget_seconds=100)
        self.assertEqual((out["failed"], out["collected"]), (1, 1))
        self.assertEqual(out["failures"], ["A: RuntimeError"])

    def test_budget_exhaustion_stops_but_still_scores(self):
        s = FakeSentiment()
        out = news_job.collect_news(list("ABCDE"), s, budget_seconds=3, clock=Clock())
        self.assertTrue(out["budget_exhausted"])
        self.assertLess(out["attempted"], 5)
        self.assertEqual(s.score_calls, 1)

    def test_scoring_failure_is_recorded_not_raised(self):
        out = news_job.collect_news(["A"], FakeSentiment(score_raises=True), budget_seconds=100)
        self.assertEqual(out["score_error"], "RuntimeError")


class EmptyFetchTest(unittest.TestCase):
    def test_zero_fetched_counts_as_empty_but_unknown_does_not(self):
        out = news_job.collect_news(["A", "B"], FakeSentiment(fetched=0), budget_seconds=100)
        self.assertEqual((out["collected"], out["empty"]), (2, 2))
        out = news_job.collect_news(["A", "B"], FakeSentiment(), budget_seconds=100)
        self.assertEqual(out["empty"], 0)

    def test_dedup_to_zero_new_is_not_empty(self):
        out = news_job.collect_news(["A"], FakeSentiment(new=0, fetched=12), budget_seconds=100)
        self.assertEqual((out["new_articles"], out["empty"]), (0, 0))


class HealthTest(unittest.TestCase):
    def summary(self, **kw):
        base = {"requested": 10, "attempted": 10, "collected": 10, "new_articles": 5,
                "failed": 0, "failures": [], "scored": 5, "budget_exhausted": False,
                "budget_seconds": 900}
        base.update(kw)
        return base

    def test_healthy_night_is_silent(self):
        n = FakeNotifier()
        self.assertEqual(news_job.check_news_health(self.summary(), n, 0.5), [])
        self.assertEqual(n.sent, [])

    def test_alarms_on_failures_budget_and_scoring(self):
        n = FakeNotifier()
        problems = news_job.check_news_health(
            self.summary(failed=8, budget_exhausted=True, score_error="X"), n, 0.5)
        self.assertEqual(len(problems), 3)
        self.assertEqual(len(n.sent), 1)

    def test_alarms_when_sources_return_nothing(self):
        n = FakeNotifier()
        problems = news_job.check_news_health(self.summary(empty=10), n, 0.5, 0.9)
        self.assertEqual(len(problems), 1)
        self.assertIn("returned nothing", problems[0])
        self.assertEqual(len(n.sent), 1)

    def test_a_few_empty_symbols_stay_silent(self):
        n = FakeNotifier()
        self.assertEqual(news_job.check_news_health(self.summary(empty=5), n, 0.5, 0.9), [])

    def test_tiny_runs_do_not_trip_the_empty_alarm(self):
        n = FakeNotifier()
        s = self.summary(requested=3, attempted=3, collected=3, empty=3)
        self.assertEqual(news_job.check_news_health(s, n, 0.5, 0.9), [])

    def test_dead_webhook_does_not_raise(self):
        n = FakeNotifier()
        n.send_news_gap_alert = mock.Mock(side_effect=RuntimeError("down"))
        news_job.check_news_health(self.summary(failed=9), n, 0.5)


class UniverseAndMainTest(unittest.TestCase):
    def services(self):
        return SimpleNamespace(
            watchlist=SimpleNamespace(symbols=lambda: ["A", "B"]),
            portfolio=SimpleNamespace(all_symbols=lambda: ["B", "C"]),
        )

    def test_universe_dedupes_preserving_order(self):
        self.assertEqual(news_job.tracked_universe(self.services()), ["A", "B", "C"])

    def test_closed_market_exits_before_touching_services(self):
        with mock.patch.object(news_job, "ensure_schema"), \
             mock.patch.object(news_job, "is_trading_day", return_value=False), \
             mock.patch.object(news_job, "get_services") as gs:
            self.assertEqual(news_job.main(), 0)
            gs.assert_not_called()

    def test_budget_is_clamped_to_task_deadline(self):
        with mock.patch.dict(os.environ, {news_job.TASK_TIMEOUT_ENV: "300"}):
            self.assertEqual(news_job._budget_seconds(900), 240.0)

    def test_bad_env_value_falls_back(self):
        with mock.patch.dict(os.environ, {news_job.BUDGET_ENV: "abc"}):
            self.assertEqual(news_job._budget_seconds(), news_job.DEFAULT_BUDGET_SECONDS)


if __name__ == "__main__":
    unittest.main()
