"""Pure options-math functions — the single home for Black-Scholes greeks,
max-pain, expected-move, and full-chain side summarisation.

No I/O, no network, no database. These functions previously existed as
duplicated copies across surfaces:
  - ``_bs_delta`` lived in both fastMCPTest/stock_price_server.py and
    api/app.py (as ``_bs_delta_local``);
  - ``_chain_side_full`` lived in both stock_price_server.py and
    options_contract_tools.py (differing only in IV rounding precision);
  - ``_compute_max_pain`` / ``_compute_expected_move`` lived inline in app.py.

Consolidating them here keeps the math identical for every caller while
preserving behavioural parity — ``chain_side_full`` takes an ``iv_decimals``
argument so each original caller's exact rounding is retained.
"""

from __future__ import annotations

import math


def safe_int(val) -> int:
    try:
        f = float(val) if val is not None else 0.0
        return 0 if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Black-Scholes greeks (no scipy required)
# ---------------------------------------------------------------------------

def norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf — no scipy required."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def norm_pdf(x: float) -> float:
    """Standard normal PDF — no scipy required."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_d1(S: float, K: float, T: float, sigma: float, r: float):
    """
    Black-Scholes d1 term, shared by delta and gamma.

    Returns None for degenerate inputs so callers can apply their own fallback.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def bs_gamma(S: float, K: float, T: float, sigma: float, r: float,
             d1: float = None) -> float:
    """
    Black-Scholes gamma for a European option (identical for calls and puts).

    Gamma peaks at the money and decays toward zero for deep ITM/OTM strikes,
    so gamma × OI correctly identifies hedging concentration near spot —
    unlike |delta| × OI, which saturates at OI for every deep-ITM strike.

    Pass a precomputed d1 to avoid recomputing it when delta was already
    derived for the same contract. Returns 0.0 for degenerate inputs.
    """
    if d1 is None:
        d1 = bs_d1(S, K, T, sigma, r)
    if d1 is None:
        return 0.0
    try:
        return norm_pdf(d1) / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def bs_delta(S: float, K: float, T: float, sigma: float,
             r: float, is_call: bool, d1: float = None) -> float:
    """
    Black-Scholes delta for a European option.

    S     — current underlying price
    K     — strike price
    T     — time to expiry in years
    sigma — implied volatility (decimal, e.g. 0.40 for 40%)
    r     — risk-free rate (decimal)
    is_call — True for call, False for put
    d1    — optional precomputed bs_d1 value to avoid recomputing it

    Returns delta in [-1, 1].  Returns ±0.5 for degenerate inputs.
    """
    if d1 is None:
        d1 = bs_d1(S, K, T, sigma, r)
    if d1 is None:
        return 0.5 if is_call else -0.5
    if is_call:
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0


def bs_vega(S: float, K: float, T: float, sigma: float, r: float,
            d1: float = None) -> float:
    """
    Black-Scholes vega for a European option (identical for calls and puts).

    Vega = S · φ(d1) · √T — the option price change per 1.00 (100 percentage
    points) move in implied volatility. Pass a precomputed d1 to avoid
    recomputing it. Returns 0.0 for degenerate inputs.
    """
    if d1 is None:
        d1 = bs_d1(S, K, T, sigma, r)
    if d1 is None:
        return 0.0
    try:
        return S * norm_pdf(d1) * math.sqrt(T)
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def bs_vanna(S: float, K: float, T: float, sigma: float, r: float,
             d1: float = None) -> float:
    """
    Black-Scholes vanna — ∂delta/∂sigma (equivalently ∂vega/∂S), identical
    for calls and puts.

    Vanna = −φ(d1) · d2 / σ  where d2 = d1 − σ√T. When dealers carry vanna,
    a change in implied volatility forces delta re-hedging in the underlying —
    the "vanna flow" that links vol crushes/spikes to spot buying/selling.
    Returns 0.0 for degenerate inputs.
    """
    if d1 is None:
        d1 = bs_d1(S, K, T, sigma, r)
    if d1 is None:
        return 0.0
    try:
        d2 = d1 - sigma * math.sqrt(T)
        return -norm_pdf(d1) * d2 / sigma
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def bs_charm(S: float, K: float, T: float, sigma: float, r: float,
             is_call: bool = True, d1: float = None) -> float:
    """
    Black-Scholes charm — ∂delta/∂T (delta decay per year), a.k.a. delta bleed.

    With zero dividend yield (q=0, matching the other greeks here) the call and
    put charms coincide:

        charm = −φ(d1) · (2rT − d2·σ√T) / (2T·σ√T),  d2 = d1 − σ√T

    ``is_call`` is accepted for forward-compatibility with a dividend-yield
    (q>0) implementation, where call/put charm differ by q·e^{−qT}·N(±d1).
    Dealers' charm exposure drives the systematic delta re-hedging into expiry
    (the "charm flow" around OpEx). Returns 0.0 for degenerate inputs.
    """
    if d1 is None:
        d1 = bs_d1(S, K, T, sigma, r)
    if d1 is None:
        return 0.0
    try:
        sqrt_t = math.sqrt(T)
        d2 = d1 - sigma * sqrt_t
        return -norm_pdf(d1) * (2 * r * T - d2 * sigma * sqrt_t) / (2 * T * sigma * sqrt_t)
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def bs_price(S: float, K: float, T: float, sigma: float, r: float,
             kind: str = "call") -> float:
    """
    Black-Scholes European option price.

    Degenerate inputs (T <= 0, sigma <= 0, non-positive S/K) fall back to
    (discounted) intrinsic value so expiry payoffs come out exact. This is the
    reference implementation mirrored by the frontend spreadMath.ts twin used
    to draw the spread-payoff "value today" curve.
    """
    kind = kind.lower()
    if kind not in ("call", "put"):
        raise ValueError("kind must be 'call' or 'put'")
    d1 = bs_d1(S, K, T, sigma, r)
    if d1 is None:
        if T <= 0:
            return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
        discounted_k = K * math.exp(-r * max(T, 0.0))
        return max(S - discounted_k, 0.0) if kind == "call" else max(discounted_k - S, 0.0)
    d2 = d1 - sigma * math.sqrt(T)
    call = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    if kind == "call":
        return call
    return call - S + K * math.exp(-r * T)  # put-call parity


