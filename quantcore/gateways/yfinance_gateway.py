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
