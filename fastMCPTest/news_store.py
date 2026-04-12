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

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(__file__).parent / "news_sentiment.db"

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS news_articles (
    article_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    summary         TEXT,
    publisher       TEXT,
    url             TEXT    NOT NULL,
    published_at    TEXT,           -- ISO-8601 (best-effort from source)
    source          TEXT    NOT NULL,  -- 'rss' or 'yfinance'
    fetched_at      TEXT    NOT NULL,  -- ISO-8601 UTC timestamp of fetch
    sentiment       TEXT    CHECK(sentiment IN ('positive','negative','neutral')),
    sentiment_score REAL,           -- confidence 0–1 for the predicted label
    positive_score  REAL,           -- raw FinBERT positive probability
    negative_score  REAL,           -- raw FinBERT negative probability
    neutral_score   REAL,           -- raw FinBERT neutral probability
    UNIQUE (symbol, url)
);

CREATE INDEX IF NOT EXISTS idx_news_symbol_time
    ON news_articles (symbol, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_unscored
    ON news_articles (symbol)
    WHERE sentiment IS NULL;
"""


class NewsStore:
    """
    Stores financial news articles and their FinBERT sentiment scores in SQLite.
    The default database (news_sentiment.db) lives next to this file.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

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
        with self._connect() as conn:
            for art in articles:
                url = (art.get("url") or "").strip()
                if not url:
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO news_articles
                        (symbol, title, summary, publisher, url,
                         published_at, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE news_articles
                SET sentiment       = ?,
                    sentiment_score = ?,
                    positive_score  = ?,
                    negative_score  = ?,
                    neutral_score   = ?
                WHERE article_id = ?
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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

        total     = len(rows)
        scored    = [r for r in rows if r["sentiment"] is not None]
        n_scored  = len(scored)

        pos = [r for r in scored if r["sentiment"] == "positive"]
        neg = [r for r in scored if r["sentiment"] == "negative"]
        neu = [r for r in scored if r["sentiment"] == "neutral"]

        avg_pos = round(sum(r["positive_score"] or 0 for r in scored) / n_scored, 3) if n_scored else None
        avg_neg = round(sum(r["negative_score"] or 0 for r in scored) / n_scored, 3) if n_scored else None

        # Signal logic
        if n_scored < 3:
            signal   = "INSUFFICIENT_DATA"
            strength = 0.0
        else:
            pos_pct = len(pos) / n_scored
            neg_pct = len(neg) / n_scored
            if pos_pct >= 0.60:
                signal   = "BULLISH"
                strength = round(pos_pct, 2)
            elif neg_pct >= 0.60:
                signal   = "BEARISH"
                strength = round(neg_pct, 2)
            elif pos_pct > neg_pct + 0.15:
                signal   = "MIXED"
                strength = round(pos_pct - neg_pct, 2)
            elif neg_pct > pos_pct + 0.15:
                signal   = "MIXED"
                strength = round(neg_pct - pos_pct, 2)
            else:
                signal   = "NEUTRAL"
                strength = round(1.0 - abs(pos_pct - neg_pct), 2)

        return {
            "symbol":              symbol.upper(),
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

    def get_sentiment_trend(self, symbol: str, days: int = 30) -> list[dict]:
        """
        Return per-day sentiment counts for trending analysis.

        Each row: date, positive_count, negative_count, neutral_count,
                  net_score (positive% - negative%), article_count
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
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
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM news_articles {clause}", params
            ).fetchone()
            return row[0]

    def get_symbols(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM news_articles ORDER BY symbol"
            ).fetchall()
            return [r[0] for r in rows]
