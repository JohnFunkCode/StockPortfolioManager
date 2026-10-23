"""Pure support/resistance confluence math (issue #93 Phase 6, moved here for #399).

No I/O. ``RecommendationsService.get_support_confluence`` gathers candidate
price levels from every level-finding technique; these functions turn that
flat list into zones. Levels within ``tolerance_pct`` of each other cluster
into one zone, and each zone is scored by the summed weight of the
*independent* methods agreeing on it. Confluence across methods dominates
repetition within one method: a second level from a method already in the
zone adds only ``EXTRA_LEVEL_SCORE``.
"""

from __future__ import annotations

import math
from typing import Optional

# Ordering follows the professional support-level hierarchy: dealer
# positioning and volume acceptance carry the most information,
# derived/geometric levels the least.
CONFLUENCE_WEIGHTS: dict[str, float] = {
    "gamma_wall":      1.0,   # BS gamma × OI peak (delta-adjusted OI)
    "gex_profile":     1.0,   # signed GEX walls + zero-gamma flip
    "volume_profile":  0.9,   # POC / value area / HVNs
    "anchored_vwap":   0.9,   # institutional cost-basis AVWAPs
    "oi_change":       0.8,   # put-OI support / call-OI resistance builds
    "expected_move":   0.7,   # spot ± options expected move
    "rolling_vwap":    0.7,   # rolling 20-bar VWAP
    "sma_200":         0.7,
    "sma_100":         0.6,
    "sma_50":          0.6,
    "prev_week_hl":    0.6,   # prior completed week high/low
    "prev_month_hl":   0.6,   # prior completed month high/low
    "prev_day_hl":     0.5,   # prior session high/low
    "bollinger_sma20": 0.5,   # 20-day SMA + Bollinger bands
    "atr_bands":       0.5,   # ATR bands + chandelier stop
    "fibonacci":       0.3,   # retracements of the last major swing
}

# Levels further than this from spot are not support or resistance for today.
LEVEL_WINDOW_PCT = 25.0
EXTRA_LEVEL_SCORE = 0.2


def make_level(method: str, level, detail: str) -> Optional[dict]:
    """One candidate level, or ``None`` when ``level`` isn't a positive number."""
    try:
        lvl = float(level)
    except (TypeError, ValueError):
        return None
    if lvl <= 0 or math.isnan(lvl):
        return None
    return {
        "level":  round(lvl, 2),
        "method": method,
        "weight": CONFLUENCE_WEIGHTS[method],
        "detail": detail,
    }


def _weighted_center(members: list[dict]) -> float:
    total_w = sum(m["weight"] for m in members)
    return sum(m["level"] * m["weight"] for m in members) / total_w


def cluster_levels(levels: list[dict], price: float, tolerance_pct: float) -> list[list[dict]]:
    """Group levels inside the ±25% window into clusters, walking upward.

    A level joins the current cluster when it lies within ``tolerance_pct``
    above the cluster's running weighted center.
    """
    low, high = price * (1 - LEVEL_WINDOW_PCT / 100), price * (1 + LEVEL_WINDOW_PCT / 100)
    candidates = sorted(
        (lvl for lvl in levels if low <= lvl["level"] <= high),
        key=lambda lvl: lvl["level"],
    )
    clusters: list[list[dict]] = []
    current: list[dict] = []
    center = 0.0
    for lvl in candidates:
        if current and lvl["level"] <= center * (1 + tolerance_pct / 100):
            current.append(lvl)
        else:
            if current:
                clusters.append(current)
            current = [lvl]
        center = _weighted_center(current)
    if current:
        clusters.append(current)
    return clusters


def score_zone(members: list[dict], price: float) -> dict:
    """Zone summary: Σ(max weight per distinct method) + 0.2 per extra level."""
    c = _weighted_center(members)
    best_by_method: dict[str, float] = {}
    for m in members:
        best_by_method[m["method"]] = max(best_by_method.get(m["method"], 0.0), m["weight"])
    extras = len(members) - len(best_by_method)
    return {
        "zone_low":     min(m["level"] for m in members),
        "zone_high":    max(m["level"] for m in members),
        "center":       round(c, 2),
        "distance_pct": round((c - price) / price * 100, 2),
        "score":        round(sum(best_by_method.values()) + EXTRA_LEVEL_SCORE * extras, 2),
        "method_count": len(best_by_method),
        "contributors": sorted(members, key=lambda m: -m["weight"]),
    }


def rank_zones(zones: list[dict], price: float, max_zones: int) -> tuple[list[dict], list[dict]]:
    """Split zones at price and keep the ``max_zones`` highest-scoring per side.

    Returns ``(support_zones, resistance_zones)``; a zone centered exactly at
    price counts as support.
    """
    support = sorted((z for z in zones if z["center"] <= price), key=lambda z: -z["score"])
    resistance = sorted((z for z in zones if z["center"] > price), key=lambda z: -z["score"])
    return support[:max_zones], resistance[:max_zones]


def confluence_interpretation(
    strongest_support: Optional[dict],
    resistance_zones: list[dict],
    methods_failed: list[str],
) -> str:
    parts = []
    if strongest_support:
        s = strongest_support
        parts.append(
            f"Strongest support zone {s['zone_low']}–{s['zone_high']} "
            f"(center {s['center']}, {s['distance_pct']}% from price): "
            f"{s['method_count']} independent method(s), score {s['score']}."
        )
    else:
        parts.append("No support zones found within 25% below the current price.")
    if resistance_zones:
        r = resistance_zones[0]
        parts.append(
            f"Strongest resistance zone {r['zone_low']}–{r['zone_high']} "
            f"(center {r['center']}, +{r['distance_pct']}%), score {r['score']}."
        )
    if methods_failed:
        parts.append("Sources unavailable: " + ", ".join(methods_failed) + ".")
    return " ".join(parts)


def _zone_key(zone: dict) -> tuple:
    return (zone["zone_low"], zone["zone_high"], zone["center"])


def mark_stop_zone(
    support_zones: list[dict], zones: list[dict], stop: Optional[dict]
) -> list[dict]:
    """``support_zones`` with the zone a confluence stop sits under flagged
    ``is_stop_zone``.

    ``stop`` carries the zone's ``zone_low``/``zone_high``/``center``. If the
    ranking cut that zone from ``support_zones``, it is found in ``zones`` (every
    scored zone) and appended with ``outside_top`` as well, so the table can
    still show where the stop sits. The input lists are not modified.
    """
    if stop is None:
        return list(support_zones)
    key = _zone_key(stop)
    marked = [{**z, "is_stop_zone": True} if _zone_key(z) == key else z
              for z in support_zones]
    if any(z.get("is_stop_zone") for z in marked):
        return marked
    for zone in zones:
        if _zone_key(zone) == key:
            marked.append({**zone, "is_stop_zone": True, "outside_top": True})
            break
    return marked
