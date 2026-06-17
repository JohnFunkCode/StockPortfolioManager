"""News + sentiment routes (pass-through).

Ports the Flask handlers:
  GET /api/securities/news/sentiment-summary?source=all
  GET /api/securities/{ticker}/news?max_articles=10

These two have bespoke 500 error bodies (extra keys), not the shared helper:
  news             -> {"ticker", "error", "articles": []}
  sentiment-summary -> {"error", "items": []}

The literal sentiment-summary route is declared first so it is never shadowed
by the /{ticker}/news template.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import load_portfolio, load_watchlist, services
from ..json_response import QuantCoreJSONResponse

router = APIRouter(prefix="/api/securities", tags=["sentiment"])


@router.get("/news/sentiment-summary")
def get_sentiment_summary(source: str = "all") -> QuantCoreJSONResponse:
    try:
        result = services().sentiment.get_sentiment_dashboard(
            load_portfolio(), load_watchlist(), source_filter=source
        )
    except Exception as exc:  # noqa: BLE001 — parity with Flask
        return QuantCoreJSONResponse({"error": str(exc), "items": []}, status_code=500)
    return QuantCoreJSONResponse(result)


# Phase 3 Step 1 surface-gap endpoint — collection-level, declared before the
# /{ticker}/news templates so the literal "news/symbols" path is never shadowed.
@router.get("/news/symbols")
def list_news_symbols() -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().sentiment.list_news_symbols())


@router.get("/{ticker}/news")
def get_security_news(ticker: str, max_articles: int = 10) -> QuantCoreJSONResponse:
    ticker = ticker.upper()
    try:
        result = services().sentiment.get_security_news(ticker, max_articles=max_articles)
    except Exception as exc:  # noqa: BLE001
        return QuantCoreJSONResponse(
            {"ticker": ticker, "error": str(exc), "articles": []}, status_code=500
        )
    return QuantCoreJSONResponse(result)


# --------------------------------------------------------------------------- #
# Phase 3 Step 1 surface-gap endpoints (previously MCP-only) — collect/score,
# windowed sentiment signal, and per-day trend. One SentimentService call deep.
# --------------------------------------------------------------------------- #
@router.post("/{ticker}/news/collect")
def collect_news(ticker: str, score: bool = True) -> QuantCoreJSONResponse:
    """Fetch RSS + yfinance news, persist, and (if score) FinBERT-score new articles."""
    return QuantCoreJSONResponse(services().sentiment.collect_news(ticker, score))


@router.get("/{ticker}/news/sentiment")
def get_news_sentiment(
    ticker: str, days: int = 7, scored_only: bool = False
) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(
        services().sentiment.get_news_sentiment(ticker, days, scored_only)
    )


@router.get("/{ticker}/news/trend")
def get_sentiment_trend(ticker: str, days: int = 30) -> QuantCoreJSONResponse:
    return QuantCoreJSONResponse(services().sentiment.get_sentiment_trend(ticker, days))
