"""News staleness alarm hosted in the daily report Job (#275 follow-up)."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import main
from quantcore.services.sentiment import SentimentService


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_news_stale_alert(self, age, ceiling):
        self.sent.append((age, ceiling))


def sentiment(age):
    return SimpleNamespace(news_freshness=lambda: {"latest_fetched_at": "x", "age_hours": age})


class AlertIfNewsStaleTest(unittest.TestCase):
    def test_fresh_news_is_silent(self):
        n = FakeNotifier()
        self.assertFalse(main.alert_if_news_stale(sentiment(20.0), n, 120))
        self.assertEqual(n.sent, [])

    def test_old_news_alarms(self):
        n = FakeNotifier()
        self.assertTrue(main.alert_if_news_stale(sentiment(200.0), n, 120))
        self.assertEqual(n.sent, [(200.0, 120)])

    def test_never_collected_alarms(self):
        n = FakeNotifier()
        self.assertTrue(main.alert_if_news_stale(sentiment(None), n, 120))

    def test_never_raises(self):
        broken = SimpleNamespace(news_freshness=mock.Mock(side_effect=RuntimeError("db")))
        self.assertFalse(main.alert_if_news_stale(broken, FakeNotifier(), 120))
        n = FakeNotifier()
        n.send_news_stale_alert = mock.Mock(side_effect=RuntimeError("webhook"))
        self.assertFalse(main.alert_if_news_stale(sentiment(500.0), n, 120))

    def test_ceiling_from_environment(self):
        n = FakeNotifier()
        with mock.patch.dict("os.environ", {main.NEWS_STALE_MAX_AGE_HOURS_ENV: "10"}):
            self.assertTrue(main.alert_if_news_stale(sentiment(20.0), n))


class NewsFreshnessTest(unittest.TestCase):
    def service(self, latest):
        svc = SentimentService.__new__(SentimentService)
        svc._news = SimpleNamespace(latest_fetched_at=lambda: latest)
        return svc

    def test_empty_table(self):
        self.assertEqual(self.service(None).news_freshness()["age_hours"], None)

    def test_age_in_hours(self):
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        stamp = (now - timedelta(hours=30)).isoformat()
        self.assertAlmostEqual(self.service(stamp).news_freshness(now)["age_hours"], 30.0)

    def test_naive_stamp_treated_as_utc(self):
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        self.assertAlmostEqual(
            self.service("2026-10-02T00:00:00").news_freshness(now)["age_hours"], 12.0)


if __name__ == "__main__":
    unittest.main()