# ---------------------------------------------------------------------------
# Full-chain side summary
# ---------------------------------------------------------------------------

def chain_side_full(chain_df, iv_decimals: int = 1) -> dict:
    """Return all contracts + aggregate stats for one side (calls or puts).

    ``iv_decimals`` controls IV rounding precision so each historical caller's
    exact output is preserved: stock_price_server's full-chain path used 1,
    options_contract_tools used 2.
    """
    df = chain_df.copy()
    df = df[df["strike"] > 0].copy()

    contracts = []
    for _, row in df.iterrows():
        contracts.append({
            "strike":        round(float(row["strike"]), 2),
            "last":          round(float(row.get("lastPrice", 0) or 0), 2),
            "bid":           round(float(row.get("bid", 0) or 0), 2),
            "ask":           round(float(row.get("ask", 0) or 0), 2),
            "iv":            round(float(row.get("impliedVolatility", 0) or 0) * 100, iv_decimals),
            "volume":        safe_int(row.get("volume")),
            "open_interest": safe_int(row.get("openInterest")),
            "in_the_money":  bool(row.get("inTheMoney", False)),
        })

    total_oi  = int(df["openInterest"].fillna(0).sum())
    total_vol = int(df["volume"].fillna(0).sum())
    avg_iv    = round(float(df["impliedVolatility"].fillna(0).mean()) * 100, iv_decimals)

    return {
        "contracts":           sorted(contracts, key=lambda x: x["strike"]),
        "total_open_interest": total_oi,
        "total_volume":        total_vol,
        "avg_iv_pct":          avg_iv,
    }


# ---------------------------------------------------------------------------
# Max pain & expected move (from stored full-chain contract dicts)
# ---------------------------------------------------------------------------

def compute_max_pain(contracts: list[dict]):
    """
    Return (max_pain_strike, pain_by_strike) where pain_by_strike maps
    strike → total dollar pain if the stock settled at that strike.
    """
    calls: dict[float, int] = {}
    puts:  dict[float, int] = {}
    for c in contracts:
        oi = int(c.get("open_interest") or 0)
        s  = float(c.get("strike") or 0)
        if s <= 0 or oi <= 0:
            continue
        if c["kind"] == "call":
            calls[s] = calls.get(s, 0) + oi
        else:
            puts[s]  = puts.get(s, 0)  + oi

    all_strikes = sorted(set(list(calls) + list(puts)))
    if not all_strikes:
        return None, {}

    min_pain = float("inf")
    max_pain_strike = all_strikes[0]
    pain_by_strike: dict[float, float] = {}

    for test_s in all_strikes:
        pain  = sum((test_s - k) * oi * 100 for k, oi in calls.items() if test_s > k)
        pain += sum((k - test_s) * oi * 100 for k, oi in puts.items()  if test_s < k)
        pain_by_strike[test_s] = pain
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = test_s

    return max_pain_strike, pain_by_strike


def compute_expected_move(contracts: list[dict], current_price: float):
    """
    Estimate expected move as the ATM straddle price (call last + put last).
    Returns (em_dollar, em_pct, atm_strike).
    """
    calls = {float(c["strike"]): c for c in contracts if c["kind"] == "call"}
    puts  = {float(c["strike"]): c for c in contracts if c["kind"] == "put"}

    all_strikes = sorted(set(list(calls) + list(puts)))
    if not all_strikes or current_price <= 0:
        return 0.0, 0.0, None

    atm_strike = min(all_strikes, key=lambda s: abs(s - current_price))
    call_last  = float((calls.get(atm_strike) or {}).get("last_price") or 0)
    put_last   = float((puts.get(atm_strike)  or {}).get("last_price") or 0)
    straddle   = call_last + put_last
    em_pct     = (straddle / current_price * 100) if current_price > 0 else 0.0
    return straddle, em_pct, atm_strike
