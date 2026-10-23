"""Pure stop-loss math behind ``RecommendationsService.get_stop_loss_analysis`` (#399).

No I/O. The service gathers price, VWAP, momentum, gamma wall, drawdown and
short interest; these functions turn them into two stops:

* a **technical stop** a buffer below the highest support under price, and
* a **trailing stop** whose width is the drawdown-calibrated base, adjusted
  for short interest.

The noise floor is the worst historical 1-day drop: a stop closer to price
than that will false-trigger on one bad session.
"""

from __future__ import annotations

from typing import Optional

# Buffer below the primary support: tighter for options-defined levels,
# slightly wider for statistical levels that get tested more frequently.
SUPPORT_BUFFERS: dict[str, float] = {
    "gamma_wall": 0.008,
    "vwap":       0.014,
    "sma_20":     0.014,
    "bb_lower":   0.020,
}
DEFAULT_SUPPORT_BUFFER = 0.014


def squeeze_potential(short_float_pct: float, short_ratio: float) -> str:
    if short_float_pct >= 20 and short_ratio >= 5:
        return "HIGH"
    if short_float_pct >= 10 or short_ratio >= 3:
        return "MEDIUM"
    return "LOW"


def candidate_supports(
    price: float,
    gamma_wall: Optional[float],
    vwap: float,
    sma_20: float,
    bb_lower: float,
) -> list[tuple[str, float]]:
    """Supports below price, highest first.

    The gamma wall counts only when it is below price, and VWAP only when price
    is above it (otherwise VWAP is resistance).
    """
    supports: dict[str, float] = {}
    if gamma_wall and float(gamma_wall) < price:
        supports["gamma_wall"] = float(gamma_wall)
    if vwap < price:
        supports["vwap"] = float(vwap)
    if sma_20 < price:
        supports["sma_20"] = float(sma_20)
    if bb_lower < price:
        supports["bb_lower"] = float(bb_lower)
    return sorted(supports.items(), key=lambda x: x[1], reverse=True)


def technical_stop(
    sorted_supports: list[tuple[str, float]], bb_lower: float
) -> tuple[str, float, float]:
    """``(primary_name, primary_level, stop)``: a buffer under the highest
    support, or 2% under the lower Bollinger band when there is none."""
    if sorted_supports:
        name, level = sorted_supports[0]
        return name, level, round(level * (1 - SUPPORT_BUFFERS.get(name, DEFAULT_SUPPORT_BUFFER)), 2)
    return "bb_lower", bb_lower, round(bb_lower * 0.980, 2)


def distance_pct(level: float, price: float) -> float:
    """Signed % distance from price to ``level`` (negative below price)."""
    return round((level - price) / price * 100, 2)


def trailing_stop_price(price: float, trailing_pct: float) -> float:
    return round(price * (1 - trailing_pct / 100), 2)


def calibrate_trailing(
    base_pct: float, short_float_pct: float, above_vwap: bool
) -> tuple[float, str]:
    """``(trailing_pct, impact_note)``: high short interest tightens the stop in
    a downtrend (shorts add breakdown pressure) and widens it in an uptrend
    (a squeeze cushions pullbacks)."""
    if short_float_pct >= 15 and not above_vwap:
        return (round(base_pct * 0.90, 2),
                "tightened — high short float in downtrend adds breakdown pressure")
    if short_float_pct >= 20 and above_vwap:
        return (round(base_pct * 1.10, 2),
                "widened — high short float with upward momentum adds squeeze cushion")
    return base_pct, "neutral"


def position_pnl(
    price: float,
    cost_basis: float,
    shares: int,
    technical_stop_price: float,
    trailing_stop_price: float,
) -> dict:
    """P&L now and at each stop; empty without a cost basis. With no share
    count the totals are per share."""
    if cost_basis <= 0:
        return {}
    pnl_per_share = round(price - cost_basis, 2)
    n = _pnl_shares(shares)
    return {
        "cost_basis":               round(cost_basis, 2),
        "shares":                   shares,
        "unrealized_pnl_per_share": pnl_per_share,
        "unrealized_pnl_pct":       round((price - cost_basis) / cost_basis * 100, 2),
        "total_unrealized_pnl":     round(pnl_per_share * n, 2),
        "pnl_at_technical_stop":    pnl_at_stop(technical_stop_price, cost_basis, shares),
        "pnl_at_trailing_stop":     pnl_at_stop(trailing_stop_price, cost_basis, shares),
    }


