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
# Vertical-spread payoff curves (issue #79 — the single home for the math the
# retired frontend twin spreadMath.ts used to duplicate; fixtures ported to
# test_options_math keep the rendered chart numerically identical)
# ---------------------------------------------------------------------------

# Risk-free rate used for the "value today" curve (was RISK_FREE in the UI).
RISK_FREE_RATE = 0.045

_SPREAD_CURVE_SAMPLES = 121


def normalize_iv(iv: float) -> float:
    """IVs arrive in percent form from chain snapshots; normalize to decimal."""
    try:
        iv = float(iv)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(iv) or iv <= 0:
        return 0.0
    return iv / 100.0 if iv > 3 else iv


def _intrinsic(S: float, K: float, kind: str) -> float:
    return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)


def spread_payoff_at(S: float, *, kind: str, long_strike: float,
                     short_strike: float, debit: float,
                     long_iv: float = 0.0, short_iv: float = 0.0) -> float:
    """P/L per share at expiration for a debit vertical (long − short − debit).

    Accepts (and ignores) the leg IVs so one spread-spec kwargs dict serves
    both the expiry and value-today functions.
    """
    return (
        _intrinsic(S, float(long_strike), kind)
        - _intrinsic(S, float(short_strike), kind)
        - float(debit)
    )


def spread_value_at(S: float, *, T: float, r: float, kind: str,
                    long_strike: float, short_strike: float,
                    long_iv: float, short_iv: float,
                    debit: float = 0.0) -> float:
    """Theoretical spread VALUE (not P/L) per share today, BS-priced per leg."""
    long_val = bs_price(S, float(long_strike), T, normalize_iv(long_iv), r, kind)
    short_val = bs_price(S, float(short_strike), T, normalize_iv(short_iv), r, kind)
    return long_val - short_val


def vertical_spread_curves(*, kind: str, long_strike: float, short_strike: float,
                           long_iv: float, short_iv: float, debit: float,
                           spot: float, T: float, r: float = RISK_FREE_RATE,
                           samples: int = _SPREAD_CURVE_SAMPLES) -> dict:
    """Expiry-payoff and value-today P/L curves over a shared price grid.

    Grid and math mirror the retired frontend twin exactly: domain spans both
    strikes (± 0.9 × span, span at least 5% of the high strike) widened to
    include spot when known; ``samples`` evenly spaced points.
    Returns {"prices": [...], "expiry": [...], "now": [...]} per share.
    """
    k_low = min(float(long_strike), float(short_strike))
    k_high = max(float(long_strike), float(short_strike))
    span = max(k_high - k_low, k_high * 0.05)
    spot = float(spot or 0.0)
    lo = max(0.0, min(k_low, spot if spot > 0 else k_low) - span * 0.9)
    hi = max(k_high, spot if spot > 0 else k_high) + span * 0.9

    spec = dict(kind=kind, long_strike=long_strike, short_strike=short_strike,
                long_iv=long_iv, short_iv=short_iv, debit=debit)
    prices, expiry, now = [], [], []
    for i in range(samples):
        S = lo + (hi - lo) * i / (samples - 1)
        prices.append(S)
        expiry.append(spread_payoff_at(S, **spec))
        now.append(spread_value_at(S, T=T, r=r, **spec) - float(debit))
    return {"prices": prices, "expiry": expiry, "now": now}


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
