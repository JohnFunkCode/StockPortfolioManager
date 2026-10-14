"""scripts/backfill_news_sentiment.py -- the FinBERT backlog catch-up (#375).

A fake sentiment service stands in for FinBERT and the database: it holds a
per-day backlog and "scores" the oldest rows first, as the real
score_unscored(oldest_first=True) does.
"""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from scripts import backfill_news_sentiment as backfill_mod


class FakeSentiment:
    def __init__(self, by_day, broken=False):
        self.by_day = dict(by_day)
        self.broken = broken
        self.calls = []

    def count_unscored_by_day(self, since=None):
        return {d: n for d, n in sorted(self.by_day.items()) if n and d >= since}

    def score_unscored(self, limit=200, *, since=None, oldest_first=False):
        self.calls.append({"limit": limit, "since": since, "oldest_first": oldest_first})
        if self.broken:
            return 0
        scored = 0
        for day in sorted(self.by_day):
            if day < since:
                continue
            take = min(self.by_day[day], limit - scored)
            self.by_day[day] -= take
            scored += take
        return scored


def run_main(fake, *argv):
    out, err = io.StringIO(), io.StringIO()
    services = mock.Mock(sentiment=fake)
    with mock.patch("quantcore.db.ensure_schema") as ensure, \
         mock.patch("quantcore.services.registry.get_services", return_value=services), \
         mock.patch("quantcore.db_safety.assert_not_production") as guard, \
         redirect_stdout(out), redirect_stderr(err):
        code = backfill_mod.main(list(argv))
    return code, out.getvalue(), err.getvalue(), ensure, guard


class BackfillTest(unittest.TestCase):
    def test_drains_the_backlog_oldest_first_from_since(self):
        fake = FakeSentiment({"2026-10-01": 4, "2026-10-02": 3, "2026-10-03": 5})
        with redirect_stdout(io.StringIO()):
            scored, remaining = backfill_mod.backfill(fake, "2026-10-02", batch=3)
        self.assertEqual((scored, remaining), (8, 0))
        self.assertEqual(fake.by_day, {"2026-10-01": 4, "2026-10-02": 0, "2026-10-03": 0})
        self.assertEqual(len(fake.calls), 3)
        self.assertTrue(all(c == {"limit": 3, "since": "2026-10-02", "oldest_first": True}
                            for c in fake.calls))

    def test_nothing_to_do_scores_nothing(self):
        fake = FakeSentiment({"2026-09-30": 9})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(backfill_mod.backfill(fake, "2026-10-02", batch=5), (0, 0))
        self.assertEqual(fake.calls, [])

    def test_a_zero_batch_stops_instead_of_looping(self):
        fake = FakeSentiment({"2026-10-02": 6}, broken=True)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(backfill_mod.backfill(fake, "2026-10-02", batch=5), (0, 6))
        self.assertEqual(len(fake.calls), 1)


class MainTest(unittest.TestCase):
    def test_dry_run_counts_and_writes_nothing(self):
        fake = FakeSentiment({"2026-10-02": 2, "2026-10-04": 1})
        code, out, _, ensure, guard = run_main(fake, "--since", "2026-10-02", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])
        self.assertIn("2026-10-02: 2", out)
        self.assertIn("3 unscored article(s) fetched since 2026-10-02 (dry run)", out)
        ensure.assert_called_once_with()
        guard.assert_called_once_with()

    def test_real_run_reports_totals(self):
        fake = FakeSentiment({"2026-10-02": 2})
        code, out, err, _, _ = run_main(fake, "--since", "2026-10-02", "--batch", "10")
        self.assertEqual(code, 0, err)
        self.assertIn("scored 2 article(s) fetched since 2026-10-02; 0 remain unscored", out)

    def test_broken_model_exits_non_zero(self):
        fake = FakeSentiment({"2026-10-02": 2}, broken=True)
        code, _, err, _, _ = run_main(fake, "--since", "2026-10-02")
        self.assertEqual(code, 1)
        self.assertIn("FinBERT failed to load", err)

    def test_allow_prod_skips_the_production_guard(self):
        fake = FakeSentiment({})
        code, _, _, _, guard = run_main(fake, "--since", "2026-10-02", "--allow-prod")
        self.assertEqual(code, 0)
        guard.assert_not_called()

    def test_argument_validation(self):
        for argv in (["--since", "02-10-2026"], ["--since", "2026-10-02", "--batch", "0"], []):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as ctx:
                backfill_mod.main(argv)
            self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
