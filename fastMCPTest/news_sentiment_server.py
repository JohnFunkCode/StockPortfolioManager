"""
news_sentiment_server.py — MCP server for financial news sentiment analysis.

Addresses GitHub issue #9: "Connect the RSS News Reader to SQLPlus, score the
items with Finbert, then surface it as an MCP server."

Tools exposed
-------------
  collect_news(symbol, score)
      Fetch RSS + yfinance news for a ticker, store in SQLite, and optionally
      score with FinBERT.  Returns a summary of new articles inserted.

  get_news_sentiment(symbol, days, scored_only)
      Return recent articles and an aggregate sentiment signal for a symbol.

  get_sentiment_trend(symbol, days)
      Return per-day sentiment breakdown for trending analysis.

  list_news_symbols()
      List every symbol that has stored articles.

Usage (standalone):
    fastmcp run news_sentiment_server.py

Requires:
    feedparser      — for RSS fetching   (pip install feedparser)
    transformers    — for FinBERT scoring (pip install transformers torch)
    torch           — required by transformers
    (all optional — server still works without them; sentiment will be NULL)
"""

import logging

from fastmcp import FastMCP

from news_collector import NewsCollector
from news_store import NewsStore

log = logging.getLogger(__name__)

mcp = FastMCP("news-sentiment-server")

# Shared instances — one DB connection pool per server process
_store     = NewsStore()
_collector = NewsCollector(store=_store)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def collect_news(symbol: str, score: bool = True) -> dict:
    """
    Fetch the latest financial news for *symbol* from Yahoo Finance RSS and
    yfinance, store articles in the local SQLite database, and (if *score* is
    True) run FinBERT sentiment scoring on any unscored articles.

    Parameters
    ----------
    symbol : Ticker symbol, e.g. "AAPL"
    score  : Whether to run FinBERT scoring after fetching (default True).
             Set to False for a faster fetch-only operation.

    Returns
    -------
    {
      symbol         : str,
      new_articles   : int,   -- articles newly inserted this run
      total_articles : int,   -- total articles for this symbol in DB
      finbert_available : bool
    }
    """
    sym = symbol.upper()

    try:
        import feedparser as _fp  # noqa
        rss_ok = True
    except ImportError:
        rss_ok = False

    totals = _collector.collect([sym], score=score)
    new_count = totals.get(sym, 0)

    try:
        from transformers import AutoTokenizer  # noqa
        finbert_ok = True
    except ImportError:
        finbert_ok = False

    return {
        "symbol":            sym,
        "new_articles":      new_count,
        "total_articles":    _store.article_count(sym),
        "rss_available":     rss_ok,
        "finbert_available": finbert_ok,
    }


@mcp.tool()
def get_news_sentiment(
    symbol: str,
    days: int = 7,
    scored_only: bool = False,
) -> dict:
    """
    Retrieve recent news articles and an aggregate sentiment signal for *symbol*.

    If no articles are stored, call collect_news(symbol) first.

    Parameters
    ----------
    symbol      : Ticker symbol, e.g. "AAPL"
    days        : How many calendar days back to look (default 7)
    scored_only : Return only FinBERT-scored articles (default False)

    Returns
    -------
    {
      symbol           : str,
      days             : int,
      signal           : "BULLISH" | "BEARISH" | "MIXED" | "NEUTRAL" | "INSUFFICIENT_DATA",
      signal_strength  : float,   -- 0.0–1.0
      total_articles   : int,
      scored_articles  : int,
      positive_count   : int,
      negative_count   : int,
      neutral_count    : int,
      avg_positive_score : float | null,
      avg_negative_score : float | null,
      top_positive     : [str],   -- up to 3 titles
      top_negative     : [str],   -- up to 3 titles
      articles         : [{ article_id, title, publisher, published_at, url,
                            sentiment, sentiment_score }]
    }
    """
    sym = symbol.upper()

    summary  = _store.get_sentiment_summary(sym, days=days)
    articles = _store.get_articles(sym, days=days, limit=50, scored_only=scored_only)

    # Trim article fields for a compact MCP response
    slim_articles = [
        {
            "article_id":      a["article_id"],
            "title":           a["title"],
            "publisher":       a["publisher"],
            "published_at":    a["published_at"],
            "url":             a["url"],
            "sentiment":       a["sentiment"],
            "sentiment_score": a["sentiment_score"],
        }
        for a in articles
    ]

    return {**summary, "articles": slim_articles}


@mcp.tool()
def get_sentiment_trend(symbol: str, days: int = 30) -> dict:
    """
    Return a per-day sentiment breakdown for *symbol* over the past *days* days.

    Useful for charting sentiment trends over time.

    Parameters
    ----------
    symbol : Ticker symbol, e.g. "AAPL"
    days   : How many calendar days back to look (default 30)

    Returns
    -------
    {
      symbol : str,
      days   : int,
      trend  : [
        {
          date           : "YYYY-MM-DD",
          article_count  : int,
          positive_count : int,
          negative_count : int,
          neutral_count  : int,
          net_score      : float   -- (positive-negative)/total; +1 all pos, -1 all neg
        }
      ]
    }
    """
    sym   = symbol.upper()
    trend = _store.get_sentiment_trend(sym, days=days)
    return {"symbol": sym, "days": days, "trend": trend}


@mcp.tool()
def list_news_symbols() -> dict:
    """
    List every ticker symbol that has at least one article stored in the
    news database.

    Returns
    -------
    {
      symbols       : [str],
      total_symbols : int
    }
    """
    syms = _store.get_symbols()
    return {"symbols": syms, "total_symbols": len(syms)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
