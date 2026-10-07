"""
news_store.py — SQLite persistence for financial news articles and FinBERT
sentiment scores.

Addresses GitHub issue #9: "Connect the RSS News Reader to SQLPlus, score the
items with Finbert, then surface it as an MCP server."

Each article is stored once per (symbol, url).  Sentiment fields are populated
after FinBERT scoring and can be updated in-place without re-inserting.

Usage:
    from news_store import NewsStore
    store = NewsStore()

    store.save_articles("AAPL", articles)          # list of article dicts
    articles = store.get_articles("AAPL", days=7)  # returns with sentiment if scored
    summary  = store.get_sentiment_summary("AAPL") # aggregate counts + signal
    trend    = store.get_sentiment_trend("AAPL", days=30)  # per-day breakdown
"""

from contextlib import closing
from datetime import datetime, timezone, timedelta
from typing import Optional

from quantcore.db import get_connection

NEWS_HEARTBEAT_INTERVAL = "news"


class NewsStore:
    """Stores financial news articles and their FinBERT sentiment scores in SQLite."""

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_articles(self, symbol: str, articles: list[dict]) -> int:
        """
        Insert articles for a symbol.  Duplicates (same symbol+url) are ignored.
        Returns the number of newly inserted rows.

        Each article dict should contain:
            title, url, summary (opt), publisher (opt), published_at (opt),
            source ('rss' or 'yfinance')
        """
        symbol = symbol.upper()
        now    = datetime.now(timezone.utc).isoformat()
        inserted = 0
        with closing(get_connection()) as conn:
            for art in articles:
                url = (art.get("url") or "").strip()
                if not url:
                    continue
                cur = conn.execute(
                    """
                    INSERT INTO news_articles
                        (symbol, title, summary, publisher, url,
                         published_at, source, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, url) DO NOTHING
                    """,
                    (
                        symbol,
                        (art.get("title") or "").strip(),
                        (art.get("summary") or "").strip() or None,
                        (art.get("publisher") or "").strip() or None,
                        url,
                        art.get("published_at") or None,
                        art.get("source", "unknown"),
                        now,
                    ),
                )
                inserted += cur.rowcount
            conn.commit()
        return inserted

    def update_sentiment(
        self,
        article_id: int,
        sentiment: str,
        sentiment_score: float,
        positive_score: float,
        negative_score: float,
        neutral_score: float,
    ) -> None:
        """Write FinBERT scores back to an existing article row."""
        with closing(get_connection()) as conn:
            conn.execute(
                """
                UPDATE news_articles
                SET sentiment       = %s,
                    sentiment_score = %s,
                    positive_score  = %s,
                    negative_score  = %s,
                    neutral_score   = %s
                WHERE article_id = %s
                """,
                (
                    sentiment,
                    round(sentiment_score, 4),
                    round(positive_score,  4),
                    round(negative_score,  4),
                    round(neutral_score,   4),
                    article_id,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_articles(
        self,
        symbol: str,
        days: int = 7,
        limit: int = 50,
        scored_only: bool = False,
    ) -> list[dict]:
        """
        Return recent articles for a symbol, newest first.

        Parameters
        ----------
        days        : how many days back to look (based on fetched_at)
        limit       : max rows to return
        scored_only : if True, only return articles that have been scored
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        scored_clause = "AND sentiment IS NOT NULL" if scored_only else ""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM news_articles
                WHERE symbol = ?
                  AND fetched_at >= ?
                  {scored_clause}
                ORDER BY published_at DESC, fetched_at DESC
                LIMIT ?
                """,
                (symbol.upper(), since, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_unscored_articles(self, symbol: Optional[str] = None, limit: int = 100) -> list[dict]:
        """Return articles that have not yet been scored by FinBERT."""
        sym_clause = "AND symbol = ?" if symbol else ""
        params = [symbol.upper()] if symbol else []
        params.append(limit)
        with closing(get_connection()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM news_articles
                WHERE sentiment IS NULL {sym_clause}
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_sentiment_summary(self, symbol: str, days: int = 7) -> dict:
        """
        Aggregate sentiment counts and derive an overall signal for a symbol.

        Returns:
            symbol, days, total_articles, scored_articles,
            positive_count, negative_count, neutral_count,
            avg_positive_score, avg_negative_score,
            signal ('BULLISH' | 'BEARISH' | 'MIXED' | 'NEUTRAL' | 'INSUFFICIENT_DATA'),
            signal_strength (0.0–1.0),
            top_positive (list of titles), top_negative (list of titles)
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT sentiment, sentiment_score, positive_score, negative_score,
                       title, published_at
                FROM news_articles
                WHERE symbol = ? AND fetched_at >= ?
                ORDER BY published_at DESC
                """,
                (symbol.upper(), since),
            ).fetchall()
        return _summarize(symbol.upper(), days, rows)

    def get_sentiment_summaries(self, symbols: list[str], days: int = 7) -> dict[str, dict]:
        """``get_sentiment_summary`` for many symbols in one query.

        Every requested symbol gets a summary — one with no articles reads as
        ``INSUFFICIENT_DATA``, exactly as the single-symbol call would.
        """
        wanted = sorted({s.upper() for s in symbols})
        if not wanted:
            return {}
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT symbol, sentiment, sentiment_score, positive_score,
                       negative_score, title, published_at
                FROM news_articles
                WHERE symbol = ANY(%s) AND fetched_at >= %s
                ORDER BY symbol, published_at DESC
                """,
                (wanted, since),
            ).fetchall()
        grouped: dict[str, list] = {sym: [] for sym in wanted}
        for r in rows:
            grouped[r["symbol"]].append(r)
        return {sym: _summarize(sym, days, rs) for sym, rs in grouped.items()}

    def get_sentiment_trend(self, symbol: str, days: int = 30) -> list[dict]:
        """
        Return per-day sentiment counts for trending analysis.

        Each row: date, positive_count, negative_count, neutral_count,
                  net_score (positive% - negative%), article_count
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT DATE(COALESCE(published_at, fetched_at)) AS day,
                       COUNT(*)                                 AS total,
                       SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) AS pos,
                       SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) AS neg,
                       SUM(CASE WHEN sentiment = 'neutral'  THEN 1 ELSE 0 END) AS neu
                FROM news_articles
                WHERE symbol = ? AND fetched_at >= ? AND sentiment IS NOT NULL
                GROUP BY day
                ORDER BY day ASC
                """,
                (symbol.upper(), since),
            ).fetchall()

        trend = []
        for r in rows:
            total = r["total"] or 1
            net   = round((r["pos"] - r["neg"]) / total, 3)
            trend.append({
                "date":           r["day"],
                "article_count":  r["total"],
                "positive_count": r["pos"],
                "negative_count": r["neg"],
                "neutral_count":  r["neu"],
                "net_score":      net,   # +1.0 = all positive, -1.0 = all negative
            })
        return trend

    # ------------------------------------------------------------------
    # Inventory helpers
    # ------------------------------------------------------------------

    def article_count(self, symbol: Optional[str] = None) -> int:
        clause = "WHERE symbol = ?" if symbol else ""
        params = [symbol.upper()] if symbol else []
        with closing(get_connection()) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM news_articles {clause}", params
            ).fetchone()
            return row[0]

    def record_collection(self, symbol: str) -> None:
        """Stamp that a collection pass for ``symbol`` completed (the staleness heartbeat).

        Kept in ``fetch_log`` (interval ``'news'``, epoch seconds) rather than read off
        ``news_articles.fetched_at``: that column only moves when a *new* article is
        inserted, so a quiet weekend would look like a dead job (#275 review).
        """
        with closing(get_connection()) as conn:
            conn.execute(
                """
                INSERT INTO fetch_log (symbol, interval, fetched_at) VALUES (%s,%s,%s)
                ON CONFLICT (symbol, interval) DO UPDATE SET fetched_at = EXCLUDED.fetched_at
                """,
                (symbol.upper(), NEWS_HEARTBEAT_INTERVAL,
                 int(datetime.now(timezone.utc).timestamp())),
            )
            conn.commit()

    def last_collection_at(self) -> Optional[datetime]:
        """UTC time of the most recent completed collection pass, or None if never."""
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT MAX(fetched_at) FROM fetch_log WHERE interval = %s",
                (NEWS_HEARTBEAT_INTERVAL,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return datetime.fromtimestamp(int(row[0]), tz=timezone.utc)

    def get_symbols(self) -> list[str]:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM news_articles ORDER BY symbol"
            ).fetchall()
            return [r[0] for r in rows]


def _signal(n_pos: int, n_neg: int, n_scored: int) -> tuple[str, float]:
    """The sentiment signal and its strength from scored-article counts."""
    if n_scored < 3:
        return "INSUFFICIENT_DATA", 0.0
    pos_pct = n_pos / n_scored
    neg_pct = n_neg / n_scored
    if pos_pct >= 0.60:
        return "BULLISH", round(pos_pct, 2)
    if neg_pct >= 0.60:
        return "BEARISH", round(neg_pct, 2)
    if abs(pos_pct - neg_pct) > 0.15:
        return "MIXED", round(abs(pos_pct - neg_pct), 2)
    return "NEUTRAL", round(1.0 - abs(pos_pct - neg_pct), 2)


def _avg(rows, column):
    """Mean of a score column over scored rows (NULL reads as 0), or None."""
    if not rows:
        return None
    return round(sum(r[column] or 0 for r in rows) / len(rows), 3)


def _summarize(symbol: str, days: int, rows) -> dict:
    """Turn one symbol's article rows (newest first) into its sentiment summary."""
    total = len(rows)
    by_sentiment: dict = {"positive": [], "negative": [], "neutral": []}
    scored = []
    for r in rows:
        if r["sentiment"] is not None:
            scored.append(r)
            by_sentiment.setdefault(r["sentiment"], []).append(r)
    n_scored = len(scored)
    pos, neg, neu = (by_sentiment[k] for k in ("positive", "negative", "neutral"))

    avg_pos = _avg(scored, "positive_score")
    avg_neg = _avg(scored, "negative_score")

    signal, strength = _signal(len(pos), len(neg), n_scored)

    return {
        "symbol":              symbol,
        "days":                days,
        "total_articles":      total,
        "scored_articles":     n_scored,
        "positive_count":      len(pos),
        "negative_count":      len(neg),
        "neutral_count":       len(neu),
        "avg_positive_score":  avg_pos,
        "avg_negative_score":  avg_neg,
        "signal":              signal,
        "signal_strength":     strength,
        "top_positive":        [r["title"] for r in pos[:3]],
        "top_negative":        [r["title"] for r in neg[:3]],
    }
