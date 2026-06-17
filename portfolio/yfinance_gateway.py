import time

import yfinance as yf
import pandas as pd

from typing import Optional

from portfolio.money import Money

# How many times to retry a flaky/empty yfinance response before degrading
# gracefully to all-None, and the base back-off (seconds) between attempts
# (multiplied by the attempt number for a simple linear back-off).
_MAX_DOWNLOAD_ATTEMPTS = 3
_DOWNLOAD_BACKOFF_SECONDS = 2.0


def _download_latest(symbols: list[str]) -> pd.DataFrame:
    """Download today's 1-minute bars, retrying on transient failures.

    yfinance can return an empty frame — or raise ``ValueError: No objects to
    concatenate`` — when Yahoo throttles or briefly returns nothing for every
    ticker. Rather than letting that crash the daily report, retry with a short
    linear back-off and, if every attempt fails, return an **empty** DataFrame so
    the caller degrades to all-None prices instead of raising.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, _MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            data = yf.download(
                tickers=" ".join(symbols),
                period="1d",
                interval="1m",
                progress=False,
                threads=False,
                group_by="column",
                auto_adjust=False,
                prepost=True,  # Include pre-market and post-market data
            )
            if data is not None and not data.empty:
                return data
            last_error = None  # empty (not an exception) — treat as retryable
            print(
                f"Warning: yFinance returned no price data "
                f"(attempt {attempt}/{_MAX_DOWNLOAD_ATTEMPTS})."
            )
        except Exception as exc:  # ValueError, network/HTTP, JSON decode, etc.
            last_error = exc
            print(
                f"Warning: yFinance price download failed "
                f"(attempt {attempt}/{_MAX_DOWNLOAD_ATTEMPTS}): {exc}"
            )

        if attempt < _MAX_DOWNLOAD_ATTEMPTS:
            time.sleep(_DOWNLOAD_BACKOFF_SECONDS * attempt)

    if last_error is not None:
        print(
            f"Warning: yFinance price download exhausted "
            f"{_MAX_DOWNLOAD_ATTEMPTS} attempts ({last_error}); "
            "degrading to all-None prices."
        )
    else:
        print(
            "Warning: yFinance returned no price data after "
            f"{_MAX_DOWNLOAD_ATTEMPTS} attempts; degrading to all-None prices."
        )
    return pd.DataFrame()


def get_latest_prices(symbols: list[str], currency: str = "USD") -> dict[str, Optional[Money]]:
    """
    Return a dict of {symbol → latest closing price today as Money object or None}.

    Never raises on a flaky Yahoo response: if every download attempt fails or
    returns empty, every symbol maps to ``None`` so the daily report continues.
    """
    data = _download_latest(symbols)

    prices: dict[str, Optional[Money]] = {s: None for s in symbols}

    if not data.empty:
        if isinstance(data.columns, pd.MultiIndex):
            # Multiple symbols case
            for sym in symbols:
                try:
                    price_value = float(data["Close"][sym].iloc[-1])
                    if( pd.isna(price_value) ):
                        # If the last price is NaN, loop through previous prices to find a valid price
                        index = len(data["Close"][sym]) - 1
                        while( pd.isna(price_value) and index > 0):
                            index -= 1
                            price_value= float(data["Close"][sym].iloc[index])
                    # assert not pd.isna(price_value), f"yFinance call to get current prices returned all prices as NaN for the symbol {sym}!"
                    if pd.isna(price_value):
                        print(f"Warning:yFinance returned all prices as NaN for the symbol {sym}!")
                        price_value=0
                    prices[sym] = Money(price_value, currency)
                except KeyError:
                    pass
        else:
            # Single symbol case
            try:
                price_value = float(data["Close"].iloc[-1])
                prices[symbols[0]] = Money(price_value, currency)
            except KeyError:
                pass

    return prices

def get_descriptive_info(symbols: list[str]) -> list[dict]:
    """
    Return a dict of descriptive info for the given symbols.
    """

    info=[]
    tickers = yf.Tickers(" ".join(symbols))

    t= tickers.tickers

    for symbol, ticker in t.items():
        # print(f"Symbol: {symbol}, Ticker Object: {ticker}")
        earnings_date = ticker.calendar["Earnings Date"][0] if "Earnings Date" in ticker.calendar \
            else None

        quarterly_income_stmt = ticker.quarterly_income_stmt
        # print(f'Symbol {symbol} '
        #       f'{earnings_date.strftime("%Y-%m-%d") if earnings_date is not None else "N/A"}')

        # Append a dictionary with the key "earnings_date" and its value
        info.append({"symbol": symbol,
                     "earnings_date": earnings_date,
                     "quarterly_income_stmt": quarterly_income_stmt})
    return info