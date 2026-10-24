"""Tests for quantcore.analytics.sentiment (issue #411).

Hand-built article lists, so each expected count is readable from the test body.
"""
import unittest
from datetime import date

from quantcore.analytics import sentiment as s


def art(stamp, sentiment="neutral", fetched="2026-10-23T22:00:00+00:00"):
    return {"published_at": stamp, "fetched_at": fetched, "sentiment": sentiment}


class TestScores(unittest.TestCase):
    def test_net_score(self):
        self.assertEqual(s.net_score(3, 6, 15), -0.2)
        self.assertIsNone(s.net_score(0, 0, 0))

    def test_majority_label_ties_go_positive_then_negative(self):
        self.assertEqual(s.majority_label(2, 2, 1), "positive")
        self.assertEqual(s.majority_label(0, 3, 3), "negative")
        self.assertEqual(s.majority_label(1, 0, 4), "neutral")
        self.assertIsNone(s.majority_label(0, 0, 0))


class TestClassifyFlip(unittest.TestCase):
    """The notifier's rule, as a table: a change only counts with negative on one side."""

    def test_table(self):
        cases = [
            ("positive", "negative", s.DETERIORATING),
            ("neutral", "negative", s.DETERIORATING),
            ("negative", "positive", s.IMPROVING),
            ("negative", "neutral", s.IMPROVING),
            ("positive", "neutral", None),
            ("neutral", "positive", None),
            ("negative", "negative", None),
            (None, "negative", None),
            ("negative", None, None),
        ]
        for prev, new, expected in cases:
            with self.subTest(prev=prev, new=new):
                self.assertEqual(s.classify_flip(prev, new), expected)


class TestArticleDate(unittest.TestCase):
    def test_eastern_date_not_utc(self):
        # 01:30 UTC on Oct 20 is still the evening of Oct 19 in New York.
        self.assertEqual(s.article_date("2026-10-20T01:30:00+00:00", None), date(2026, 10, 19))

    def test_z_suffix_and_naive_read_as_utc(self):
        self.assertEqual(s.article_date("2026-10-20T15:00:00Z", None), date(2026, 10, 20))
        self.assertEqual(s.article_date("2026-10-20T03:00:00", None), date(2026, 10, 19))

    def test_falls_back_to_fetched_at(self):
        self.assertEqual(s.article_date("Mon, 20 Oct 2026", "2026-10-21T15:00:00+00:00"),
                         date(2026, 10, 21))
        self.assertIsNone(s.article_date(None, "garbage"))


class TestDailyCounts(unittest.TestCase):
    def test_window_uses_the_article_date(self):
        # Fetched inside the window but published long before it: outside.
        old = art("2026-08-01T15:00:00+00:00", "negative")
        rows = s.daily_counts(
            [art("2026-10-20T15:00:00+00:00", "positive"),
             art("2026-10-20T16:00:00+00:00", "negative"),
             art("2026-10-20T17:00:00+00:00", "negative"), old],
            start=date(2026, 10, 1), end=date(2026, 10, 23))
        self.assertEqual(rows, [{
            "date": "2026-10-20", "article_count": 3, "positive_count": 1,
            "negative_count": 2, "neutral_count": 0, "net_score": -0.333,
        }])

    def test_unscored_articles_are_skipped(self):
        rows = s.daily_counts([art("2026-10-20T15:00:00+00:00", None)],
                              start=date(2026, 10, 1), end=date(2026, 10, 23))
        self.assertEqual(rows, [])


