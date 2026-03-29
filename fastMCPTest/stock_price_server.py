import math
import yfinance as yf
from fastmcp import FastMCP

mcp = FastMCP("stock-price-server")


def _safe_int(val):
    try:
        f = float(val) if val is not None else 0.0
        return 0 if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return 0


def _summarize_options(chain_df, price, kind):
    """Return ATM-nearest strikes and aggregate stats for calls or puts."""
    df = chain_df.copy()
    df = df[df["strike"] > 0].copy()
    df["moneyness"] = abs(df["strike"] - price)

    # 5 strikes nearest to ATM
    atm = df.nsmallest(5, "moneyness")

    contracts = []
    for _, row in atm.iterrows():
        contracts.append({
            "strike": round(float(row["strike"]), 2),
            "last": round(float(row.get("lastPrice", 0)), 2),
            "bid": round(float(row.get("bid", 0)), 2),
            "ask": round(float(row.get("ask", 0)), 2),
            "iv": round(float(row.get("impliedVolatility", 0)) * 100, 1),
            "volume": _safe_int(row.get("volume")),
            "open_interest": _safe_int(row.get("openInterest")),
            "in_the_money": bool(row.get("inTheMoney", False)),
        })

    total_oi = int(df["openInterest"].fillna(0).sum())
    total_vol = int(df["volume"].fillna(0).sum())
    avg_iv = round(float(df["impliedVolatility"].fillna(0).mean()) * 100, 1)

    return {
        "atm_contracts": sorted(contracts, key=lambda x: x["strike"]),
        "total_open_interest": total_oi,
        "total_volume": total_vol,
        "avg_iv_pct": avg_iv,
    }


@mcp.tool()
def get_news(symbol: str, max_articles: int = 10) -> dict:
    """Get recent news articles for a given ticker symbol from Yahoo Finance."""
    ticker = yf.Ticker(symbol.upper())
    raw = ticker.news or []

    articles = []
    for item in raw[:max_articles]:
        content = item.get("content", {})
        pub_ts = content.get("pubDate", "")
        articles.append({
            "title": content.get("title", ""),
            "publisher": content.get("provider", {}).get("displayName", ""),
            "published": pub_ts,
            "summary": content.get("summary", ""),
            "url": content.get("canonicalUrl", {}).get("url", ""),
        })

    return {
        "symbol": symbol.upper(),
        "article_count": len(articles),
        "articles": articles,
    }


@mcp.tool()
def get_stock_price(symbol: str) -> dict:
    """Get the current stock price, Bollinger Bands (20-day, 2σ), and options chain summary for a given ticker symbol."""
    ticker = yf.Ticker(symbol.upper())
    info = ticker.fast_info

    price = info.last_price
    if price is None:
        raise ValueError(f"Could not retrieve price for symbol: {symbol}")

    # Bollinger Bands
    hist = ticker.history(period="3mo")
    close = hist["Close"]
    sma20 = close.rolling(window=20).mean().iloc[-1]
    std20 = close.rolling(window=20).std().iloc[-1]
    upper_band = sma20 + 2 * std20
    lower_band = sma20 - 2 * std20

    # Options chain (nearest expiration)
    options_data = None
    expirations = ticker.options
    if expirations:
        nearest_exp = expirations[0]
        chain = ticker.option_chain(nearest_exp)
        calls_summary = _summarize_options(chain.calls, price, "call")
        puts_summary = _summarize_options(chain.puts, price, "put")

        total_call_oi = calls_summary["total_open_interest"]
        total_put_oi = puts_summary["total_open_interest"]
        put_call_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None

        options_data = {
            "expiration": nearest_exp,
            "put_call_ratio": put_call_ratio,
            "calls": calls_summary,
            "puts": puts_summary,
        }

    return {
        "symbol": symbol.upper(),
        "price": round(price, 2),
        "currency": getattr(info, "currency", "USD"),
        "bollinger_bands": {
            "upper": round(upper_band, 2),
            "middle": round(sma20, 2),
            "lower": round(lower_band, 2),
            "period": 20,
            "std_dev": 2,
        },
        "options": options_data,
    }


if __name__ == "__main__":
    mcp.run()
