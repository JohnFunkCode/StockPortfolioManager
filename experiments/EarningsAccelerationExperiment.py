#!/usr/bin/env python3
"""
Earnings Acceleration Experiment

Measures whether a company's quarter-over-quarter earnings growth rate is itself
increasing — the fundamental metric most correlated with future stock price
appreciation per O'Neil's CAN SLIM study and Jegadeesh & Livnat research.

Given 5 quarters of Net Income (oldest→newest: Q0, Q1, Q2, Q3, Q4):

1. 4 QoQ growth rates:  rate_i = (Q_i - Q_{i-1}) / |Q_{i-1}|
2. 3 acceleration deltas:  accel_j = rate_{j+1} - rate_j
3. Two scores:
   - Acceleration count: how many of the 3 deltas are positive (e.g. "2/3")
   - Average acceleration delta: mean of the 3 deltas in percentage points
"""

import yfinance as yf


def compute_earnings_acceleration(quarterly_income_stmt):
    """
    Compute earnings acceleration from a yfinance quarterly_income_stmt DataFrame.

    Returns (accel_count, accel_total, avg_accel_delta, qoq_rates, net_incomes)
    or (None, None, None, [], []) if insufficient data.
    """
    if quarterly_income_stmt is None or quarterly_income_stmt.empty:
        return None, None, None, [], []

    if "Net Income" not in quarterly_income_stmt.index:
        return None, None, None, [], []

    # Columns are dates (newest first), extract the Net Income row
    incomes = quarterly_income_stmt.loc["Net Income"].dropna()

    if len(incomes) < 5:
        return None, None, None, [], list(incomes.values)

    # Take 5 most recent quarters (newest first from yfinance), reverse to oldest-first
    incomes_slice = incomes.iloc[:5].values[::-1]
    net_incomes = [float(v) for v in incomes_slice]

    # Compute 4 QoQ growth rates using abs(prev) in denominator
    qoq_rates = []
    for i in range(1, 5):
        prev = net_incomes[i - 1]
        curr = net_incomes[i]
        if prev == 0:
            qoq_rates.append(None)
            continue
        rate = (curr - prev) / abs(prev)
        qoq_rates.append(rate)

    # Compute 3 acceleration deltas
    accel_deltas = []
    for j in range(len(qoq_rates) - 1):
        if qoq_rates[j] is not None and qoq_rates[j + 1] is not None:
            accel_deltas.append(qoq_rates[j + 1] - qoq_rates[j])

    if not accel_deltas:
        return None, None, None, [r for r in qoq_rates if r is not None], net_incomes

    accel_count = sum(1 for d in accel_deltas if d > 0)
    accel_total = len(accel_deltas)
    avg_accel_delta = sum(accel_deltas) / len(accel_deltas)

    # Filter None rates for the returned list
    clean_rates = [r for r in qoq_rates if r is not None]

    return accel_count, accel_total, avg_accel_delta, clean_rates, net_incomes


def format_dollars(value):
    """Format a dollar amount in human-readable form (e.g. $12.3B, $450.2M)."""
    v = float(value)
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1e9:
        return f"{sign}${av / 1e9:.2f}B"
    elif av >= 1e6:
        return f"{sign}${av / 1e6:.1f}M"
    else:
        return f"{sign}${av:,.0f}"


def report(symbols):
    """Fetch quarterly earnings data and compute acceleration scores for the given symbols."""
    print("=" * 80)
    print("Earnings Acceleration Report")
    print("=" * 80)

    tickers = yf.Tickers(" ".join(symbols))

    results = []

    for symbol, ticker in tickers.tickers.items():
        print(f"\n--- {symbol} ---")

        try:
            stmt = ticker.quarterly_income_stmt
        except Exception as e:
            print(f"  Error fetching data: {e}")
            results.append((symbol, None, None, None))
            continue

        accel_count, accel_total, avg_delta, qoq_rates, net_incomes = (
            compute_earnings_acceleration(stmt)
        )

        if not net_incomes:
            print("  Insufficient quarterly earnings data")
            results.append((symbol, None, None, None))
            continue

        # Print quarterly net incomes (oldest to newest)
        print("  Quarterly Net Income (oldest -> newest):")
        for i, inc in enumerate(net_incomes):
            marker = ""
            if i > 0 and i - 1 < len(qoq_rates):
                rate = qoq_rates[i - 1]
                direction = "+" if rate >= 0 else ""
                marker = f"  ({direction}{rate:.1%})"
            print(f"    Q{i + 1}: {format_dollars(inc)}{marker}")

        if accel_count is not None:
            # Recompute acceleration deltas for display
            # (we need the raw rates including position info)
            raw_rates = []
            for i in range(1, 5):
                prev = net_incomes[i - 1]
                curr = net_incomes[i]
                if prev == 0:
                    raw_rates.append(None)
                else:
                    raw_rates.append((curr - prev) / abs(prev))

            accel_deltas = []
            for j in range(len(raw_rates) - 1):
                if raw_rates[j] is not None and raw_rates[j + 1] is not None:
                    accel_deltas.append(raw_rates[j + 1] - raw_rates[j])

            print("  Acceleration Deltas:")
            for j, delta in enumerate(accel_deltas):
                direction = "+" if delta >= 0 else ""
                status = "accelerating" if delta > 0 else "decelerating"
                print(f"    Q{j + 2}->Q{j + 3}: {direction}{delta:.1%} ({status})")

            print(f"  Acceleration Count: {accel_count}/{accel_total}")
            direction = "+" if avg_delta >= 0 else ""
            print(f"  Avg Acceleration Delta: {direction}{avg_delta:.1%} per quarter")
        else:
            print("  Acceleration: N/A (insufficient data)")

        results.append((symbol, accel_count, accel_total, avg_delta))

    # Sort by acceleration count desc, then by avg delta desc as tiebreaker
    def sort_key(r):
        count = r[1] if r[1] is not None else -1
        delta = r[3] if r[3] is not None else float("-inf")
        return (count, delta)

    results.sort(key=sort_key, reverse=True)

    # Summary table
    print("\n" + "=" * 80)
    print("Summary (sorted by acceleration count, then avg delta)")
    print("=" * 80)
    print(f"{'Symbol':<10} {'Accel Count':>12} {'Avg Delta':>12}")
    print("-" * 34)
    for symbol, accel_count, accel_total, avg_delta in results:
        if accel_count is not None:
            count_str = f"{accel_count}/{accel_total}"
            direction = "+" if avg_delta >= 0 else ""
            delta_str = f"{direction}{avg_delta:.1%}/qtr"
        else:
            count_str = "N/A"
            delta_str = "N/A"
        print(f"{symbol:<10} {count_str:>12} {delta_str:>12}")


if __name__ == "__main__":
    symbols = ["NVDA", "CAT", "GLW", "WDC", "GOOGL", "AAPL", "QCOM", "GEV", "TER"]
    report(symbols)
