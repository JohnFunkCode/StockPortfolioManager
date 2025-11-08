import math
import pandas as pd
import yfinance as yf


def compute_historical_volatility(prices):
    """
    Compute daily and annualized volatility from a series of prices using log returns.

    Returns:
        (daily_vol, annual_vol) or (None, None) if not enough data.
    """
    if len(prices) < 2:
        return None, None

    log_returns = []
    for i in range(1, len(prices)):
        p_prev = prices[i - 1]
        p_curr = prices[i]
        if p_prev <= 0 or p_curr <= 0:
            continue
        r = math.log(p_curr / p_prev)
        log_returns.append(r)

    if len(log_returns) < 2:
        return None, None

    mean_r = sum(log_returns) / len(log_returns)
    var_r = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(var_r)
    annual_vol = daily_vol * math.sqrt(252.0)
    return daily_vol, annual_vol


def suggest_H_from_vol(prices, alpha=0.5, min_H=0.05, max_H=0.30):
    """
    Derive a harvest threshold H from historical volatility.

    alpha : scaling factor for annualized volatility (H_raw = alpha * sigma_annual)
    min_H : minimum allowed H (e.g. 0.05 for 5%)
    max_H : maximum allowed H (e.g. 0.30 for 30%)

    Returns:
        (H, annual_vol) or (None, None) if volatility cannot be computed.
    """
    _, annual_vol = compute_historical_volatility(prices)
    if annual_vol is None:
        return None, None

    H_raw = alpha * annual_vol
    H = max(min_H, min(max_H, H_raw))
    return H, annual_vol


def run_harvest_from_prices_with_iterations(prices, H, s0, tax_rate=0.0, n_iterations=1):
    """
    Path-based simulator with taxes and a fixed number of desired harvests.

    prices     : list of daily prices (e.g., length ~360)
    H          : harvest threshold as fraction of original investment (e.g. 0.2 = +20%)
    s0         : initial integer share count
    tax_rate   : effective tax rate on each harvest (0.0–1.0)
    n_iterations : target number of harvest events to execute

    Strategy:
    - Original principal V0 = s0 * P0 is fixed as the "capital to preserve".
    - Trigger: whenever current value >= (1 + H) * V0 *and* we haven't yet done
      n_iterations harvests.
    - At trigger: sell max whole shares while keeping remaining value >= V0.
    - Taxes are applied to gross cash harvested each time.

    Returns:
        success: bool (True iff we achieved exactly n_iterations harvests
                       without violating constraints)
        harvests: list of harvest-event dicts
        final_state: dict summarizing ending wealth and returns
    """
    N = len(prices)
    P0 = prices[0]
    V0 = s0 * P0

    s = s0  # current shares
    total_gross_harvested = 0.0
    total_net_harvested = 0.0
    total_tax_paid = 0.0
    harvests = []
    harvest_count = 0

    for t in range(1, N):
        P = prices[t]
        value = s * P

        # Only apply strategy until we've done n_iterations harvests
        if harvest_count < n_iterations and value >= V0 * (1 + H):
            # minimum shares to keep so remaining value >= V0 at this price
            min_shares_after = math.ceil(V0 / P)

            # we must be able to sell at least 1 share
            if min_shares_after > s - 1:
                return False, harvests, None  # cannot execute required harvest

            s_after = min_shares_after
            shares_sold = s - s_after
            gross_cash = shares_sold * P
            tax = gross_cash * tax_rate
            net_cash = gross_cash - tax

            total_gross_harvested += gross_cash
            total_net_harvested += net_cash
            total_tax_paid += tax
            harvest_count += 1

            harvests.append({
                "day": t,
                "price": P,
                "shares_before": s,
                "shares_sold": shares_sold,
                "shares_after": s_after,
                "gross_cash_harvested": gross_cash,
                "tax_paid": tax,
                "net_cash_harvested": net_cash,
                "remaining_value": s_after * P,
            })

            s = s_after

            if s <= 0:
                return False, harvests, None  # pathological, but guard anyway

    # End of horizon: compute final wealth metrics (after at most n_iterations harvests)
    Pend = prices[-1]
    final_value = s * Pend

    total_wealth_after_tax = final_value + total_net_harvested
    total_wealth_gross = final_value + total_gross_harvested
    total_return_after_tax = total_wealth_after_tax / V0 - 1
    total_return_gross = total_wealth_gross / V0 - 1

    years = (N - 1) / 365.0
    if years > 0:
        annualized_return_after_tax = (total_wealth_after_tax / V0) ** (1 / years) - 1
        annualized_return_gross = (total_wealth_gross / V0) ** (1 / years) - 1
    else:
        annualized_return_after_tax = 0.0
        annualized_return_gross = 0.0

    final_state = {
        "shares_final": s,
        "ending_price": Pend,
        "ending_value": final_value,
        "total_gross_harvested": total_gross_harvested,
        "total_net_harvested": total_net_harvested,
        "total_tax_paid": total_tax_paid,
        "total_wealth_after_tax": total_wealth_after_tax,
        "total_wealth_gross": total_wealth_gross,
        "total_return_after_tax": total_return_after_tax,
        "total_return_gross": total_return_gross,
        "annualized_return_after_tax": annualized_return_after_tax,
        "annualized_return_gross": annualized_return_gross,
        "harvest_count": harvest_count,
    }

    # Success: we managed to execute exactly n_iterations harvests
    success = (harvest_count == n_iterations)
    return success, harvests, final_state


