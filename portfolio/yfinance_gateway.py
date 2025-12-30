import yfinance as yf
import pandas as pd

from typing import Optional

from portfolio.money import Money


def get_latest_prices(symbols: list[str], currency: str = "USD") -> dict[str, Optional[Money]]:
    """
    Return a dict of {symbol → latest closing price today as Money object or None}.
    """
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
                    assert not pd.isna(price_value), f"yFinance returned all prices as NaN for the symbol {sym}!"
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
