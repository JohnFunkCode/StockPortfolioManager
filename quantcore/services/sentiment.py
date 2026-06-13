"""SentimentService — financial news collection + FinBERT sentiment analysis.

Phase 1 Step 2: absorbs fastMCPTest/news_collector.py wholesale (NewsCollector
moved here verbatim), the four news_sentiment_server MCP tool bodies,
stock_price_server.get_news, and the two REST news route bodies from
api/app.py. This module owns the single FinBERT loader — the duplicate
transformers pipeline that lived in stock_price_server.py is deleted.

FinBERT is optional — if `transformers` and `torch` are not installed the
collector still stores articles but leaves sentiment fields as NULL, and
get_news() returns a sentiment_note instead of scores.

RSS feeds: Yahoo Finance provides a per-ticker RSS feed:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}&region=US&lang=en-US
(RSS fetch uses urllib directly here; extracting an RSS gateway is Phase 2.)
"""

from __future__ import annotations

import logging
import ssl
from datetime import datetime, timezone
from typing import Optional
from urllib.request import urlopen

from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.news_repository import NewsStore
from quantcore.repositories.sentiment_repository import SentimentStore

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


def _score_sentiment(text: str) -> dict | None:
    """Score a text snippet with FinBERT for the compact get_news() response.

    Returns {"sentiment": "positive"|"negative"|"neutral", "sentiment_score": float}
    or None if the model is unavailable.
    """
    if not text.strip():
        return None
    try:
        result = _score_text(text[:1000])
        if result is None:
            return None
        return {
            "sentiment": result["sentiment"],
            "sentiment_score": round(float(result["sentiment_score"]), 4),
        }
    except Exception:
        return None


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
        # Create SSL context that accepts self-signed or expired certificates
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Fetch URL with permissive SSL context, then parse the content
        with urlopen(url, context=ctx, timeout=10) as response:
            content = response.read()
        feed = feedparser.parse(content)
    except Exception as exc:
        # RSS feed may be unavailable or Yahoo Finance changed the endpoint
        log.debug("RSS feed unavailable for %s (%s) — falling back to yfinance only", symbol, type(exc).__name__)
        return []

    # If feed.bozo is set, log the error but try to parse what we got
    if feed.get("bozo"):
        log.debug("RSS feed bozo flag set for %s: %s", symbol, feed.bozo_exception)

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


