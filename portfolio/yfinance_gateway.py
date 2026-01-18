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