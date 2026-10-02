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
    return SimpleNamespace(news_freshness=lambda: {"last_collected_at": "x", "age_hours": age})


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
    NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)

    def service(self, last):
        svc = SentimentService.__new__(SentimentService)
        svc._news = SimpleNamespace(last_collection_at=lambda: last)
        return svc

    def test_never_collected(self):
        out = self.service(None).news_freshness()
        self.assertEqual((out["last_collected_at"], out["age_hours"]), (None, None))

    def test_age_in_hours(self):
        last = self.NOW - timedelta(hours=30)
        self.assertAlmostEqual(self.service(last).news_freshness(self.NOW)["age_hours"], 30.0)

    def test_collect_news_records_heartbeat(self):
        calls = []
        svc = SentimentService.__new__(SentimentService)
        svc._news = SimpleNamespace(record_collection=calls.append,
                                    article_count=lambda s: 0)
        svc._collector = SimpleNamespace(collect=lambda syms, score: {}, last_fetched={})
        svc.collect_news("aapl", score=False)
        self.assertEqual(calls, ["AAPL"])


if __name__ == "__main__":
    unittest.main()
