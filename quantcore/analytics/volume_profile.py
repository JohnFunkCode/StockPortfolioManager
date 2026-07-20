"""Pure volume-profile math — arrays in, dict out. No I/O, no network, no DB.

A volume profile redistributes each bar's traded volume across the price
levels the bar actually spanned, building a histogram of "volume at price".
Uniform intrabar distribution (each bar's volume spread evenly over
[low, high] by bin-overlap fraction) is the standard approximation for
bar-level data; binning by close alone badly distorts daily profiles.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_volume_profile(
    highs, lows, volumes, bins: int = 50, value_area_pct: float = 0.70
) -> dict:
    """Histogram of volume at price with POC and value area.

    Each bar's volume is distributed uniformly across [low, high] by the
    fraction of that range overlapping each bin (a degenerate high == low bar
    puts all its volume into the bin containing that price). POC is the
    highest-volume bin; the value area grows outward from the POC, repeatedly
    annexing the higher-volume adjacent bin (upper bin on ties) until it holds
    ``value_area_pct`` of total volume.

    Returns bin_edges / bin_centers / bin_volumes (np.ndarray), total_volume,
    poc / poc_volume, value_area_low / value_area_high (outer bin edges), and
    value_area_volume_pct (the fraction actually enclosed).
    """
    high = pd.Series(highs, dtype=float).to_numpy()
    low = pd.Series(lows, dtype=float).to_numpy()
    vol = pd.Series(volumes, dtype=float).fillna(0.0).to_numpy()

    ok = ~(np.isnan(high) | np.isnan(low)) & (vol > 0)
    high, low, vol = high[ok], low[ok], vol[ok]
    if len(vol) == 0 or float(vol.sum()) <= 0:
        raise ValueError("No volume data to build a profile from")

    price_min = float(low.min())
    price_max = float(high.max())

    if price_max <= price_min:
        # Every bar traded at one price — the profile is a single spike.
        total = float(vol.sum())
        return {
            "bin_edges": np.array([price_min, price_min]),
            "bin_centers": np.array([price_min]),
            "bin_volumes": np.array([total]),
            "total_volume": total,
            "poc": price_min,
            "poc_volume": total,
            "value_area_low": price_min,
            "value_area_high": price_min,
            "value_area_volume_pct": 1.0,
        }

    edges = np.linspace(price_min, price_max, bins + 1)
    bin_volumes = np.zeros(bins)

    for h, l, v in zip(high, low, vol):
        if h <= l:
            i = min(int(np.searchsorted(edges, l, side="right")) - 1, bins - 1)
            bin_volumes[max(i, 0)] += v
        else:
            overlap = np.minimum(h, edges[1:]) - np.maximum(l, edges[:-1])
            np.clip(overlap, 0.0, None, out=overlap)
            bin_volumes += v * overlap / (h - l)

    total = float(bin_volumes.sum())
    poc_idx = int(np.argmax(bin_volumes))

    lo = hi = poc_idx
    covered = float(bin_volumes[poc_idx])
    target = value_area_pct * total
    while covered < target and (lo > 0 or hi < bins - 1):
        below = bin_volumes[lo - 1] if lo > 0 else -1.0
        above = bin_volumes[hi + 1] if hi < bins - 1 else -1.0
        if above >= below:
            hi += 1
            covered += float(bin_volumes[hi])
        else:
            lo -= 1
            covered += float(bin_volumes[lo])

    centers = (edges[:-1] + edges[1:]) / 2
    return {
        "bin_edges": edges,
        "bin_centers": centers,
        "bin_volumes": bin_volumes,
        "total_volume": total,
        "poc": float(centers[poc_idx]),
        "poc_volume": float(bin_volumes[poc_idx]),
        "value_area_low": float(edges[lo]),
        "value_area_high": float(edges[hi + 1]),
        "value_area_volume_pct": covered / total,
    }


def find_volume_nodes(
    bin_centers, bin_volumes, hvn_ratio: float = 1.25, lvn_ratio: float = 0.60
) -> dict:
    """High/Low Volume Nodes from a profile histogram.

    Volumes are smoothed with a 3-bin centered rolling mean, then compared to
    the median smoothed volume: local maxima at >= hvn_ratio * median are HVNs
    (price acceptance), local minima at <= lvn_ratio * median are LVNs
    (rejection / air pockets). Adjacent qualifying bins are merged into one
    node placed at the run's highest- (HVN) or lowest- (LVN) raw-volume bin.

    Returns {"hvns": [{"price", "volume"}], "lvns": [...]}, price-ascending.
    """
    centers = np.asarray(bin_centers, dtype=float)
    raw = np.asarray(bin_volumes, dtype=float)
    n = len(raw)
    if n < 3:
        return {"hvns": [], "lvns": []}

    smooth = pd.Series(raw).rolling(3, center=True, min_periods=1).mean().to_numpy()
    median = float(np.median(smooth))
    if median <= 0:
        return {"hvns": [], "lvns": []}

    def _nodes(is_candidate, pick):
        flags = []
        for i in range(n):
            left = smooth[i - 1] if i > 0 else None
            right = smooth[i + 1] if i < n - 1 else None
            flags.append(is_candidate(smooth[i], left, right))
        nodes, run = [], []
        for i in range(n + 1):
            if i < n and flags[i]:
                run.append(i)
            elif run:
                j = pick(run)
                nodes.append({"price": float(centers[j]), "volume": float(raw[j])})
                run = []
        return nodes

    hvns = _nodes(
        lambda v, l, r: (l is None or v >= l) and (r is None or v >= r)
        and v >= hvn_ratio * median,
        lambda run: max(run, key=lambda i: raw[i]),
    )
    lvns = _nodes(
        lambda v, l, r: (l is None or v <= l) and (r is None or v <= r)
        and v <= lvn_ratio * median,
        lambda run: min(run, key=lambda i: raw[i]),
    )
    return {"hvns": hvns, "lvns": lvns}
