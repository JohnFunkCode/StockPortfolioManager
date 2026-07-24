"""Unit tests for SentimentService / NewsCollector / feed parsers.

Coverage uplift (July 2026). No network and no model loads: the FinBERT
lazy-loader globals are pinned per-test (transformers IS installed in dev,
so an unpatched probe would download the real model), RSS bytes are served
from an in-memory XML document, and yfinance payloads are literal dicts in
both the new nested-content and legacy flat shapes.
"""
import unittest
from unittest.mock import Mock, patch

from quantcore.services import sentiment as sentiment_mod
from quantcore.services.sentiment import (
    NewsCollector,
    SentimentService,
    _fetch_rss,
    _fetch_yfinance_news,
    _text_for_scoring,
)

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Yahoo! Finance: INTC News</title>
  <item>
    <title>Intel wins foundry deal</title>
    <link>https://example.com/a1</link>
    <description>Big contract.</description>
    <pubDate>Mon, 13 Jul 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Chips rally</title>
    <link>https://example.com/a2</link>
  </item>
</channel></rss>
"""


def finbert_pinned(available: bool):
    """Pin the lazy-loader verdict so no test can trigger a real model load."""
    return patch.object(sentiment_mod, "_finbert_available", available)


class TestTextForScoring(unittest.TestCase):
    def test_title_and_summary_joined(self):
        self.assertEqual(
            _text_for_scoring({"title": "T", "summary": "S"}), "T. S"
        )

    def test_title_only(self):
        self.assertEqual(_text_for_scoring({"title": "T", "summary": None}), "T")


class TestFetchRss(unittest.TestCase):
    def test_parses_feed_items(self):
        fake_resp = Mock()
        fake_resp.read.return_value = RSS_XML
        fake_resp.__enter__ = lambda s: fake_resp
        fake_resp.__exit__ = lambda s, *a: False
        with patch.object(sentiment_mod, "urlopen", return_value=fake_resp):
            articles = _fetch_rss("INTC")
        self.assertEqual(len(articles), 2)
        first = articles[0]
        self.assertEqual(first["title"], "Intel wins foundry deal")
        self.assertEqual(first["url"], "https://example.com/a1")
        self.assertEqual(first["source"], "rss")
        self.assertIn("2026-07-13", first["published_at"])
        self.assertIn("INTC", first["publisher"])
        # Second item has no pubDate — survives with published_at=None.
        self.assertIsNone(articles[1]["published_at"])

    def test_network_failure_returns_empty(self):
        with patch.object(sentiment_mod, "urlopen", side_effect=OSError("down")):
            self.assertEqual(_fetch_rss("INTC"), [])


class TestFetchYfinanceNews(unittest.TestCase):
    def test_new_style_payload(self):
        gateway = Mock()
        gateway.news.return_value = [{
            "id": "x",
            "content": {
                "title": "Headline",
                "summary": "Body",
                "pubDate": "2026-07-10T09:00:00Z",
                "clickThroughUrl": {"url": "https://example.com/n1"},
                "provider": {"displayName": "Reuters"},
            },
        }]
        out = _fetch_yfinance_news("INTC", gateway)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["url"], "https://example.com/n1")
        self.assertEqual(out[0]["publisher"], "Reuters")
        self.assertEqual(out[0]["published_at"], "2026-07-10T09:00:00Z")
        self.assertEqual(out[0]["source"], "yfinance")

    def test_legacy_payload_and_unix_timestamp(self):
        gateway = Mock()
        gateway.news.return_value = [{
            "title": "Old style",
            "link": "https://example.com/legacy",
            "providerPublishTime": 1_750_000_000,
        }]
        out = _fetch_yfinance_news("INTC", gateway)
        self.assertEqual(out[0]["url"], "https://example.com/legacy")
        self.assertTrue(out[0]["published_at"].startswith("2025-06-15"))

    def test_urlless_items_skipped_and_gateway_errors_swallowed(self):
        gateway = Mock()
        gateway.news.return_value = [{"content": {"title": "no url"}}]
        self.assertEqual(_fetch_yfinance_news("INTC", gateway), [])
        gateway.news.side_effect = RuntimeError("boom")
        self.assertEqual(_fetch_yfinance_news("INTC", gateway), [])


class TestNewsCollector(unittest.TestCase):
    def make(self):
        store = Mock()
        store.save_articles.return_value = 3
        return NewsCollector(store=store, gateway=Mock()), store

    def test_collect_merges_sources_and_counts_inserts(self):
        collector, store = self.make()
        with patch.object(sentiment_mod, "_fetch_rss", return_value=[{"t": 1}]), \
             patch.object(sentiment_mod, "_fetch_yfinance_news", return_value=[{"t": 2}]), \
             patch.object(collector, "score_unscored") as score:
            totals = collector.collect(["intc"], score=False)
        self.assertEqual(totals, {"INTC": 3})
        args, _ = store.save_articles.call_args
        self.assertEqual(args[0], "INTC")
        self.assertEqual(len(args[1]), 2)  # rss + yfinance merged
        score.assert_not_called()

    def test_collect_scores_when_asked(self):
        collector, _ = self.make()
        with patch.object(sentiment_mod, "_fetch_rss", return_value=[]), \
             patch.object(sentiment_mod, "_fetch_yfinance_news", return_value=[]), \
             patch.object(collector, "score_unscored") as score:
            collector.collect(["INTC"], score=True)
        score.assert_called_once()

    def test_score_unscored_no_articles(self):
        collector, store = self.make()
        store.get_unscored_articles.return_value = []
        self.assertEqual(collector.score_unscored(), 0)

    def test_score_unscored_finbert_unavailable(self):
        collector, store = self.make()
        store.get_unscored_articles.return_value = [{"article_id": 1, "title": "x"}]
        with finbert_pinned(False):
            self.assertEqual(collector.score_unscored(), 0)
        store.update_sentiment.assert_not_called()

    def test_score_unscored_scores_and_survives_update_failures(self):
        collector, store = self.make()
        store.get_unscored_articles.return_value = [
            {"article_id": 1, "title": "good news", "summary": "s"},
            {"article_id": 2, "title": "  ", "summary": ""},   # empty text: skipped
            {"article_id": 3, "title": "bad db row", "summary": "s"},
        ]
        store.update_sentiment.side_effect = [None, RuntimeError("db hiccup")]
        result = {
            "sentiment": "positive", "sentiment_score": 0.9,
            "positive_score": 0.9, "negative_score": 0.05, "neutral_score": 0.05,
        }
        with finbert_pinned(True), \
             patch.object(sentiment_mod, "_score_text", return_value=result):
            scored = collector.score_unscored()
        self.assertEqual(scored, 1)  # one clean, one skipped, one failed update
        self.assertEqual(store.update_sentiment.call_count, 2)


class SentimentServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.news = Mock()
        self.snapshots = Mock()
        self.yf = Mock()
        self.service = SentimentService(
            news_repository=self.news,
            sentiment_repository=self.snapshots,
            yfinance_gateway=self.yf,
        )


class TestServiceSurfaces(SentimentServiceTestBase):
    def test_collect_news_reports_counts_and_capabilities(self):
        self.news.article_count.return_value = 12
        with patch.object(self.service._collector, "collect", return_value={"INTC": 4}):
            out = self.service.collect_news("intc", score=False)
        self.assertEqual(out["symbol"], "INTC")
        self.assertEqual(out["new_articles"], 4)
        self.assertEqual(out["total_articles"], 12)
        self.assertTrue(out["rss_available"])       # feedparser installed in dev
        self.assertIn("finbert_available", out)

    def test_get_news_sentiment_slims_articles(self):
        self.news.get_sentiment_summary.return_value = {"symbol": "INTC", "signal": "neutral"}
        self.news.get_articles.return_value = [{
            "article_id": 1, "title": "T", "publisher": "P",
            "published_at": "2026-07-10", "url": "u",
            "sentiment": "positive", "sentiment_score": 0.8,
            "raw_blob": "should not leak",
        }]
        out = self.service.get_news_sentiment("intc")
        self.assertEqual(out["signal"], "neutral")
        self.assertEqual(
            set(out["articles"][0]),
            {"article_id", "title", "publisher", "published_at", "url",
             "sentiment", "sentiment_score"},
        )

    def test_trend_and_symbol_listing(self):
        self.news.get_sentiment_trend.return_value = [{"date": "2026-07-10"}]
        out = self.service.get_sentiment_trend("intc", days=14)
        self.assertEqual(out["days"], 14)
        self.assertEqual(len(out["trend"]), 1)

        self.news.get_symbols.return_value = ["INTC", "WMT"]
        listing = self.service.list_news_symbols()
        self.assertEqual(listing["total_symbols"], 2)


NEW_STYLE_ITEM = {
    "content": {
        "title": "Great quarter",
        "summary": "Beats estimates",
        "pubDate": "2026-07-10T09:00:00Z",
        "provider": {"displayName": "Bloomberg"},
        "canonicalUrl": {"url": "https://example.com/gq"},
    }
}


class TestGetNews(SentimentServiceTestBase):
    def test_without_finbert_notes_the_gap(self):
        self.yf.news.return_value = [NEW_STYLE_ITEM]
        with finbert_pinned(False):
            out = self.service.get_news("intc")
        self.assertEqual(out["article_count"], 1)
        self.assertEqual(out["articles"][0]["publisher"], "Bloomberg")
        self.assertNotIn("sentiment_summary", out)
        self.assertIn("sentiment_note", out)

    def test_with_finbert_builds_the_summary(self):
        self.yf.news.return_value = [NEW_STYLE_ITEM, NEW_STYLE_ITEM]
        scored = {"sentiment": "positive", "sentiment_score": 0.91}
        with finbert_pinned(True), \
             patch.object(sentiment_mod, "_score_sentiment", return_value=scored):
            out = self.service.get_news("INTC")
        summary = out["sentiment_summary"]
        self.assertEqual(summary["overall"], "positive")
        self.assertEqual(summary["positive_count"], 2)
        self.assertEqual(summary["scored_count"], 2)

    def test_get_security_news_persists_snapshot_best_effort(self):
        with patch.object(self.service, "get_news", return_value={"symbol": "INTC"}):
            out = self.service.get_security_news("intc")
        self.snapshots.save_snapshot.assert_called_once()
        self.assertEqual(out["symbol"], "INTC")

        self.snapshots.save_snapshot.side_effect = RuntimeError("db away")
        with patch.object(self.service, "get_news", return_value={"symbol": "INTC"}):
            out = self.service.get_security_news("INTC")  # must not raise
        self.assertEqual(out["symbol"], "INTC")


# ---------------------------------------------------------------------------
# Dashboard + scoring wrapper (85%-campaign part 7)
# ---------------------------------------------------------------------------

def snap(sentiment, neg=0):
    return {
        "captured_at": "2026-07-24T00:00:00Z", "overall_sentiment": sentiment,
        "positive_count": 1, "negative_count": neg, "neutral_count": 0,
        "scored_count": 1 + neg, "article_count": 2 + neg,
    }


class TestSentimentDashboard(SentimentServiceTestBase):
    PORTFOLIO = [{"symbol": "AAA", "name": "Alpha", "source": "portfolio", "tags": []}]
    WATCHLIST = [
        {"symbol": "AAA", "name": "Alpha", "source": "watchlist", "tags": ["dual"]},
        {"symbol": "BBB", "name": "Beta", "source": "watchlist", "tags": []},
    ]

    def arm(self):
        self.snapshots.get_all_latest.return_value = {
            "AAA": snap("positive"),
            "BBB": snap("negative", neg=5),
            "ORPHAN": snap("neutral"),   # scored but untracked -> watchlist src
        }

    def test_ranked_negative_first_with_dual_source(self):
        self.arm()
        out = self.service.get_sentiment_dashboard(
            [dict(s) for s in self.PORTFOLIO], [dict(s) for s in self.WATCHLIST]
        )
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["items"][0]["symbol"], "BBB")       # negative first
        aaa = next(i for i in out["items"] if i["symbol"] == "AAA")
        self.assertEqual(aaa["source"], "both")
        self.assertEqual(aaa["tags"], ["dual"])

    def test_source_filters(self):
        self.arm()
        portfolio_only = self.service.get_sentiment_dashboard(
            [dict(s) for s in self.PORTFOLIO], [dict(s) for s in self.WATCHLIST],
            source_filter="portfolio",
        )
        self.assertEqual([i["symbol"] for i in portfolio_only["items"]], ["AAA"])
        watch_only = self.service.get_sentiment_dashboard(
            [dict(s) for s in self.PORTFOLIO], [dict(s) for s in self.WATCHLIST],
            source_filter="watchlist",
        )
        self.assertEqual({i["symbol"] for i in watch_only["items"]},
                         {"AAA", "BBB", "ORPHAN"})


class TestScoreSentimentWrapper(unittest.TestCase):
    def test_blank_text_short_circuits(self):
        self.assertIsNone(sentiment_mod._score_sentiment("   "))

    def test_rounds_and_slims_the_finbert_result(self):
        full = {"sentiment": "negative", "sentiment_score": 0.87654321,
                "positive_score": 0.1, "negative_score": 0.88, "neutral_score": 0.02}
        with patch.object(sentiment_mod, "_score_text", return_value=full):
            out = sentiment_mod._score_sentiment("bad quarter")
        self.assertEqual(out, {"sentiment": "negative", "sentiment_score": 0.8765})

    def test_model_unavailable_or_error_returns_none(self):
        with patch.object(sentiment_mod, "_score_text", return_value=None):
            self.assertIsNone(sentiment_mod._score_sentiment("text"))
        with patch.object(sentiment_mod, "_score_text",
                          side_effect=RuntimeError("torch OOM")):
            self.assertIsNone(sentiment_mod._score_sentiment("text"))


if __name__ == "__main__":
    unittest.main()
