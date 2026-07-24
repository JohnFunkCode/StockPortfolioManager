"""Seeded-test-DB round trips for the four thin repositories that lacked
coverage (85%-campaign): FundamentalsRepository, NewsStore,
OptionsPositionStore, and the OhlcvRepository facade. Test database only.
"""
import os
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from quantcore.db import get_connection  # noqa: E402
from quantcore.repositories.fundamentals_repository import FundamentalsRepository  # noqa: E402
from quantcore.repositories.news_repository import NewsStore  # noqa: E402
from quantcore.repositories.ohlcv_repository import OhlcvRepository  # noqa: E402
from quantcore.repositories.options_position_repository import OptionsPositionStore  # noqa: E402

SYM = "ZZREPOS"


def purge():
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM fundamentals_history WHERE symbol = %s", (SYM,))
        conn.execute("DELETE FROM news_articles WHERE symbol = %s", (SYM,))
        conn.execute("DELETE FROM options_positions WHERE symbol = %s", (SYM,))
        conn.execute("DELETE FROM ohlcv WHERE symbol = %s", (SYM,))
        conn.commit()


class RepoTestBase(unittest.TestCase):
    def setUp(self):
        purge()
        self.addCleanup(purge)


class TestFundamentalsRepository(RepoTestBase):
    def setUp(self):
        super().setUp()
        self.repo = FundamentalsRepository()

    def test_set_get_roundtrip_and_miss(self):
        self.assertIsNone(self.repo.get(SYM, "fundamental_score"))
        self.repo.set(SYM, "fundamental_score", {"composite_score": 7, "symbol": SYM})
        cached = self.repo.get(SYM, "fundamental_score")
        self.assertEqual(cached["composite_score"], 7)

    def test_history_and_all_latest(self):
        self.repo.set(SYM, "fundamental_score", {"composite_score": 9, "symbol": SYM})
        history = self.repo.history(SYM, "fundamental_score")
        self.assertGreaterEqual(len(history), 1)
        latest = [e for e in self.repo.get_all_latest("fundamental_score")
                  if e.get("symbol") == SYM]
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["composite_score"], 9)

    def test_invalidate_specific_and_all(self):
        self.repo.set(SYM, "fundamental_score", {"composite_score": 1, "symbol": SYM})
        self.repo.set(SYM, "revenue_growth", {"trajectory": "stable", "symbol": SYM})
        self.repo.invalidate(SYM, "fundamental_score")
        self.assertIsNone(self.repo.get(SYM, "fundamental_score"))
        self.assertIsNotNone(self.repo.get(SYM, "revenue_growth"))
        self.repo.invalidate(SYM)          # all types for the symbol
        self.assertIsNone(self.repo.get(SYM, "revenue_growth"))

    def test_stats_and_ttl(self):
        self.repo.set(SYM, "fundamental_score", {"composite_score": 1, "symbol": SYM})
        stats = self.repo.stats()
        self.assertIn("data_types", stats)
        self.assertGreater(self.repo.ttl_seconds(), 0)


def article(i, published_days_ago=0.5):
    ts = datetime.now(timezone.utc) - timedelta(days=published_days_ago)
    return {
        "title": f"Headline {i}",
        "url": f"https://example.com/{SYM}/{i}",
        "summary": f"Body {i}",
        "publisher": "TestWire",
        "published_at": ts.isoformat(),
        "source": "rss",
    }


class TestNewsStore(RepoTestBase):
    def setUp(self):
        super().setUp()
        self.store = NewsStore()

    def test_save_dedupes_on_url(self):
        first = self.store.save_articles(SYM, [article(1), article(2)])
        self.assertEqual(first, 2)
        again = self.store.save_articles(SYM, [article(1), article(3)])
        self.assertEqual(again, 1)                     # url 1 deduped
        self.assertEqual(self.store.article_count(SYM), 3)
        self.assertIn(SYM, self.store.get_symbols())

    def test_scoring_flow_and_scored_only_filter(self):
        self.store.save_articles(SYM, [article(1), article(2)])
        unscored = self.store.get_unscored_articles(limit=50)
        mine = [a for a in unscored if a["symbol"] == SYM] if unscored and "symbol" in unscored[0] else unscored
        self.assertGreaterEqual(len(mine), 2)
        target = mine[0]
        self.store.update_sentiment(
            article_id=target["article_id"], sentiment="positive",
            sentiment_score=0.91, positive_score=0.91,
            negative_score=0.04, neutral_score=0.05,
        )
        scored = self.store.get_articles(SYM, days=7, scored_only=True)
        self.assertEqual(len(scored), 1)
        self.assertEqual(scored[0]["sentiment"], "positive")
        every = self.store.get_articles(SYM, days=7, scored_only=False)
        self.assertEqual(len(every), 2)

    def test_summary_and_trend_shapes(self):
        self.store.save_articles(SYM, [article(1)])
        summary = self.store.get_sentiment_summary(SYM, days=7)
        self.assertEqual(summary.get("symbol"), SYM)
        trend = self.store.get_sentiment_trend(SYM, days=30)
        self.assertIsInstance(trend, list)