def compute_price_targets(P0, H, s0, n_harvests):
    """
    Compute the *theoretical* price target ladder for the plan, assuming
    each harvest executes exactly at the threshold price.

    P0         : initial price
    H          : threshold fraction (e.g. 0.2 = +20%)
    s0         : initial shares
    n_harvests : desired number of harvests

    Returns: list of dicts describing each ladder rung.
    """
    V0 = s0 * P0
    s = s0
    targets = []

    for j in range(1, n_harvests + 1):
        if s <= 1:
            break

        # price where current position hits (1+H) * V0
        P_target = (1 + H) * V0 / s

        # minimum shares we must keep at that price to preserve V0
        min_shares_after = math.ceil(V0 / P_target)  # algebraically == ceil(s / (1+H))

        # must be able to sell at least 1 share
        if min_shares_after > s - 1:
            break

        shares_sold = s - min_shares_after

        targets.append({
            "harvest": j,
            "price_target": P_target,
            "shares_before": s,
            "shares_sold": shares_sold,
            "shares_after": min_shares_after,
        })

        s = min_shares_after

    return targets


def design_harvest_plan(prices, H, tax_rate, n_iterations, max_s0=1000):
    """
    Backtest-style entry point (kept for completeness).
    """
    P0 = prices[0]

    for s0 in range(1, max_s0 + 1):
        success, harvests, final_state = run_harvest_from_prices_with_iterations(
            prices, H, s0, tax_rate=tax_rate, n_iterations=n_iterations
        )
        if success:
            ladder = compute_price_targets(P0, H, s0, n_iterations)
            return {
                "s0": s0,
                "ladder": ladder,
                "harvests": harvests,
                "final_state": final_state,
            }

    # No feasible plan within [1, max_s0]
    return None


def design_forward_ladder_from_history(
    prices,
    H=None,
    n_iterations=5,
    max_s0=1000,
    alpha=0.5,
    min_H=0.05,
    max_H=0.30,
):
    """
    Use the last N days of prices to infer an average daily growth rate, then build a
    forward-looking price target ladder starting from the most recent price.

    prices      : list of daily prices (e.g., 360 values)
    H           : harvest threshold fraction; if None, derive dynamically from volatility
    n_iterations: desired number of harvests going forward
    max_s0      : upper bound to search for initial share count
    alpha       : scaling factor for volatility -> H mapping
    min_H/max_H : clamps for the dynamic H

    Returns:
        dict with:
          - "s0": minimal initial shares required (if found)
          - "P_current": most recent price used as the starting point
          - "r_daily": inferred average daily growth rate
          - "H": chosen harvest threshold
          - "annual_vol": annualized volatility used to compute H (if H was None)
          - "V0": initial investment value
          - "n_iterations": planned number of harvests
          - "ladder": list of ladder rungs with expected days and projected cash/gains
        or None if no feasible s0 <= max_s0 can achieve n_iterations.
    """
    if len(prices) < 2:
        return None

    P_start = prices[0]
    P_current = prices[-1]
    N = len(prices) - 1

    if P_start <= 0 or P_current <= 0:
        return None

    # Infer average daily growth rate from the last N days
    r_daily = (P_current / P_start) ** (1.0 / N) - 1.0

    # If H is not provided, derive it from historical volatility
    annual_vol = None
    if H is None:
        H, annual_vol = suggest_H_from_vol(
            prices,
            alpha=alpha,
            min_H=min_H,
            max_H=max_H,
        )
        if H is None:
            return None
    else:
        # If H is fixed, still compute annual_vol for reporting
        _, annual_vol = compute_historical_volatility(prices)

    # Search for minimal s0 such that the theoretical ladder can achieve n_iterations
    for s0 in range(1, max_s0 + 1):
        ladder = compute_price_targets(P_current, H, s0, n_iterations)
        if len(ladder) == n_iterations:
            # Original principal for the forward plan
            V0 = s0 * P_current

            # Enrich each rung with an estimated time and projected cash/gains
            enriched = []
            cumulative_harvest = 0.0
            for rung in ladder:
                P_target = rung["price_target"]
                shares_before = rung["shares_before"]
                shares_sold = rung["shares_sold"]
                shares_after = rung["shares_after"]

                # Time to target based on inferred daily growth
                if r_daily > 0 and P_target > P_current:
                    days_to_target = math.log(P_target / P_current) / math.log(1.0 + r_daily)
                else:
                    days_to_target = None

                # Projected cash flows and gains at this rung
                gross_harvest = shares_sold * P_target
                cumulative_harvest += gross_harvest
                remaining_value = shares_after * P_target
                total_wealth = cumulative_harvest + remaining_value
                total_return = total_wealth / V0 - 1.0

                rung_with_time = dict(rung)
                rung_with_time["expected_days_from_now"] = days_to_target
                rung_with_time["gross_harvest"] = gross_harvest
                rung_with_time["cumulative_harvest"] = cumulative_harvest
                rung_with_time["remaining_value"] = remaining_value
                rung_with_time["total_wealth"] = total_wealth
                rung_with_time["total_return_vs_initial"] = total_return
                enriched.append(rung_with_time)

            return {
                "s0": s0,
                "P_current": P_current,
                "r_daily": r_daily,
                "H": H,
                "annual_vol": annual_vol,
                "V0": V0,
                "n_iterations": n_iterations,
                "ladder": enriched,
            }

    # No feasible ladder within [1, max_s0]
    return None


