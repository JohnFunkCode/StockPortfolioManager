#!/usr/bin/env python3
"""
Revenue Growth Weighted Score Experiment

Computes a weighted score measuring quarter-over-quarter revenue growth consistency.

Formula:
    Weighted Score = sum(max(0, QoQ_rate_i)) / sum(|QoQ_rate_i|)

where QoQ_rate_i = (Revenue_i - Revenue_{i-1}) / Revenue_{i-1}
across the last 4 quarter transitions (requires 5 quarters of data).

Score interpretation:
    1.0  = all 4 quarters had positive revenue growth
    0.0  = all 4 quarters had negative revenue growth
    0.5  = mixed, with equal magnitude of growth and decline
"""

import yfinance as yf


def compute_revenue_growth_score(quarterly_income_stmt):
    """
    Compute the Revenue Growth Weighted Score from a yfinance quarterly_income_stmt DataFrame.

    Returns a tuple of (score, qoq_rates, revenues_used) or (None, [], []) if insufficient data.
    """
    if quarterly_income_stmt is None or quarterly_income_stmt.empty:
        return None, [], []

    if "Total Revenue" not in quarterly_income_stmt.index:
        return None, [], []

    # Columns are dates (newest first), extract the Total Revenue row
    revenues = quarterly_income_stmt.loc["Total Revenue"].dropna()

    if len(revenues) < 5:
        return None, [], list(revenues.values)

    # Take 5 most recent quarters (newest first from yfinance), reverse to oldest-first
    revenues_slice = revenues.iloc[:5].values[::-1]

    # Compute 4 QoQ growth rates
    qoq_rates = []
    for i in range(1, 5):
        prev = float(revenues_slice[i - 1])
        curr = float(revenues_slice[i])
        if prev == 0:
            continue
        rate = (curr - prev) / prev
        qoq_rates.append(rate)

    if not qoq_rates:
        return None, [], list(revenues_slice)

    sum_abs = sum(abs(r) for r in qoq_rates)
    if sum_abs == 0:
        return None, qoq_rates, list(revenues_slice)

    sum_positive = sum(max(0, r) for r in qoq_rates)
    score = sum_positive / sum_abs

    return score, qoq_rates, list(revenues_slice)


def format_revenue(value):
    """Format a revenue number in human-readable form (e.g. $12.3B, $450.2M)."""
    v = float(value)
    if abs(v) >= 1e9:
        return f"${v / 1e9:.2f}B"
    elif abs(v) >= 1e6:
        return f"${v / 1e6:.1f}M"
    else:
        return f"${v:,.0f}"


def report(symbols):
    """Fetch quarterly revenue data and compute growth scores for the given symbols."""
    print("=" * 80)
    print("Revenue Growth Weighted Score Report")
    print("=" * 80)

    tickers = yf.Tickers(" ".join(symbols))

    results = []

    for symbol, ticker in tickers.tickers.items():
        print(f"\n--- {symbol} ---")

        try:
            stmt = ticker.quarterly_income_stmt
        except Exception as e:
            print(f"  Error fetching data: {e}")
            results.append((symbol, None))
            continue

        score, qoq_rates, revenues = compute_revenue_growth_score(stmt)

        if not revenues:
            print("  Insufficient quarterly revenue data")
            results.append((symbol, None))
            continue

        # Print quarterly revenues (oldest to newest)
        print("  Quarterly Revenues (oldest → newest):")
        for i, rev in enumerate(revenues):
            marker = ""
            if i > 0 and len(qoq_rates) >= i:
                rate = qoq_rates[i - 1]
                direction = "+" if rate >= 0 else ""
                marker = f"  ({direction}{rate:.1%})"
            print(f"    Q{i + 1}: {format_revenue(rev)}{marker}")

        if score is not None:
            print(f"  Weighted Score: {score:.2f} ({score * 100:.0f}%)")
        else:
            print("  Weighted Score: N/A")

        results.append((symbol, score))

    # Sort the results by score
    results.sort(key=lambda r: r[1], reverse=True)


    # Summary table
    print("\n" + "=" * 80)
    print("Summary")
    print("=" * 80)
    print(f"{'Symbol':<10} {'Score':>10}")
    print("-" * 20)
    for symbol, score in results:
        score_str = f"{score * 100:.0f}%" if score is not None else "N/A"
        print(f"{symbol:<10} {score_str:>10}")


if __name__ == "__main__":
    symbols = ["NVDA", "CAT", "GLW", "WDC", "GOOGL", "AAPL", "QCOM", "GEV", "TER"]
    report(symbols)
