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
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).parent / "sentiment.sqlite"

_DDL = """
CREATE TABLE IF NOT EXISTS sentiment_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT    NOT NULL,
    captured_at      TEXT    NOT NULL,
    article_count    INTEGER NOT NULL DEFAULT 0,
    positive_count   INTEGER NOT NULL DEFAULT 0,
    negative_count   INTEGER NOT NULL DEFAULT 0,
    neutral_count    INTEGER NOT NULL DEFAULT 0,
    scored_count     INTEGER NOT NULL DEFAULT 0,
    overall_sentiment TEXT,
    articles_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_ts
    ON sentiment_snapshots (symbol, captured_at DESC);
"""


class SentimentStore:
    def __init__(self, db_path: Path | None = None):
        self._db = db_path or _DB_PATH
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_DDL)
            conn.commit()

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

        with closing(self._connect()) as conn:
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
        with closing(self._connect()) as conn:
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
        with closing(self._connect()) as conn:
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
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, symbol, captured_at, article_count,
                       positive_count, negative_count, neutral_count,
                       scored_count, overall_sentiment
                FROM sentiment_snapshots
                WHERE symbol = ?
                  AND captured_at >= datetime('now', ? || ' days')
                ORDER BY captured_at ASC
                """,
                (symbol.upper(), f"-{days}"),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_latest(self) -> dict[str, dict]:
        """
        Return the most recent snapshot for every symbol in the store.
        Result: {symbol: snapshot_dict}
        """
        with closing(self._connect()) as conn:
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
