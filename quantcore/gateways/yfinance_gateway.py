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

import concurrent.futures
import datetime
import logging
import threading

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_YF_DOWNLOAD_LOCK = threading.Lock()

# Hard cap on a single fetch window (Yahoo practicality + payload sanity).
_MAX_FETCH_DAYS = 730

_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]
_FIELD_NAMES = {"Open", "High", "Low", "Close", "Volume", "Adj Close",
                "Dividends", "Stock Splits"}


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
        end = datetime.datetime.utcnow()
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
        """Close yfinance's per-thread peewee cache DB connections.

        yfinance opens one sqlite connection per thread (tkr-tz.db,
        cookies.db) that is never closed; long-running batch work leaks file
        descriptors without this. Safe no-op if yfinance internals change.
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
        indefinitely.  We run it in a thread and abandon it after `timeout` seconds.
        """
        ticker = yf.Ticker(symbol)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: ticker.info)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(
                    f"Yahoo Finance .info request timed out after {timeout}s for {symbol}. "
                    "The endpoint is temporarily slow or rate-limiting. Try again in a moment."
                )

    def fast_info(self, symbol: str):
        """Live quote snapshot (bid/ask/last price) — yfinance fast_info object."""
        return yf.Ticker(symbol).fast_info

    def expirations(self, symbol: str) -> tuple:
        """Available option expiration dates for a symbol."""
        return yf.Ticker(symbol).options

    def option_chain(self, symbol: str, expiration: str):
        """Option chain (calls/puts DataFrames) for one expiration."""
        return yf.Ticker(symbol).option_chain(expiration)

    def news(self, symbol: str) -> list:
        """Recent news items for a symbol (raw yfinance dicts)."""
        return yf.Ticker(symbol).news

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
