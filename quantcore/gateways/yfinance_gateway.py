"""YFinanceGateway — all yfinance network access for the services layer.

Architectural standard v2 §5.1: services never import yfinance directly; they
receive this gateway via constructor injection. Methods are added as services
migrate (Phase 1 Steps 1-8). The legacy portfolio/yfinance_gateway.py used by
main.py's report path is separate and consolidates here in Phase 2.

This module is the ONLY yf.download call site in the codebase (issue #74/#75):
yf.download passes results through module-global state and is not thread-safe
— concurrent calls can hand one ticker's bars to another (the July 2026 OHLCV
corruption). Every download is serialized on _YF_DOWNLOAD_LOCK, enforced by
test_architecture_guards.py.
"""

import datetime
import logging
import threading
import time

import math
from dataclasses import dataclass

import pandas as pd
import yfinance as yf
# A yfinance internal (#518): the only seam to Yahoo's batch quote endpoint.
# Imported at module level on purpose, so a release that moves it fails the
# import (and CI's lean-import job) on the dependency-upgrade PR, not in prod.
from yfinance.data import YfData

from quantcore.analytics.market_time import ET

logger = logging.getLogger(__name__)

_YF_DOWNLOAD_LOCK = threading.Lock()

# Hard cap on a single fetch window (Yahoo practicality + payload sanity).
_MAX_FETCH_DAYS = 730

_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]
_FIELD_NAMES = {"Open", "High", "Low", "Close", "Volume", "Adj Close",
                "Dividends", "Stock Splits"}


# Backoff before each retry of a rate-limited options call (#373). Two retries,
# ten seconds at most per call: enough to ride out a short 429 burst, short
# enough that the daily capture's per-symbol budget check still bounds the run.
_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 6.0)


# Yahoo's batch quote endpoint (#518) and how it is called.
_V7_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
BATCH_QUOTE_CHUNK = 100
BATCH_QUOTE_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class Quote:
    """One symbol's v7 quote. Numbers are None when Yahoo omits them or sends
    a non-finite value; times are epoch seconds."""

    regular_open: float | None
    regular_high: float | None
    regular_low: float | None
    regular_price: float | None
    regular_volume: int | None
    regular_time: int | None
    previous_close: float | None
    regular_change_pct: float | None
    pre_price: float | None
    pre_change_pct: float | None
    pre_time: int | None
    post_price: float | None
    post_change_pct: float | None
    post_time: int | None
    market_state: str | None
    currency: str | None
    exchange_tz: str | None


def _num(raw: dict, key: str) -> float | None:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _int(raw: dict, key: str) -> int | None:
    value = _num(raw, key)
    return int(value) if value is not None else None


