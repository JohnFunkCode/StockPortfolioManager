"""
news_collector.py — Fetches financial news from RSS feeds and yfinance, then
scores articles with FinBERT sentiment analysis.

Addresses GitHub issue #9: "Connect the RSS News Reader to SQLPlus, score the
items with Finbert, then surface it as an MCP server."

RSS feeds
---------
Yahoo Finance provides a per-ticker RSS feed:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}&region=US&lang=en-US

FinBERT is optional — if `transformers` and `torch` are not installed the
collector will still store articles but leave sentiment fields as NULL.

Usage:
    from news_collector import NewsCollector
    collector = NewsCollector()

    count = collector.collect(["AAPL", "MSFT"])   # fetch + store + score
    summary = collector.score_unscored()           # score previously fetched articles
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

from news_store import NewsStore

log = logging.getLogger(__name__)

# Yahoo Finance per-ticker RSS feed (returns up to ~20 items)
_YF_RSS_URL = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline"
    "?s={symbol}&region=US&lang=en-US"
)

# FinBERT model name on HuggingFace Hub
_FINBERT_MODEL = "ProsusAI/finbert"

# Lazy-loaded globals — only allocated on first FinBERT use
_finbert_tokenizer = None
_finbert_model = None
_finbert_available: Optional[bool] = None   # None = not yet probed


def _ensure_finbert() -> bool:
    """Load FinBERT on first call.  Returns True if available, False otherwise."""
    global _finbert_tokenizer, _finbert_model, _finbert_available
    if _finbert_available is not None:
        return _finbert_available
    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch  # noqa: F401 — confirm torch is present

        log.info("Loading FinBERT model '%s' (first-time download may take a moment)…", _FINBERT_MODEL)
        _finbert_tokenizer = AutoTokenizer.from_pretrained(_FINBERT_MODEL)
        _finbert_model = AutoModelForSequenceClassification.from_pretrained(_FINBERT_MODEL)
        _finbert_model.eval()
        _finbert_available = True
        log.info("FinBERT loaded successfully.")
    except ImportError:
        log.warning(
            "transformers / torch not installed — articles will be stored without sentiment. "
            "Run: pip install transformers torch"
        )
        _finbert_available = False
    except Exception as exc:
        log.warning("Could not load FinBERT (%s) — sentiment scoring disabled.", exc)
        _finbert_available = False
    return _finbert_available


def _score_text(text: str) -> Optional[dict]:
    """
    Score a single text with FinBERT.

    Returns a dict with keys:
        sentiment, sentiment_score, positive_score, negative_score, neutral_score
    or None if FinBERT is unavailable.

    FinBERT label order: positive=0, negative=1, neutral=2
    (from the ProsusAI/finbert config)
    """
    if not _ensure_finbert():
        return None

    import torch
    import torch.nn.functional as F

    # Truncate to 512 tokens (FinBERT limit)
    inputs = _finbert_tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=512,
        padding=True,
    )
    with torch.no_grad():
        logits = _finbert_model(**inputs).logits
    probs = F.softmax(logits, dim=1).squeeze().tolist()

    # ProsusAI/finbert label order: positive, negative, neutral
    pos_score, neg_score, neu_score = probs
    label_idx = int(torch.argmax(logits))
    labels = ["positive", "negative", "neutral"]
    sentiment = labels[label_idx]
    sentiment_score = probs[label_idx]

    return {
        "sentiment":       sentiment,
        "sentiment_score": sentiment_score,
        "positive_score":  pos_score,
        "negative_score":  neg_score,
        "neutral_score":   neu_score,
    }


def _text_for_scoring(article: dict) -> str:
    """Combine title + summary for a richer FinBERT input."""
    title   = article.get("title") or ""
    summary = article.get("summary") or ""
    if summary:
        return f"{title}. {summary}"
    return title


# ---------------------------------------------------------------------------
# RSS helpers
# ---------------------------------------------------------------------------

def _fetch_rss(symbol: str) -> list[dict]:
    """
    Fetch articles for *symbol* from the Yahoo Finance RSS feed.

    Returns a list of article dicts ready for NewsStore.save_articles().
    Requires `feedparser` to be installed; returns [] if not available.
    """
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser not installed — RSS fetch skipped.  Run: pip install feedparser")
        return []

    url = _YF_RSS_URL.format(symbol=symbol)
    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        log.warning("RSS parse error for %s: %s", symbol, exc)
        return []

    articles = []
    for entry in feed.entries:
        published_at = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                published_at = datetime(
                    *entry.published_parsed[:6], tzinfo=timezone.utc
                ).isoformat()
            except Exception:
                pass

        articles.append({
            "title":        getattr(entry, "title", "").strip(),
            "url":          getattr(entry, "link",  "").strip(),
            "summary":      getattr(entry, "summary", "").strip() or None,
            "publisher":    feed.feed.get("title", "Yahoo Finance RSS"),
            "published_at": published_at,
            "source":       "rss",
        })
    return articles


def _fetch_yfinance_news(symbol: str) -> list[dict]:
    """
    Fetch recent news items from yfinance as a supplementary source.
    Returns a list of article dicts ready for NewsStore.save_articles().
    """
    try:
        ticker = yf.Ticker(symbol)
        raw = ticker.news or []
    except Exception as exc:
        log.warning("yfinance news error for %s: %s", symbol, exc)
        return []

    articles = []
    for item in raw:
        # yfinance news dict keys vary by version
        url = item.get("link") or item.get("url") or ""
        if not url:
            continue

        published_at = None
        ts = item.get("providerPublishTime") or item.get("published")
        if ts:
            try:
                published_at = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except Exception:
                pass

        articles.append({
            "title":        (item.get("title") or "").strip(),
            "url":          url.strip(),
            "summary":      None,
            "publisher":    item.get("publisher") or "Yahoo Finance",
            "published_at": published_at,
            "source":       "yfinance",
        })
    return articles


# ---------------------------------------------------------------------------
# Public collector
# ---------------------------------------------------------------------------

class NewsCollector:
    """
    Fetches news from RSS + yfinance, persists via NewsStore, and scores with FinBERT.

    Parameters
    ----------
    store : NewsStore (optional) — uses default DB path if not provided
    """

    def __init__(self, store: Optional[NewsStore] = None) -> None:
        self.store = store or NewsStore()

    def collect(self, symbols: list[str], score: bool = True) -> dict[str, int]:
        """
        Fetch and store news for each symbol.

        Parameters
        ----------
        symbols : list of ticker symbols
        score   : if True, score newly inserted articles with FinBERT

        Returns
        -------
        dict mapping symbol → number of new articles inserted
        """
        totals: dict[str, int] = {}
        for sym in symbols:
            sym = sym.upper()
            rss_articles  = _fetch_rss(sym)
            yf_articles   = _fetch_yfinance_news(sym)
            all_articles  = rss_articles + yf_articles

            inserted = self.store.save_articles(sym, all_articles)
            totals[sym] = inserted
            log.info("%s: %d new article(s) stored (RSS=%d, yfinance=%d)",
                     sym, inserted, len(rss_articles), len(yf_articles))

        if score:
            self.score_unscored()

        return totals

    def score_unscored(self, limit: int = 200) -> int:
        """
        Score all articles in the DB that don't yet have sentiment.

        Returns the number of articles scored.
        """
        unscored = self.store.get_unscored_articles(limit=limit)
        if not unscored:
            return 0

        if not _ensure_finbert():
            log.info("FinBERT unavailable — skipping scoring of %d article(s).", len(unscored))
            return 0

        scored_count = 0
        for article in unscored:
            text = _text_for_scoring(article)
            if not text.strip():
                continue
            result = _score_text(text)
            if result is None:
                continue
            try:
                self.store.update_sentiment(
                    article_id     = article["article_id"],
                    sentiment      = result["sentiment"],
                    sentiment_score= result["sentiment_score"],
                    positive_score = result["positive_score"],
                    negative_score = result["negative_score"],
                    neutral_score  = result["neutral_score"],
                )
                scored_count += 1
            except Exception as exc:
                log.warning("Sentiment update failed for article %s: %s",
                            article["article_id"], exc)

        log.info("Scored %d/%d article(s) with FinBERT.", scored_count, len(unscored))
        return scored_count