class TestWeeklyBuckets(unittest.TestCase):
    TODAY = date(2026, 10, 23)          # a Friday; its week starts Mon Oct 19

    def build(self, articles, weeks=4, history_start=date(2026, 9, 30)):
        return s.weekly_buckets(articles, today=self.TODAY, weeks=weeks,
                                history_start=history_start)

    def test_every_week_is_present_and_monday_aligned(self):
        out = self.build([])
        self.assertEqual([b["week_start"] for b in out["buckets"]],
                         ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"])
        self.assertEqual(out["buckets"][0]["week_end"], "2026-10-04")
        self.assertEqual(out["weeks_with_articles"], 0)
        self.assertEqual(out["total_articles"], 0)

    def test_week_boundary_is_eastern_midnight_monday(self):
        # Sunday 23:00 ET (03:00 UTC Monday) belongs to the earlier week;
        # Monday 00:30 ET to the later one.
        out = self.build([art("2026-10-19T03:00:00+00:00", "negative"),
                          art("2026-10-19T04:30:00+00:00", "positive")])
        by_week = {b["week_start"]: b for b in out["buckets"]}
        self.assertEqual(by_week["2026-10-12"]["negative_count"], 1)
        self.assertEqual(by_week["2026-10-19"]["positive_count"], 1)

    def test_empty_thin_in_progress_and_before_history(self):
        articles = [art(f"2026-10-13T1{i}:00:00+00:00", "negative") for i in range(4)]
        articles += [art(f"2026-10-2{d}T15:00:00+00:00", "neutral")
                     for d in range(0, 4) for _ in range(3)]
        out = self.build(articles, weeks=6)
        by_week = {b["week_start"]: b for b in out["buckets"]}

        early = by_week["2026-09-14"]
        self.assertTrue(early["before_history"])
        self.assertIsNone(early["net_score"])
        self.assertIsNone(early["label"])

        straddle = by_week["2026-09-28"]    # history starts mid-week: measured, empty
        self.assertFalse(straddle["before_history"])
        self.assertEqual(straddle["article_count"], 0)

        thin = by_week["2026-10-12"]
        self.assertTrue(thin["thin"])
        self.assertEqual(thin["net_score"], -1.0)

        current = by_week["2026-10-19"]
        self.assertTrue(current["in_progress"])
        self.assertFalse(current["thin"])   # 12 articles
        self.assertFalse(thin["in_progress"])
        self.assertEqual(out["weeks_with_articles"], 2)
        self.assertEqual(out["total_articles"], 16)
        self.assertEqual(out["thin_threshold"], s.THIN_WEEK_ARTICLES)

    def test_no_history_marks_every_empty_week(self):
        out = self.build([], history_start=None)
        self.assertTrue(all(b["before_history"] for b in out["buckets"]))
        self.assertIsNone(out["history_start"])

    def test_flip_compares_against_the_previous_week_with_articles(self):
        # positive week, empty week, negative week: the flip lands on the
        # negative week, skipping the empty one; positive -> neutral is no flip.
        articles = [art("2026-09-29T15:00:00+00:00", "positive"),
                    art("2026-10-14T15:00:00+00:00", "negative"),
                    art("2026-10-20T15:00:00+00:00", "neutral")]
        flips = {b["week_start"]: b["flip"] for b in self.build(articles)["buckets"]}
        self.assertEqual(flips, {
            "2026-09-28": None,
            "2026-10-05": None,
            "2026-10-12": s.DETERIORATING,
            "2026-10-19": s.IMPROVING,
        })

    def test_articles_after_today_are_ignored(self):
        out = self.build([art("2026-10-25T15:00:00+00:00", "negative")])
        self.assertEqual(out["total_articles"], 0)

    def test_thin_threshold_is_exactly_ten(self):
        def week_of(n):
            return [art(f"2026-10-20T{10 + i % 10:02d}:00:00+00:00", "neutral")
                    for i in range(n)]
        nine = self.build(week_of(9), weeks=1)["buckets"][0]
        ten = self.build(week_of(10), weeks=1)["buckets"][0]
        self.assertEqual((nine["article_count"], nine["thin"]), (9, True))
        self.assertEqual((ten["article_count"], ten["thin"]), (10, False))

    def test_single_week_window(self):
        out = self.build([art("2026-10-21T15:00:00+00:00", "negative")], weeks=1)
        self.assertEqual(len(out["buckets"]), 1)
        only = out["buckets"][0]
        self.assertEqual(only["week_start"], "2026-10-19")
        self.assertTrue(only["in_progress"])
        self.assertIsNone(only["flip"])          # nothing earlier to flip from
        self.assertEqual(out["weeks_with_articles"], 1)

    def test_history_starting_on_a_week_edge(self):
        # History starts Monday Oct 12: the week ending Sunday Oct 11 is before
        # it, the week starting Oct 12 is measured even though it is empty.
        out = self.build([], weeks=3, history_start=date(2026, 10, 12))
        by_week = {b["week_start"]: b["before_history"] for b in out["buckets"]}
        self.assertEqual(by_week, {"2026-10-05": True, "2026-10-12": False,
                                   "2026-10-19": False})

    def test_weeks_floor_is_one(self):
        self.assertEqual(len(self.build([], weeks=0)["buckets"]), 1)


if __name__ == "__main__":
    unittest.main()
