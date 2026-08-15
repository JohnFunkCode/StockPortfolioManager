"""Pure net-asset-value math for treasury/NAV vehicles.

No I/O, no network, no database. Given an entity's holdings of an underlying,
the claims that sit ahead of the common, and the share count, these functions
produce the discount a common holder is actually being offered — and what it
costs to hold while waiting.

The whole module exists because of one recurring error: quoting a vehicle's
discount against *gross* holdings. Common shareholders do not own
the gross stack. Converts and preferred sit senior to them, so a headline 37%
discount to gross BTC was really ~10% once ~$16B of senior claims were netted
out. ``net_nav_per_share`` subtracts senior claims before dividing, and there
is deliberately no "gross NAV per share" helper to reach for by mistake.
"""

from __future__ import annotations

import math
from typing import Optional


def _clean(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)


def gross_nav(units: float, spot: float, other_assets: float = 0.0) -> Optional[float]:
    """Total value of the underlying stack plus any non-underlying assets.

    This is the number the market quotes and the wrong one to divide by share
    count — it is exposed only as an input to ``net_nav`` and for reporting the
    size of the gap between gross and net.
    """
    if units is None or spot is None:
        return None
    try:
        return _clean(float(units) * float(spot) + float(other_assets or 0.0))
    except (TypeError, ValueError):
        return None


def net_nav(units: float, spot: float, senior_claims: float = 0.0,
            other_assets: float = 0.0) -> Optional[float]:
    """Gross NAV less everything senior to the common (converts + preferred)."""
    gross = gross_nav(units, spot, other_assets)
    if gross is None:
        return None
    try:
        return _clean(gross - float(senior_claims or 0.0))
    except (TypeError, ValueError):
        return None


def net_nav_per_share(units: float, spot: float, diluted_shares: float,
                      senior_claims: float = 0.0,
                      other_assets: float = 0.0) -> Optional[float]:
    """Net NAV attributable to each diluted common share.

    Returns None for a non-positive share count and for a *negative* net NAV —
    when senior claims exceed the stack the common is an option on recovery,
    not a claim on assets, and a per-share "value" would be meaningless.
    """
    nav = net_nav(units, spot, senior_claims, other_assets)
    if nav is None or nav < 0:
        return None
    try:
        shares = float(diluted_shares)
    except (TypeError, ValueError):
        return None
    if shares <= 0:
        return None
    return _clean(nav / shares)


def premium_discount(price: float, nav_per_share: float) -> Optional[float]:
    """Signed percentage premium (+) or discount (-) of price to NAV/share."""
    if price is None or nav_per_share is None:
        return None
    try:
        p, n = float(price), float(nav_per_share)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return _clean((p - n) / n * 100.0)


def carry_drag(annual_senior_cost: float, net_nav_value: float) -> Optional[float]:
    """Annual cost of servicing senior claims, as a percentage of net NAV.

    The melting-ice-cube term. A vehicle paying its preferred stack out of a
    static asset base has a NAV that shrinks every year, which means the spread
    a convergence trade is long carries a negative drift before any market move.
    """
    if annual_senior_cost is None or net_nav_value is None:
        return None
    try:
        cost, nav = float(annual_senior_cost), float(net_nav_value)
    except (TypeError, ValueError):
        return None
    if nav <= 0:
        return None
    return _clean(cost / nav * 100.0)


def years_of_burn(discount_pct: float, carry_drag_pct: float) -> Optional[float]:
    """How many years of NAV bleed the current discount actually funds.

    A 10% discount against 2.1%/yr of drag buys roughly five years — which is
    the honest way to read the "cheap" number. Returns None when there is no
    discount (a premium funds nothing) or no drag (nothing to fund).
    """
    if discount_pct is None or carry_drag_pct is None:
        return None
    try:
        disc, drag = float(discount_pct), float(carry_drag_pct)
    except (TypeError, ValueError):
        return None
    if disc >= 0 or drag <= 0:
        return None
    return _clean(abs(disc) / drag)


def exposure_ratio(gross_nav_value: float, market_cap: float) -> Optional[float]:
    """Dollars of underlying exposure carried per dollar of equity.

    The hedge-ratio correction: if each $1 of equity carries $1.6 of underlying,
    a 1:1 dollar short leaves the position materially net-long. Note this is a
    *structural* ratio only — the traded beta can differ sharply when the
    discount itself, rather than the underlying, drives the equity's P&L.
    """
    if gross_nav_value is None or market_cap is None:
        return None
    try:
        gross, cap = float(gross_nav_value), float(market_cap)
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    return _clean(gross / cap)


def premium_history(prices, spots, units: float, diluted_shares: float,
                    senior_claims: float = 0.0,
                    other_assets: float = 0.0) -> list[dict]:
    """Per-date premium/discount series from aligned price and spot sequences.

    ``prices`` and ``spots`` are parallel sequences of (date, value) pairs — the
    caller aligns them; this function only does arithmetic, and reuses
    ``net_nav_per_share``/``premium_discount`` so a historical point can never
    drift from what the point-in-time workup would report.

    IMPORTANT — the capital structure is treated as CONSTANT across the whole
    series, because a single holdings snapshot is all that is usually
    available. Every point therefore answers "what would the discount have been
    on that date, if the entity had held today's units against today's senior
    claims", which is an approximation, not history. Callers must label it as
    such; ``ArbitrageService.get_premium_history`` carries a ``method`` field
    and a note for exactly this reason.

    Dates whose inputs are unusable (non-positive spot, insolvent structure)
    are dropped rather than emitted as null holes.
    """
    out: list[dict] = []
    for (date_str, price), (_, spot) in zip(prices, spots):
        nav_ps = net_nav_per_share(units, spot, diluted_shares, senior_claims,
                                   other_assets)
        discount = premium_discount(price, nav_ps)
        if discount is None:
            continue
        out.append({
            "date": date_str,
            "premium_discount_pct": discount,
            "nav_per_share": nav_ps,
            "price": _clean(price),
        })
    return out


def analyze_nav_vehicle(price: float, units: float, spot: float,
                        diluted_shares: float, senior_claims: float = 0.0,
                        annual_senior_cost: float = 0.0,
                        other_assets: float = 0.0) -> dict:
    """Full NAV workup for one vehicle — the single entry point services use.

    Reports gross and net discounts side by side precisely so the difference
    between them is visible; the gross figure is the headline that misleads.
    """
    gross = gross_nav(units, spot, other_assets)
    net = net_nav(units, spot, senior_claims, other_assets)
    nav_ps = net_nav_per_share(units, spot, diluted_shares, senior_claims,
                               other_assets)
    discount = premium_discount(price, nav_ps)
    drag = carry_drag(annual_senior_cost, net)

    market_cap = None
    if price is not None and diluted_shares:
        try:
            market_cap = _clean(float(price) * float(diluted_shares))
        except (TypeError, ValueError):
            market_cap = None

    gross_ps = None
    gross_discount = None
    if gross is not None and diluted_shares:
        try:
            shares = float(diluted_shares)
            if shares > 0:
                gross_ps = _clean(gross / shares)
                gross_discount = premium_discount(price, gross_ps)
        except (TypeError, ValueError):
            gross_ps = None

    return {
        "gross_nav": gross,
        "net_nav": net,
        "senior_claims": _clean(senior_claims),
        "nav_per_share": nav_ps,
        "market_cap": market_cap,
        "premium_discount_pct": discount,
        "gross_premium_discount_pct": gross_discount,
        "carry_drag_pct": drag,
        "years_of_burn": years_of_burn(discount, drag),
        "exposure_ratio": exposure_ratio(gross, market_cap),
    }
