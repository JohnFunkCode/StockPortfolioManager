"""YFinanceGateway — all yfinance network access for the services layer.

Architectural standard v2 §5.1: services never import yfinance directly; they
receive this gateway via constructor injection. Methods are added as services
migrate (Phase 1 Steps 1-8). The legacy portfolio/yfinance_gateway.py used by
main.py's report path is separate and consolidates here in Phase 2.
"""

import concurrent.futures

import yfinance as yf


class YFinanceGateway:
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