class TestOptionsPositionStore(RepoTestBase):
    def setUp(self):
        super().setUp()
        self.store = OptionsPositionStore()

    def add(self, kind="call", strike=100.0, expiration_days=30,
            purchase_price=3.0, target=None):
        exp = (date.today() + timedelta(days=expiration_days)).isoformat()
        return self.store.add_position(
            symbol=SYM, kind=kind, strike=strike, expiration=exp,
            contracts=2, purchase_price=purchase_price,
            purchase_date=date.today().isoformat(), target_price=target,
        )

    def test_add_get_close_lifecycle(self):
        pid = self.add()
        pos = self.store.get_position(pid)
        self.assertEqual(pos["symbol"], SYM)
        self.assertEqual(self.store.position_count("ACTIVE"), 1)
        self.store.close_position(pid, reason="took profit")
        self.assertEqual(self.store.position_count("ACTIVE"), 0)

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.add(kind="straddle")

    def test_auto_expire_past_positions(self):
        self.add(expiration_days=-2)                   # already expired
        live = self.add(expiration_days=30)
        expired = self.store.auto_expire_past_positions()
        expired_ids = [p["position_id"] for p in expired]
        self.assertEqual(len(expired_ids), 1)
        active = self.store.get_active_positions()
        self.assertEqual([p["position_id"] for p in active], [live])

    def test_pending_alerts_itm_and_expiry(self):
        self.add(kind="call", strike=100.0, expiration_days=5,
                 purchase_price=1.0)                   # ITM + <=7d expiry
        alerts = self.store.get_pending_alerts({SYM: 110.0})
        types = {a["alert_type"] for a in alerts}
        self.assertIn("ITM", types)
        self.assertIn("EXPIRATION_7D", types)
        itm = next(a for a in alerts if a["alert_type"] == "ITM")
        self.assertEqual(itm["intrinsic_value"], 10.0)
        # Missing price -> position silently skipped.
        self.assertEqual(self.store.get_pending_alerts({}), [])


def bars_df(n=5, start_price=100.0):
    # Fixed past business days: deterministic, and always CLOSED bars.
    idx = pd.bdate_range(end="2026-07-17", periods=n, tz="UTC")
    prices = [start_price + i for i in range(n)]
    return pd.DataFrame(
        {"Open": prices, "High": [p + 1 for p in prices],
         "Low": [p - 1 for p in prices], "Close": prices,
         "Volume": [1_000_000] * n},
        index=idx,
    )


class TestOhlcvRepositoryFacade(RepoTestBase):
    def setUp(self):
        super().setUp()
        self.repo = OhlcvRepository()

    def test_store_and_read_back(self):
        self.assertEqual(self.repo.count_cached(SYM, "1d"), 0)
        self.repo.store_bars(SYM, "1d", bars_df())
        self.assertEqual(self.repo.count_cached(SYM, "1d"), 5)
        out = self.repo.get_bars(SYM, "1d", days=30)
        self.assertEqual(len(out), 5)
        self.assertIn("Close", out.columns)

    def test_closed_bar_bookkeeping(self):
        self.repo.store_bars(SYM, "1d", bars_df())
        ts = self.repo.latest_closed_ts(SYM, "1d")
        self.assertIsNotNone(ts)
        self.assertFalse(self.repo.has_open_bar(SYM, "1d"))  # all bars days old

    def test_daily_bars_for_symbols(self):
        self.repo.store_bars(SYM, "1d", bars_df())
        rows = self.repo.daily_bars_for_symbols([SYM])
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["symbol"], SYM)
        self.assertIn("close", rows[0])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Fundamentals cache edge branches (85%-campaign part 10)
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from quantcore.repositories import fundamentals_repository as fr  # noqa: E402


class TestFundamentalsCacheEdges(RepoTestBase):
    def test_ttl_env_parsing(self):
        with patch.dict(os.environ, {"FUNDAMENTALS_CACHE_TTL_HOURS": "2"}):
            self.assertEqual(fr._get_ttl_seconds(), 7200.0)
        with patch.dict(os.environ, {"FUNDAMENTALS_CACHE_TTL_HOURS": "-5"}):
            self.assertEqual(fr._get_ttl_seconds(), 0.0)      # clamped
        with patch.dict(os.environ, {"FUNDAMENTALS_CACHE_TTL_HOURS": "nope"}):
            self.assertEqual(fr._get_ttl_seconds(), 86400.0)  # default on garbage

    def test_ttl_zero_disables_reads(self):
        fr.cache_set(SYM, "fundamental_score", {"composite_score": 5})
        with patch.dict(os.environ, {"FUNDAMENTALS_CACHE_TTL_HOURS": "0"}):
            self.assertIsNone(fr.cache_get(SYM, "fundamental_score"))

    def test_expired_entry_is_a_miss(self):
        fr.cache_set(SYM, "fundamental_score", {"composite_score": 5})
        # Age the row far past any TTL.
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE fundamentals_history SET fetched_at = fetched_at - 999999 "
                "WHERE symbol = %s", (SYM,)
            )
            conn.commit()
        self.assertIsNone(fr.cache_get(SYM, "fundamental_score"))
        # ...but history still sees it.
        self.assertGreaterEqual(len(fr.cache_history(SYM, "fundamental_score")), 1)

    def test_unserializable_and_none_payloads_are_skipped(self):
        fr.cache_set(SYM, "fundamental_score", None)          # no-op
        fr.cache_set(SYM, "fundamental_score", {"bad": object()})  # unserializable
        self.assertIsNone(fr.cache_get(SYM, "fundamental_score"))

    def test_corrupt_json_entry_is_a_miss(self):
        fr.cache_set(SYM, "fundamental_score", {"ok": 1})
        with closing(get_connection()) as conn:
            conn.execute(
                "UPDATE fundamentals_history SET payload = 'not json' "
                "WHERE symbol = %s", (SYM,)
            )
            conn.commit()
        self.assertIsNone(fr.cache_get(SYM, "fundamental_score"))
