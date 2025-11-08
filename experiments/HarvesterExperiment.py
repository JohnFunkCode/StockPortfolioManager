import math
import pandas as pd

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
    Main entry point.

    prices      : list of daily prices (e.g., 360 values)
    H           : threshold fraction of original investment for each harvest
                  (e.g. 0.2 for +20%)
    tax_rate    : effective tax rate on each harvest (0–1)
    n_iterations: desired number of harvests over this price history
    max_s0      : upper bound to search for initial share count

    Returns:
        dict with:
          - "s0": minimal initial shares required (if found)
          - "ladder": theoretical price target ladder for this plan
          - "harvests": realized harvests on this price path with s0 shares
          - "final_state": performance metrics for this plan
        or None if no feasible s0 <= max_s0 can achieve n_iterations.
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

def design_forward_ladder_from_history(prices, H, n_iterations, max_s0=1000):
    """
    Use the last N days of prices to infer an average daily growth rate, then build a
    forward-looking price target ladder starting from the most recent price.

    prices      : list of daily prices (e.g., 360 values)
    H           : threshold fraction of original investment for each harvest
                  (e.g. 0.2 for +20%)
    n_iterations: desired number of harvests going forward
    max_s0      : upper bound to search for initial share count

    Returns:
        dict with:
          - "s0": minimal initial shares required (if found)
          - "P_current": most recent price used as the starting point
          - "r_daily": inferred average daily growth rate
          - "ladder": list of ladder rungs with expected days to each target
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
                "V0": V0,
                "ladder": enriched,
            }

    # No feasible ladder within [1, max_s0]
    return None

import yfinance as yf

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
        # Use the most recent `days` of daily history
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
        forward_plan = design_forward_ladder_from_history(prices, H=0.1, n_iterations=5, max_s0=1000)
        if forward_plan:
            print(f"\nForward Price Target Ladder for {symbol}")
            print(f"Current price: {forward_plan['P_current']:.2f}")
            print(f"Inferred daily growth rate: {forward_plan['r_daily']:.6f}")
            print(f"Harvest threshold (H): {0.1:.2f} — each harvest triggers when the position grows by 10% above the original investment.")
            print(f"Number of planned harvests (n_iterations): {5} — total number of times gains will be harvested in this forward plan.\n")
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
                print(f"  Harvest {rung['harvest']}: "
                      f"Target ${rung['price_target']:.2f}, "
                      f"Sell {rung['shares_sold']} shares "
                      f"(→ {rung['shares_after']} remain), "
                      f"Expected in ~{days_str}")
                print(f"    Projected harvest this stage: ${rung['gross_harvest']:.2f}")
                print(f"    Cumulative harvested:         ${rung['cumulative_harvest']:.2f}")
                print(f"    Remaining position value:     ${rung['remaining_value']:.2f}")
                print(f"    Total wealth vs initial:      ${rung['total_wealth']:.2f} "
                      f"({rung['total_return_vs_initial'] * 100:.2f}% gain)")
        else:
            print(f"No feasible forward ladder found for {symbol}.")