def _text(raw: dict, key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and value else None


def _parse_quote(raw: dict) -> Quote:
    return Quote(
        regular_open=_num(raw, "regularMarketOpen"),
        regular_high=_num(raw, "regularMarketDayHigh"),
        regular_low=_num(raw, "regularMarketDayLow"),
        regular_price=_num(raw, "regularMarketPrice"),
        regular_volume=_int(raw, "regularMarketVolume"),
        regular_time=_int(raw, "regularMarketTime"),
        previous_close=_num(raw, "regularMarketPreviousClose"),
        regular_change_pct=_num(raw, "regularMarketChangePercent"),
        pre_price=_num(raw, "preMarketPrice"),
        pre_change_pct=_num(raw, "preMarketChangePercent"),
        pre_time=_int(raw, "preMarketTime"),
        post_price=_num(raw, "postMarketPrice"),
        post_change_pct=_num(raw, "postMarketChangePercent"),
        post_time=_int(raw, "postMarketTime"),
        market_state=_text(raw, "marketState"),
        currency=_text(raw, "currency"),
        exchange_tz=_text(raw, "exchangeTimezoneName"),
    )


def _quote_results(payload) -> list[dict]:
    """The result list of a v7 response, or [] for anything malformed."""
    if not isinstance(payload, dict):
        return []
    body = payload.get("quoteResponse")
    results = body.get("result") if isinstance(body, dict) else None
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _fetch_quote_chunk(chunk: list[str], box: dict, timeout: float) -> None:
    """Thread target: one v7 request, its payload or its exception into ``box``."""
    try:
        box["value"] = YfData().get_raw_json(
            _V7_QUOTE_URL,
            params={"symbols": ",".join(chunk), "formatted": "false"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — logged on the caller's thread
        box["error"] = exc


def _start_quote_workers(
    chunks: list[list[str]], timeout: float,
) -> list[tuple[list[str], dict, threading.Thread]]:
    """Start one daemon thread per chunk; returns (chunk, result box, thread)."""
    jobs = []
    for n, chunk in enumerate(chunks):
        box: dict = {}
        worker = threading.Thread(
            target=_fetch_quote_chunk, args=(chunk, box, timeout),
            daemon=True, name=f"yf-quote-{n}",
        )
        worker.start()
        jobs.append((chunk, box, worker))
    return jobs


def _collect_quote_chunks(jobs, quotes: dict, timeout: float) -> None:
    """Join the workers against one shared deadline and fold each finished
    chunk's quotes into ``quotes``. A chunk that failed or is still running is
    logged and left as None."""
    deadline = time.monotonic() + timeout
    for _, _, worker in jobs:
        worker.join(max(0.0, deadline - time.monotonic()))

    for chunk, box, worker in jobs:
        if worker.is_alive():
            logger.warning("batch_quotes: chunk of %d symbols timed out after %ss",
                           len(chunk), timeout)
        elif "error" in box:
            # Class name only: the message can carry the request URL.
            logger.warning("batch_quotes: chunk of %d symbols failed (%s)",
                           len(chunk), type(box["error"]).__name__)
        else:
            for raw in _quote_results(box.get("value")):
                symbol = str(raw.get("symbol") or "").upper()
                if symbol in quotes:
                    quotes[symbol] = _parse_quote(raw)


def is_rate_limited(exc: BaseException) -> bool:
    """True when ``exc`` is Yahoo saying "slow down" (HTTP 429).

    yfinance raises ``YFRateLimitError`` for most of these, but some paths
    surface the raw HTTP error instead, so the message is checked as well.
    """
    rate_limit_error = getattr(getattr(yf, "exceptions", None), "YFRateLimitError", None)
    if rate_limit_error is not None and isinstance(exc, rate_limit_error):
        return True
    text = str(exc)
    return "429" in text or "Too Many Requests" in text


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """yf.download returns MultiIndex columns in newer versions; flatten to
    field names whichever level holds them."""
    if isinstance(df.columns, pd.MultiIndex):
        if df.columns.get_level_values(0)[0] in _FIELD_NAMES:
            df.columns = df.columns.get_level_values(0)
        else:
            df.columns = df.columns.get_level_values(1)
    return df


class YFinanceGateway:
    def __init__(self, sleep=time.sleep):
        # ``sleep`` is injectable so the retry tests do not wait out the backoff.
        self._sleep = sleep
        self._rate_limit_lock = threading.Lock()
        self._rate_limited = 0

    @property
    def rate_limited(self) -> int:
        """How many options calls Yahoo has answered with a 429 so far.

        Counts every 429, retried or not, over this gateway's lifetime. The
        daily job reads it before and after each symbol to tell a rate-limited
        failure from any other kind (#373).
        """
        with self._rate_limit_lock:
            return self._rate_limited

    def _with_rate_limit_retry(self, call, what: str):
        """Run ``call``, retrying only on a 429, with a bounded backoff."""
        for attempt in range(len(_RATE_LIMIT_BACKOFF_SECONDS) + 1):
            try:
                return call()
            except Exception as exc:
                if not is_rate_limited(exc):
                    raise
                with self._rate_limit_lock:
                    self._rate_limited += 1
                if attempt == len(_RATE_LIMIT_BACKOFF_SECONDS):
                    raise
                delay = _RATE_LIMIT_BACKOFF_SECONDS[attempt]
                logger.warning("Yahoo rate-limited %s; retrying in %.0fs", what, delay)
                self._sleep(delay)

    def fetch_history(
        self,
        symbol: str,
        interval: str,
        days: int,
        auto_adjust: bool = True,
        include_adj_close: bool = False,
    ) -> pd.DataFrame:
        """Single-symbol OHLCV download — the canonical history fetch seam.

        Returns a DataFrame with Open/High/Low/Close/Volume (plus Adj Close
        when requested and available), NaN-Close rows dropped; empty standard
        frame when Yahoo returns nothing. Window capped at _MAX_FETCH_DAYS.
        """
        fetch_days = min(days, _MAX_FETCH_DAYS)
        # yf.download's `end` bound is EXCLUSIVE, so it must sit one day past the
        # session we want; passing today silently drops today's bar and the cache
        # can then only ever serve the previous close. Anchor to the ET market
        # date, not UTC — utcnow() rolls over at 20:00 ET and would otherwise
        # shift the window mid-evening.
        today_et = datetime.datetime.now(tz=ET).date()
        end = today_et + datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=fetch_days)
        with _YF_DOWNLOAD_LOCK:
            df = yf.download(
                symbol,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=auto_adjust,
                progress=False,
            )
        if df is None or df.empty:
            logger.warning("yfinance returned no data for %s/%s", symbol, interval)
            return pd.DataFrame(columns=_OHLCV_COLS)
        df = _flatten_columns(df)
        if not set(_OHLCV_COLS).issubset(set(df.columns)):
            logger.warning(
                "yfinance columns unexpected for %s/%s: %s",
                symbol, interval, list(df.columns),
            )
            return pd.DataFrame(columns=_OHLCV_COLS)
        cols = list(_OHLCV_COLS)
        if include_adj_close and "Adj Close" in df.columns:
            cols.append("Adj Close")
        return df[cols].dropna(subset=["Close"])

    def close_thread_caches(self) -> None:
        """Close the *calling thread's* yfinance peewee cache DB connections.

        yfinance opens one sqlite connection per thread (tkr-tz.db,
        cookies.db) that is never closed; long-running batch work leaks file
        descriptors without this. peewee keeps that state thread-local, so
        this closes only the caller's connection: pool work must call it on
        each worker (e.g. in a per-task ``finally``), not once afterwards.
        Safe no-op if yfinance internals change.
        """
        try:
            from yfinance.cache import _CookieDBManager, _TzDBManager

            _TzDBManager.close_db()
            _CookieDBManager.close_db()
        except Exception:  # noqa: BLE001 — provider internals, best-effort
            pass

    def ticker_info(self, symbol: str, timeout: float = 15.0) -> dict:
        """Fetch ticker.info with a hard timeout to prevent callers from hanging.

        yfinance's ticker.info hits a slow Yahoo Finance endpoint that can block
        indefinitely. We run it in a thread and abandon it after `timeout`.

        Abandoning has to be literal, which is why this is a bare daemon thread
        and not a ThreadPoolExecutor. Under `with ThreadPoolExecutor(...)`,
        `__exit__` calls `shutdown(wait=True)`, so raising TimeoutError inside
        the block blocks on the way out until the hung worker returns anyway —
        the timeout chose when the exception was *built*, not when the caller
        got control back. Measured: a 1s timeout against a 6s hang took 6.01s.
        `Thread.join(timeout)` has no such back door, and a daemon thread also
        can't wedge interpreter exit the way a pool worker can (the executor's
        atexit hook joins its threads).

        The cost is an orphaned thread per timeout, alive until Yahoo answers.
        That is the right trade: it is bounded by the request rate, it holds no
        lock, and the alternative is holding a user's HTTP request instead.
        """
        ticker = yf.Ticker(symbol)
        box: dict = {}

        def _fetch() -> None:
            try:
                box["value"] = ticker.info
            except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
                box["error"] = exc

        worker = threading.Thread(
            target=_fetch, daemon=True, name=f"yf-info-{symbol}"
        )
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            raise TimeoutError(
                f"Yahoo Finance .info request timed out after {timeout}s for {symbol}. "
                "The endpoint is temporarily slow or rate-limiting. Try again in a moment."
            )
        if "error" in box:
            raise box["error"]
        return box.get("value") or {}

    def fast_info(self, symbol: str):
        """Live quote snapshot (bid/ask/last price) — yfinance fast_info object."""
        return yf.Ticker(symbol).fast_info

    def expirations(self, symbol: str) -> tuple:
        """Available option expiration dates for a symbol (retried on a 429)."""
        return self._with_rate_limit_retry(
            lambda: yf.Ticker(symbol).options, f"{symbol} expirations")

    def option_chain(self, symbol: str, expiration: str):
        """Option chain (calls/puts DataFrames) for one expiration (retried on a 429)."""
        return self._with_rate_limit_retry(
            lambda: yf.Ticker(symbol).option_chain(expiration),
            f"{symbol} {expiration} chain")

    def news(self, symbol: str, count: int = 10) -> list:
        """Recent news items for a symbol, as flat ``yf.Search`` dicts.

        ``Ticker.news`` (and Yahoo's RSS feed) return nothing any more, with no
        error, so this reads ``yf.Search(...).news`` instead (issue #275). Search
        is a text match, so it also returns stories about other companies; items
        whose ``relatedTickers`` do not include the symbol are dropped. An item
        with no ``relatedTickers`` at all is kept — absence is not a mismatch.
        """
        wanted = symbol.upper()
        items = yf.Search(symbol, news_count=count).news or []
        kept = []
        for item in items:
            related = item.get("relatedTickers")
            if related and wanted not in {str(t).upper() for t in related}:
                continue
            kept.append(item)
        return kept

    def info(self, symbol: str) -> dict:
        """Raw ticker.info without the watchdog timeout.

        Fundamentals scoring tolerates a slow/failed fetch (it scores "no data"
        metrics as 0), so it keeps yfinance's native blocking behavior rather
        than ticker_info()'s 15s TimeoutError contract.
        """
        return yf.Ticker(symbol).info

    def financials(self, symbol: str):
        """Annual income statement DataFrame (rows = line items)."""
        return yf.Ticker(symbol).financials

    def cashflow(self, symbol: str):
        """Annual cash-flow statement DataFrame."""
        return yf.Ticker(symbol).cashflow

    def quarterly_financials(self, symbol: str):
        """Quarterly income statement DataFrame."""
        return yf.Ticker(symbol).quarterly_financials

    def quarterly_income_stmt(self, symbol: str):
        """Quarterly income statement DataFrame (income_stmt variant)."""
        return yf.Ticker(symbol).quarterly_income_stmt

    def calendar(self, symbol: str):
        """Upcoming events calendar (dict or DataFrame depending on yfinance version)."""
        return yf.Ticker(symbol).calendar

    def earnings_dates(self, symbol: str):
        """Past + scheduled earnings dates DataFrame (tz-aware index)."""
        return yf.Ticker(symbol).earnings_dates

    def history(self, symbol: str, period: str = "1y", auto_adjust: bool = True):
        """Per-ticker OHLCV history DataFrame via ticker.history()."""
        return yf.Ticker(symbol).history(period=period, auto_adjust=auto_adjust)

    def download(self, tickers, period: str = "1y", auto_adjust: bool = True):
        """Bulk multi-ticker OHLCV download via yf.download() (progress suppressed).

        Used by relative-strength scoring, which fetches a symbol and its
        benchmarks (SPY/QQQ/sector ETF) in a single call. Serialized on the
        same lock as fetch_history — yf.download is never thread-safe.
        """
        with _YF_DOWNLOAD_LOCK:
            return yf.download(
                tickers, period=period, auto_adjust=auto_adjust, progress=False
            )

    def batch_quotes(
        self, symbols: list[str], timeout: float = BATCH_QUOTE_TIMEOUT_SECONDS,
    ) -> dict[str, "Quote | None"]:
        """Quotes for many symbols from Yahoo's v7 batch quote endpoint (#518).

        ``yf.download`` looks batched but sends one chart request per ticker;
        v7/finance/quote answers up to ~100 symbols per request (260 symbols:
        3 requests, ~1.3 s). yfinance has no public wrapper for it, so this goes
        through ``YfData.get_raw_json``, which handles the cookie and crumb.

        Chunks of BATCH_QUOTE_CHUNK run concurrently on daemon threads (the
        ``ticker_info`` pattern: a hung request is abandoned, not waited for)
        and share one deadline, so the caller waits at most ``timeout``. A
        chunk that fails or misses the deadline maps its symbols to None and
        the others still return. Yahoo silently omits unknown symbols; those
        are None too. Never raises.
        """
        wanted = list(dict.fromkeys(s.upper() for s in symbols if s))
        quotes: dict[str, Quote | None] = {s: None for s in wanted}
        if not wanted:
            return quotes

        chunks = [wanted[i:i + BATCH_QUOTE_CHUNK]
                  for i in range(0, len(wanted), BATCH_QUOTE_CHUNK)]
        jobs = _start_quote_workers(chunks, timeout)
        _collect_quote_chunks(jobs, quotes, timeout)

        missing = [s for s, q in quotes.items() if q is None]
        if missing:
            logger.info("batch_quotes: no quote for %d of %d symbols",
                        len(missing), len(wanted))
        return quotes