def _pnl_shares(shares: int) -> int:
    return shares if shares > 0 else 1


def pnl_at_stop(stop_price: float, cost_basis: float, shares: int) -> float:
    """Position P&L if ``stop_price`` fills; per share with no share count."""
    return round((stop_price - cost_basis) * _pnl_shares(shares), 2)


def stop_flags(
    *,
    vwap_bars: int,
    above_vwap: bool,
    macd_crossover: str,
    rsi: float,
    stop_inside_noise: bool,
    squeeze: str,
    recent_1day_pct: float,
    max_1day_pct: float,
) -> list[str]:
    flags = [f"{vwap_bars}_bars_{'above' if above_vwap else 'below'}_vwap"]
    flags.append(
        "bearish_macd" if "bearish" in macd_crossover else
        "bullish_macd_crossover" if macd_crossover == "bullish_crossover" else
        "bullish_macd"
    )
    if rsi < 35:
        flags.append(f"rsi_oversold_{rsi:.0f}")
    elif rsi > 70:
        flags.append(f"rsi_overbought_{rsi:.0f}")
    if stop_inside_noise:
        flags.append("technical_stop_inside_noise_floor")
    if squeeze == "HIGH":
        flags.append("squeeze_potential_high")
    elif squeeze == "MEDIUM":
        flags.append("squeeze_potential_medium")
    if recent_1day_pct > max_1day_pct * 1.4:
        flags.append("elevated_recent_volatility")
    return flags


def stop_summary(
    *,
    symbol: str,
    price: float,
    above_vwap: bool,
    vwap_bars: int,
    vwap: float,
    macd_crossover: str,
    rsi: float,
    primary_name: str,
    primary_level: float,
    technical_stop_price: float,
    technical_dist_pct: float,
    stop_inside_noise: bool,
    max_1day_pct: float,
    trailing_pct: float,
    trailing_stop_price: float,
    cost_basis: float,
    shares: int,
    position: dict,
) -> str:
    trend = "uptrend" if above_vwap else "downtrend"
    mom = "bullish" if "bullish" in macd_crossover else "bearish"
    lines = [
        f"{symbol} at ${price:.2f} — {trend} "
        f"({vwap_bars} bars {'above' if above_vwap else 'below'} VWAP ${vwap:.2f}).",
        f"Momentum: MACD {mom}, RSI {rsi:.0f}.",
        f"Primary support: {primary_name.replace('_', ' ')} at ${primary_level:.2f}.",
        f"Technical stop: ${technical_stop_price:.2f} ({technical_dist_pct:.1f}% from price).",
    ]
    if stop_inside_noise:
        lines.append(
            f"WARNING — technical stop is inside the {max_1day_pct:.1f}% "
            "historical 1-day noise floor and will false-trigger on a bad session."
        )
    lines.append(f"Broker trailing stop: {trailing_pct:.1f}% → ${trailing_stop_price:.2f}.")
    if cost_basis > 0 and shares > 0:
        lines.append(
            f"Position: {shares} shares @ ${cost_basis:.2f}. "
            f"P&L at trailing stop: ${position['pnl_at_trailing_stop']:+.2f}."
        )
    return " ".join(lines)


# ── Confluence stop and drawdown replay (#399 PR 2) ──────────────────────────

# A zone qualifies for a stop only when this many independent methods agree on it.
MIN_CONFLUENCE_METHODS = 2
# Same buffer as a gamma-wall stop: a confluence zone is a defined level, not a
# statistical one that gets tested often.
CONFLUENCE_STOP_BUFFER = 0.008


