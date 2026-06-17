"""
SentimentStore — persist FinBERT news sentiment snapshots to SQLite.

Each time /api/securities/<ticker>/news is called, the aggregate sentiment
is saved here so we can:
  - Track sentiment trend over time (sparkline, flip detection)
  - Power the bulk watchlist sentiment dashboard
  - Feed the screener's news_sentiment filter
  - Trigger Discord alerts when sentiment flips positive ↔ negative
"""

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone

from quantcore.db import get_connection


class SentimentStore:
    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_snapshot(self, symbol: str, news_response: dict) -> None:
        """Persist the FinBERT aggregate from a get_news() response dict."""
        summary = news_response.get("sentiment_summary")
        if summary is None:
            return  # FinBERT not available — nothing to store

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        articles_json = json.dumps(news_response.get("articles", []))

        with closing(get_connection()) as conn:
            conn.execute(
                """
                INSERT INTO sentiment_snapshots
                    (symbol, captured_at, article_count, positive_count,
                     negative_count, neutral_count, scored_count,
                     overall_sentiment, articles_json)
                VALUES
                    (:symbol, :captured_at, :article_count, :positive_count,
                     :negative_count, :neutral_count, :scored_count,
                     :overall_sentiment, :articles_json)
                """,
                {
                    "symbol":            symbol.upper(),
                    "captured_at":       now,
                    "article_count":     news_response.get("article_count", 0),
                    "positive_count":    summary.get("positive_count", 0),
                    "negative_count":    summary.get("negative_count", 0),
                    "neutral_count":     summary.get("neutral_count", 0),
                    "scored_count":      summary.get("scored_count", 0),
                    "overall_sentiment": summary.get("overall"),
                    "articles_json":     articles_json,
                },
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_latest(self, symbol: str) -> dict | None:
        """Return the most recent snapshot for a symbol, or None."""
        with closing(get_connection()) as conn:
            row = conn.execute(
                """
                SELECT * FROM sentiment_snapshots
                WHERE symbol = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
        return dict(row) if row else None

    def get_prior(self, symbol: str) -> dict | None:
        """Return the second-most-recent snapshot (for flip detection)."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM sentiment_snapshots
                WHERE symbol = ?
                ORDER BY captured_at DESC
                LIMIT 2
                """,
                (symbol.upper(),),
            ).fetchall()
        return dict(rows[1]) if len(rows) >= 2 else None

    def get_history(self, symbol: str, days: int = 30) -> list[dict]:
        """Return up to `days` days of snapshots for a symbol, oldest first."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT id, symbol, captured_at, article_count,
                       positive_count, negative_count, neutral_count,
                       scored_count, overall_sentiment
                FROM sentiment_snapshots
                WHERE symbol = %s
                  AND captured_at >= %s
                ORDER BY captured_at ASC
                """,
                (symbol.upper(), since),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_latest(self) -> dict[str, dict]:
        """
        Return the most recent snapshot for every symbol in the store.
        Result: {symbol: snapshot_dict}
        """
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT s.*
                FROM sentiment_snapshots s
                INNER JOIN (
                    SELECT symbol, MAX(captured_at) AS max_ts
                    FROM sentiment_snapshots
                    GROUP BY symbol
                ) latest ON s.symbol = latest.symbol
                          AND s.captured_at = latest.max_ts
                ORDER BY s.symbol
                """
            ).fetchall()
        return {dict(r)["symbol"]: dict(r) for r in rows}