def fetch_prices(symbol, days=360):
    """
    Fetches historical daily closing prices for the past `days` days using yfinance.

    symbol : str
        Stock ticker symbol, e.g., 'AAPL' or 'NVDA'
    days   : int
        Number of most recent days to retrieve (default = 360)

    Returns:
        List[float] of daily close prices (most recent last),
        or None if data retrieval fails.
    """
    try:
        df = yf.download(symbol, period=f"{days}d", interval="1d", progress=False)
        if df.empty:
            print(f"[fetch_prices] No data found for {symbol}")
            return None

        # yfinance can return either a Series or a DataFrame (e.g., with multi-index columns).
        close_data = df["Close"]

        # If this is a DataFrame (e.g., single symbol but with a symbol-level column), pick the appropriate column.
        if isinstance(close_data, pd.DataFrame):
            if symbol in close_data.columns:
                close_data = close_data[symbol]
            else:
                # Fallback: take the first column
                close_data = close_data.iloc[:, 0]

        closes = close_data.dropna().tolist()
        return closes
    except Exception as e:
        print(f"[fetch_prices] Error fetching data for {symbol}: {e}")
        return None


if __name__ == "__main__":
    symbol = "msft"
    prices = fetch_prices(symbol, days=360)
    if prices:
        n_iterations = 5
        forward_plan = design_forward_ladder_from_history(
            prices,
            H=None,                   # dynamic H from volatility swap this out for 0.10 to see how it works.
            n_iterations=n_iterations,
            max_s0=1000,
            alpha=0.5,
            min_H=0.05,
            max_H=0.30,
        )

        if forward_plan:
            print(f"\nForward Price Target Ladder for {symbol}")
            print(f"Current price: {forward_plan['P_current']:.2f}")
            print(f"Inferred daily growth rate: {forward_plan['r_daily']:.6f}")
            print(f"Inferred annual volatility: {forward_plan['annual_vol'] * 100:.2f}%\n")

            print(
                f"Harvest threshold (H): {forward_plan['H']:.3f} "
                f"— dynamic, based on historical volatility."
            )
            print(
                f"Number of planned harvests (n_iterations): {forward_plan['n_iterations']} "
                f"— total harvest steps in this ladder.\n"
            )

            print(f"Initial shares required: {forward_plan['s0']}\n")

            V0 = forward_plan["V0"]
            print("Starting Position Summary:")
            print(f"  Initial investment value (V0): ${V0:.2f}")
            print(f"  Shares held initially:         {forward_plan['s0']}")
            print(f"  Starting price per share:      ${forward_plan['P_current']:.2f}")
            print(f"  Total capital at risk:         ${V0:.2f}\n")

            print("Price Target Ladder (forward-looking):")
            for rung in forward_plan["ladder"]:
                days = rung["expected_days_from_now"]
                days_str = f"{days:.1f} days" if days is not None else "N/A"
                print(
                    f"  Harvest {rung['harvest']}: "
                    f"Target ${rung['price_target']:.2f}, "
                    f"Sell {rung['shares_sold']} shares "
                    f"(→ {rung['shares_after']} remain), "
                    f"Expected in ~{days_str}"
                )
                print(f"    Projected harvest this stage: ${rung['gross_harvest']:.2f}")
                print(f"    Cumulative harvested:         ${rung['cumulative_harvest']:.2f}")
                print(f"    Remaining position value:     ${rung['remaining_value']:.2f}")
                print(
                    f"    Total wealth vs initial:      ${rung['total_wealth']:.2f} "
                    f"({rung['total_return_vs_initial'] * 100:.2f}% gain)"
                )
        else:
            print(f"No feasible forward ladder found for {symbol}.")