"""
news_sentiment_server.py — MCP server for financial news sentiment analysis.

Addresses GitHub issue #9: "Connect the RSS News Reader to SQLPlus, score the
items with Finbert, then surface it as an MCP server."

Tools exposed
-------------
  collect_news(symbol, score)
      Fetch RSS + yfinance news for a ticker, store in the database, and
      optionally score with FinBERT.  Returns a summary of new articles inserted.

  get_news_sentiment(symbol, days, scored_only)
      Return recent articles and an aggregate sentiment signal for a symbol.

  get_sentiment_trend(symbol, days)
      Return per-day sentiment breakdown for trending analysis.

  list_news_symbols()
      List every symbol that has stored articles.

Usage (standalone):
    fastmcp run news_sentiment_server.py

Thin adapter (architectural standard v2): all business logic lives in
quantcore.services.sentiment.SentimentService; each tool here is exactly one
service call deep.
"""

import sys
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MCP_DIR.parent
for path in (PROJECT_ROOT, MCP_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from fastmcp import FastMCP

from quantcore.services.registry import get_services

mcp = FastMCP("news-sentiment-server")


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
    return get_services().sentiment.collect_news(symbol, score)


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
    return get_services().sentiment.get_news_sentiment(symbol, days, scored_only)


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
    return get_services().sentiment.get_sentiment_trend(symbol, days)


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
    return get_services().sentiment.list_news_symbols()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from quantcore.db import init_schema
    init_schema()
    mcp.run()