def _confluence_stop_row(zone: dict, stop: float, price: float, stepped: bool) -> dict:
    return {
        "stop":               stop,
        "distance_pct":       distance_pct(stop, price),
        "zone_low":           zone["zone_low"],
        "zone_high":          zone["zone_high"],
        "center":             zone["center"],
        "score":              zone["score"],
        "method_count":       zone["method_count"],
        "stepped_past_noise": stepped,
    }


def confluence_stop(
    zones: list[dict], price: float, noise_floor_pct: float
) -> tuple[Optional[dict], Optional[str]]:
    """A stop under the nearest support zone that at least
    ``MIN_CONFLUENCE_METHODS`` methods agree on, as ``(stop, reason)``.

    ``zones`` must be every scored zone, not the ranked top N: the ranking is by
    score, so the nearest qualifying zone can be cut from it. A stop closer to
    price than ``noise_floor_pct`` (the worst 1-day drop) would trip on one
    ordinary bad session, so the search steps down to the next qualifying zone;
    ``reason`` then names the zones it stepped past. When no zone clears, the
    stop is ``None`` and ``reason`` says why.
    """
    candidates = sorted(
        (z for z in zones
         if z["center"] <= price and z["method_count"] >= MIN_CONFLUENCE_METHODS),
        key=lambda z: -z["center"],
    )
    if not candidates:
        return None, (
            f"No support zone below price where {MIN_CONFLUENCE_METHODS} or more "
            "methods agree."
        )
    skipped: list[str] = []
    for zone in candidates:
        stop = round(zone["zone_low"] * (1 - CONFLUENCE_STOP_BUFFER), 2)
        if abs(distance_pct(stop, price)) >= noise_floor_pct:
            row = _confluence_stop_row(zone, stop, price, stepped=bool(skipped))
            reason = None
            if skipped:
                reason = (
                    f"Stepped past the zone(s) centered at {', '.join(skipped)}: a stop "
                    f"under them sits inside the {noise_floor_pct:.1f}% one-day noise floor."
                )
            return row, reason
        skipped.append(f"${zone['center']:.2f}")
    return None, (
        f"Every qualifying zone ({', '.join(skipped)}) puts the stop inside the "
        f"{noise_floor_pct:.1f}% one-day noise floor."
    )


# (key, label) of each historical drop the replay applies to today's price.
REPLAY_DROPS: tuple[tuple[str, str], ...] = (
    ("recent_1day", "Worst day, last 30 bars"),
    ("max_1day",    "Worst 1-day drop"),
    ("max_intraday", "Worst intraday drop"),
    ("max_5day",    "Worst 5-day drop"),
)


def drawdown_replay(
    price: float,
    worst_returns_pct: dict[str, float],
    technical_stop_price: float,
    trailing_stop_price: float,
    confluence_stop_price: Optional[float],
) -> list[dict]:
    """Replay each historical drop from today's price and say which stops it hits.

    ``worst_returns_pct`` maps each ``REPLAY_DROPS`` key to the signed worst
    return over that window, as ``get_historical_drawdown`` reports it. A
    window whose worst return is a gain had no drop: it replays as 0%, at
    today's price, and hits nothing. (Taking the magnitude instead would turn a
    +2% day into a 2% loss and invent a stop hit, #418 review.) A drop hits a
    stop when the price it reaches is at or below the stop.
    ``hits_confluence`` is ``None`` when there is no confluence stop.
    """
    rows = []
    for key, label in REPLAY_DROPS:
        loss = max(0.0, -worst_returns_pct[key])
        at = round(price * (1 - loss / 100), 2)
        rows.append({
            "key":             key,
            "label":           label,
            "drop_pct":        -round(loss, 2) if loss else 0.0,
            "price":           at,
            "hits_technical":  at <= technical_stop_price,
            "hits_trailing":   at <= trailing_stop_price,
            "hits_confluence": None if confluence_stop_price is None
                               else at <= confluence_stop_price,
        })
    return rows