def _fetch_yfinance_news(symbol: str, gateway: YFinanceGateway) -> list[dict]:
    """
    Fetch recent news items from yfinance as a supplementary source.
    Returns a list of article dicts ready for NewsStore.save_articles().
    """
    try:
        raw = gateway.news(symbol) or []
    except Exception as exc:
        log.warning("yfinance news error for %s: %s", symbol, exc)
        return []

    articles = []
    for item in raw:
        # yfinance API structure: top level has 'id', content nested under 'content' key
        content = item.get("content") or {}

        # Extract URL from clickThroughUrl or fall back to older structure
        url = ""
        click_url = content.get("clickThroughUrl") or {}
        if isinstance(click_url, dict):
            url = click_url.get("url") or ""
        if not url:
            # Fallback for older yfinance versions
            url = item.get("link") or item.get("url") or ""
        if not url:
            continue

        # Extract published timestamp
        published_at = None
        pub_date = content.get("pubDate")
        if pub_date:
            try:
                # Handle ISO format or Unix timestamp
                if isinstance(pub_date, str):
                    published_at = pub_date
                else:
                    published_at = datetime.fromtimestamp(int(pub_date), tz=timezone.utc).isoformat()
            except Exception:
                pass

        if not published_at:
            ts = item.get("providerPublishTime") or item.get("published")
            if ts:
                try:
                    published_at = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
                except Exception:
                    pass

        # Extract provider
        provider = "Yahoo Finance"
        provider_obj = content.get("provider")
        if isinstance(provider_obj, dict):
            provider = provider_obj.get("displayName") or provider

        articles.append({
            "title":        (content.get("title") or item.get("title") or "").strip(),
            "url":          url.strip(),
            "summary":      content.get("summary") or None,
            "publisher":    provider,
            "published_at": published_at,
            "source":       "yfinance",
        })
    return articles


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class NewsCollector:
    """
    Fetches news from RSS + yfinance, persists via NewsStore, and scores with FinBERT.

    Parameters
    ----------
    store   : NewsStore (optional) — uses default DB if not provided
    gateway : YFinanceGateway (optional) — yfinance access for the news feed
    """

    def __init__(self, store: Optional[NewsStore] = None,
                 gateway: Optional[YFinanceGateway] = None) -> None:
        self.store = store or NewsStore()
        self.gateway = gateway or YFinanceGateway()

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
            yf_articles   = _fetch_yfinance_news(sym, self.gateway)
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


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SentimentService:
    def __init__(self, news_repository: NewsStore,
                 sentiment_repository: SentimentStore,
                 yfinance_gateway: YFinanceGateway) -> None:
        self._news = news_repository
        self._sentiment = sentiment_repository
        self._yf = yfinance_gateway
        self._collector = NewsCollector(store=news_repository, gateway=yfinance_gateway)

    # -- news_sentiment_server tools ------------------------------------

    def collect_news(self, symbol: str, score: bool = True) -> dict:
        """Fetch RSS + yfinance news for a ticker, store, optionally FinBERT-score."""
        sym = symbol.upper()

        try:
            import feedparser as _fp  # noqa
            rss_ok = True
        except ImportError:
            rss_ok = False

        totals = self._collector.collect([sym], score=score)
        new_count = totals.get(sym, 0)

        try:
            from transformers import AutoTokenizer  # noqa
            finbert_ok = True
        except ImportError:
            finbert_ok = False

        return {
            "symbol":            sym,
            "new_articles":      new_count,
            "total_articles":    self._news.article_count(sym),
            "rss_available":     rss_ok,
            "finbert_available": finbert_ok,
        }

    def get_news_sentiment(self, symbol: str, days: int = 7,
                           scored_only: bool = False) -> dict:
        """Recent articles + aggregate sentiment signal for a symbol."""
        sym = symbol.upper()

        summary  = self._news.get_sentiment_summary(sym, days=days)
        articles = self._news.get_articles(sym, days=days, limit=50, scored_only=scored_only)

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

    def get_sentiment_trend(self, symbol: str, days: int = 30) -> dict:
        """Per-day sentiment breakdown for a symbol over the past N days."""
        sym   = symbol.upper()
        trend = self._news.get_sentiment_trend(sym, days=days)
        return {"symbol": sym, "days": days, "trend": trend}

    def list_news_symbols(self) -> dict:
        """Every symbol that has at least one stored article."""
        syms = self._news.get_symbols()
        return {"symbols": syms, "total_symbols": len(syms)}

    # -- stock_price_server.get_news ------------------------------------

    def get_news(self, symbol: str, max_articles: int = 10) -> dict:
        """Live yfinance news for a ticker, each article FinBERT-scored."""
        raw = self._yf.news(symbol.upper()) or []

        finbert_available = _ensure_finbert()

        articles = []
        for item in raw[:max_articles]:
            content = item.get("content", {})
            title   = content.get("title", "")
            summary = content.get("summary", "")

            article = {
                "title":     title,
                "publisher": content.get("provider", {}).get("displayName", ""),
                "published": content.get("pubDate", ""),
                "summary":   summary,
                "url":       content.get("canonicalUrl", {}).get("url", ""),
            }

            if finbert_available:
                scored = _score_sentiment(f"{title}. {summary}".strip())
                if scored:
                    article["sentiment"]       = scored["sentiment"]
                    article["sentiment_score"] = scored["sentiment_score"]

            articles.append(article)

        result: dict = {
            "symbol":        symbol.upper(),
            "article_count": len(articles),
            "articles":      articles,
        }

        if finbert_available:
            scored_articles = [a for a in articles if "sentiment" in a]
            pos = sum(1 for a in scored_articles if a["sentiment"] == "positive")
            neg = sum(1 for a in scored_articles if a["sentiment"] == "negative")
            neu = sum(1 for a in scored_articles if a["sentiment"] == "neutral")
            counts = {"positive": pos, "negative": neg, "neutral": neu}
            overall = max(counts, key=lambda k: counts[k]) if scored_articles else "neutral"
            result["sentiment_summary"] = {
                "overall":        overall,
                "positive_count": pos,
                "negative_count": neg,
                "neutral_count":  neu,
                "scored_count":   len(scored_articles),
            }
        else:
            result["sentiment_note"] = (
                "FinBERT sentiment scoring unavailable — install transformers and torch: "
                "pip install transformers torch"
            )

        return result

    # -- REST route bodies ----------------------------------------------

    def get_security_news(self, symbol: str, max_articles: int = 10) -> dict:
        """get_news() + persist the aggregate sentiment snapshot for trend tracking."""
        result = self.get_news(symbol.upper(), max_articles=max_articles)

        # Persist aggregate sentiment for trend/flip tracking
        try:
            self._sentiment.save_snapshot(symbol.upper(), result)
        except Exception:
            pass  # non-fatal — never block the news response

        return result

    def get_sentiment_dashboard(self, portfolio: list[dict], watchlist: list[dict],
                                source_filter: str = "all") -> dict:
        """Bulk sentiment dashboard: latest snapshot per scored symbol, merged
        with security metadata and ranked by overall_sentiment then negative_count.

        portfolio/watchlist are security dicts (symbol, name, source, tags) —
        supplied by the caller until PortfolioService exists (Step 6)."""
        portfolio_map = {s["symbol"]: s for s in portfolio}
        watchlist_map = {s["symbol"]: s for s in watchlist}
        combined: dict[str, dict] = {}
        for sym, s in portfolio_map.items():
            combined[sym] = s
        for sym, s in watchlist_map.items():
            if sym in combined:
                combined[sym]["source"] = "both"
                combined[sym]["tags"] = s.get("tags", [])
            else:
                combined[sym] = s

        latest = self._sentiment.get_all_latest()

        _ORDER = {"negative": 0, "neutral": 1, "positive": 2}

        items = []
        for sym, snap in latest.items():
            sec = combined.get(sym, {})
            src = sec.get("source", "watchlist")
            if source_filter == "portfolio" and src not in ("portfolio", "both"):
                continue
            if source_filter == "watchlist" and src not in ("watchlist", "both"):
                continue

            items.append({
                "symbol":            sym,
                "name":              sec.get("name", sym),
                "source":            src,
                "tags":              sec.get("tags", []),
                "captured_at":       snap["captured_at"],
                "overall_sentiment": snap["overall_sentiment"],
                "positive_count":    snap["positive_count"],
                "negative_count":    snap["negative_count"],
                "neutral_count":     snap["neutral_count"],
                "scored_count":      snap["scored_count"],
                "article_count":     snap["article_count"],
            })

        items.sort(key=lambda x: (
            _ORDER.get(x["overall_sentiment"] or "neutral", 1),
            -(x["negative_count"] or 0),
        ))

        return {"items": items, "count": len(items)}
