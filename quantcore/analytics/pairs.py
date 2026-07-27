"""Pure pair/spread statistics — aligned price Series in, values out.

No I/O, no network, no database. This is the mean-reversion half of the
arbitrage scanner: given two aligned price series it answers "how much of x
hedges y", "is the residual stationary", "how fast does it revert", and "is
the gap closing or still widening".

Implemented directly on numpy rather than statsmodels deliberately.
``statsmodels``/``scipy`` are not declared in ``requirements-base.txt``, which
is installed by six containers (5 MCP wrappers + the report job); pulling the
stack in to reach ``adfuller`` would inflate all of them for three regressions
that fit in this module.

Critical-value tables come from MacKinnon's response surfaces. Two distinct
tables are needed and confusing them is the classic error: a plain ADF on an
*observed* series uses the standard τ table, while the Engle-Granger test on a
*fitted residual* needs the more demanding cointegration table (the residual
was chosen to look stationary, so the null is harder to reject).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

# MacKinnon (1994) response-surface coefficients for the ADF τ statistic with
# a constant and no trend: crit = t_inf + t1/N + t2/N**2.
_ADF_CRIT_C = {
    0.01: (-3.43035, -6.5393, -16.786),
    0.05: (-2.86154, -2.8903, -4.234),
    0.10: (-2.56677, -1.5384, -2.809),
}

# MacKinnon (1991/2010) asymptotic critical values for a residual-based
# (Engle-Granger) test with a constant and two variables (one regressor).
_EG_CRIT_2VAR = {
    0.01: -3.90,
    0.05: -3.34,
    0.10: -3.04,
}

_LEVELS = (0.01, 0.05, 0.10)


def _clean(val) -> Optional[float]:
    """Round to 6dp, mapping NaN/inf to None (mirrors indicators.safe_float)."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)


def _as_array(series, use_log: bool) -> np.ndarray:
    arr = np.asarray(pd.Series(series).astype(float).values, dtype=float)
    if use_log:
        with np.errstate(divide="ignore", invalid="ignore"):
            arr = np.where(arr > 0, np.log(np.where(arr > 0, arr, 1.0)), np.nan)
    return arr


def align(y, x) -> tuple[pd.Series, pd.Series]:
    """Inner-join two price Series on their index and drop non-finite rows.

    Every function here assumes aligned inputs; callers reading two symbols
    out of OHLCV should route them through this first so a trading-holiday
    mismatch cannot silently shift one leg against the other.
    """
    ys, xs = pd.Series(y).astype(float), pd.Series(x).astype(float)
    joined = pd.concat([ys, xs], axis=1, join="inner").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    return joined.iloc[:, 0], joined.iloc[:, 1]


def _ols(y: np.ndarray, X: np.ndarray) -> Optional[dict]:
    """OLS with t-statistics. ``X`` excludes the intercept column."""
    n = y.shape[0]
    if n <= X.shape[1] + 1:
        return None
    design = np.column_stack([np.ones(n), X])
    k = design.shape[1]
    try:
        beta, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if rank < k:
        return None
    resid = y - design @ beta
    dof = n - k
    if dof <= 0:
        return None
    rss = float(resid @ resid)
    sigma2 = rss / dof
    try:
        xtx_inv = np.linalg.inv(design.T @ design)
    except np.linalg.LinAlgError:
        return None
    var_beta = np.diag(xtx_inv) * sigma2
    with np.errstate(invalid="ignore", divide="ignore"):
        tstats = beta / np.sqrt(var_beta)
    tss = float(((y - y.mean()) ** 2).sum())
    return {
        "beta": beta,
        "tstats": tstats,
        "resid": resid,
        "r_squared": (1.0 - rss / tss) if tss > 0 else None,
        "n": n,
    }


def hedge_ratio(y, x, use_log: bool = True, stability_window: int = 60) -> dict:
    """Regress y on x and report the hedge ratio plus how stable it is.

    ``beta`` is the dollar (or log-return) exposure of x needed per unit of y.
    ``beta_stability`` is the standard deviation of trailing-window betas — a
    ratio that drifts is a ratio you cannot hold, so the scanner surfaces it
    rather than quietly reporting the full-sample number.
    """
    ya, xa = _as_array(y, use_log), _as_array(x, use_log)
    if ya.shape != xa.shape or ya.size == 0:
        return {"beta": None, "alpha": None, "r_squared": None,
                "beta_stability": None, "n": 0}
    mask = np.isfinite(ya) & np.isfinite(xa)
    ya, xa = ya[mask], xa[mask]
    fit = _ols(ya, xa.reshape(-1, 1))
    if fit is None:
        return {"beta": None, "alpha": None, "r_squared": None,
                "beta_stability": None, "n": int(ya.size)}

    rolling: list[float] = []
    if stability_window and ya.size >= stability_window * 2:
        for start in range(0, ya.size - stability_window + 1, max(1, stability_window // 2)):
            window = _ols(
                ya[start:start + stability_window],
                xa[start:start + stability_window].reshape(-1, 1),
            )
            if window is not None:
                rolling.append(float(window["beta"][1]))

    return {
        "beta": _clean(fit["beta"][1]),
        "alpha": _clean(fit["beta"][0]),
        "r_squared": _clean(fit["r_squared"]),
        "beta_stability": _clean(np.std(rolling)) if len(rolling) >= 3 else None,
        "n": int(fit["n"]),
    }


def spread_series(y, x, beta: float, alpha: float = 0.0,
                  use_log: bool = True) -> pd.Series:
    """Residual series y - (alpha + beta*x), indexed like the inputs."""
    ys, xs = pd.Series(y).astype(float), pd.Series(x).astype(float)
    if use_log:
        ys = np.log(ys.where(ys > 0))
        xs = np.log(xs.where(xs > 0))
    return (ys - (alpha + beta * xs)).dropna()


def zscore(series, window: Optional[int] = None) -> dict:
    """Latest z-score of a series against its own history.

    ``window`` trims the reference sample to the trailing N observations; the
    default (and any non-positive value) uses the full series. Guarding the
    sign matters: ``s.iloc[-window:]`` with a negative window slices from the
    *front*, so a window of -5 would silently score against everything except
    the first five bars rather than against the last five.
    """
    s = pd.Series(series).astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return {"z": None, "mean": None, "std": None, "latest": None, "n": 0}
    ref = s.iloc[-window:] if (window and window > 0) else s
    mean = float(ref.mean())
    std = float(ref.std(ddof=1)) if ref.size > 1 else 0.0
    latest = float(s.iloc[-1])
    z = (latest - mean) / std if std > 0 else None
    return {
        "z": _clean(z),
        "mean": _clean(mean),
        "std": _clean(std),
        "latest": _clean(latest),
        "n": int(ref.size),
    }


def _max_lags(n: int) -> int:
    """Schwert's rule — the *upper bound* for a lag search, not the lag itself."""
    return max(0, min(int(math.floor(12 * (n / 100.0) ** 0.25)), max(0, n // 4)))


def _adf_design(arr: np.ndarray, lag: int, rows: Optional[int] = None):
    """Build (target, regressors) for a lag order, or None if too short.

    ``rows`` trims to the trailing N observations so competing lag orders can
    be scored on an identical sample.
    """
    dy = np.diff(arr)
    if dy.size - lag < 10:
        return None
    level = arr[lag:-1]
    target = dy[lag:]
    cols = [level]
    for i in range(1, lag + 1):
        cols.append(dy[lag - i:-i])
    design = np.column_stack(cols)
    if rows is not None:
        if target.size < rows:
            return None
        target, design = target[-rows:], design[-rows:]
    return target, design


def adf_statistic(series, lags: Optional[int] = None) -> dict:
    """Augmented Dickey-Fuller test with a constant (no trend term).

    Fits ``dy_t = a + g*y_{t-1} + sum(d_i * dy_{t-i})`` and returns the t-stat
    on ``g``. A more negative statistic is stronger evidence of stationarity;
    ``reject_at`` is the tightest level whose critical value is breached, or
    None when the unit-root null survives.

    With ``lags=None`` the lag order is chosen by AIC over 0..Schwert bound,
    every candidate scored on the *same* trailing sample — AIC compares
    likelihoods, so fitting each lag on the observations it happens to leave
    over would reward short lags for having more data rather than for fitting
    better. Using the Schwert bound directly as the lag order (tempting, since
    it is what ``maxlag=None`` computes) fits ~17 lags to 500 observations and
    costs enough power that genuinely stationary spreads stop rejecting.
    """
    s = pd.Series(series).astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    arr = np.asarray(s.values, dtype=float)
    n_obs = arr.size
    if n_obs < 20:
        return {"statistic": None, "lags": None, "n": int(n_obs),
                "critical_values": {}, "reject_at": None, "stationary": False}

    if lags is None:
        max_lag = _max_lags(n_obs)
        # Common sample: what the most heavily lagged candidate can support.
        common_rows = (arr.size - 1) - max_lag
        best, lag = None, 0
        for candidate in range(0, max_lag + 1):
            design = _adf_design(arr, candidate, rows=common_rows)
            if design is None:
                break
            trial = _ols(design[0], design[1])
            if trial is None:
                continue
            rss = float(trial["resid"] @ trial["resid"])
            if rss <= 0:
                continue
            aic = trial["n"] * math.log(rss / trial["n"]) + 2 * (candidate + 2)
            if best is None or aic < best:
                best, lag = aic, candidate
        # Refit the winner on its own full sample for the reported statistic.
        design = _adf_design(arr, lag)
        fit = _ols(design[0], design[1]) if design else None
    else:
        lag = max(0, int(lags))
        design = _adf_design(arr, lag)
        fit = _ols(design[0], design[1]) if design else None

    if fit is None:
        return {"statistic": None, "lags": None, "n": int(n_obs),
                "critical_values": {}, "reject_at": None, "stationary": False}

    stat = float(fit["tstats"][1])
    n_eff = fit["n"]
    crit = {}
    for level_pct in _LEVELS:
        t_inf, t1, t2 = _ADF_CRIT_C[level_pct]
        crit[level_pct] = round(t_inf + t1 / n_eff + t2 / (n_eff ** 2), 4)

    reject_at = next((lv for lv in _LEVELS if stat < crit[lv]), None)
    return {
        "statistic": _clean(stat),
        "lags": int(lag),
        "n": int(n_eff),
        "critical_values": crit,
        "reject_at": reject_at,
        "stationary": reject_at is not None,
    }


def engle_granger(y, x, use_log: bool = True,
                  lags: Optional[int] = None) -> dict:
    """Two-step Engle-Granger cointegration test.

    Regress y on x, then ADF the residual against *cointegration* critical
    values rather than the standard ADF table — the residual was fitted to
    look stationary, so the ordinary table would reject far too readily.
    """
    ys, xs = align(y, x)
    hr = hedge_ratio(ys, xs, use_log=use_log)
    if hr["beta"] is None:
        return {"cointegrated": False, "reject_at": None, "statistic": None,
                "critical_values": _EG_CRIT_2VAR, "hedge_ratio": hr,
                "n": hr["n"], "spread": None}

    spread = spread_series(ys, xs, hr["beta"], hr["alpha"], use_log=use_log)
    adf = adf_statistic(spread, lags=lags)
    stat = adf["statistic"]
    reject_at = (
        next((lv for lv in _LEVELS if stat < _EG_CRIT_2VAR[lv]), None)
        if stat is not None else None
    )
    return {
        "cointegrated": reject_at is not None,
        "reject_at": reject_at,
        "statistic": stat,
        "critical_values": dict(_EG_CRIT_2VAR),
        "lags": adf["lags"],
        "hedge_ratio": hr,
        "n": adf["n"],
        "spread": spread,
    }


def half_life(spread) -> dict:
    """Ornstein-Uhlenbeck mean-reversion speed via an AR(1) fit.

    Fits ``ds_t = a + lam * s_{t-1}``; ``half_life = -ln(2)/ln(1+lam)``. A
    non-negative lambda means the series is not reverting at all, which is
    returned as ``None`` rather than a huge number — a half-life longer than
    the intended holding period disqualifies the trade, it does not merely
    weaken it.
    """
    s = pd.Series(spread).astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    arr = np.asarray(s.values, dtype=float)
    if arr.size < 20:
        return {"half_life_days": None, "lambda": None, "mean_reverting": False,
                "n": int(arr.size)}

    lagged = arr[:-1]
    delta = np.diff(arr)
    fit = _ols(delta, lagged.reshape(-1, 1))
    if fit is None:
        return {"half_life_days": None, "lambda": None, "mean_reverting": False,
                "n": int(arr.size)}

    lam = float(fit["beta"][1])
    if lam >= 0 or (1.0 + lam) <= 0:
        return {"half_life_days": None, "lambda": _clean(lam),
                "mean_reverting": False, "n": int(arr.size)}

    hl = -math.log(2.0) / math.log(1.0 + lam)
    return {
        "half_life_days": _clean(hl),
        "lambda": _clean(lam),
        "mean_reverting": True,
        "n": int(arr.size),
    }


TREND_TSTAT_THRESHOLD = 2.0


def spread_trend(spread) -> dict:
    """Is the gap closing or still widening?

    Regresses the spread on time and compares the slope's sign with the current
    deviation from the mean. ``widening=True`` is the GBTC pattern: a spread
    that has been getting worse for the whole sample, which the scanner must
    penalise rather than reward for being wide.

    The slope must first clear ``TREND_TSTAT_THRESHOLD`` (~5% two-sided) to
    count as a direction at all. Without that gate a spread with *no* trend
    has a slope of floating-point noise whose sign is arbitrary — on a
    perfectly symmetric series the true slope is exactly zero, and macOS
    Accelerate and Linux OpenBLAS disagree on the sign of the 1e-16 that comes
    out. That made ``widening`` platform-dependent and, worse, let rounding
    error cost a real candidate 25% of its score via the trend factor.

    ``widening=None`` means "not enough data to say"; an insignificant slope
    is a definite False.
    """
    s = pd.Series(spread).astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    arr = np.asarray(s.values, dtype=float)
    empty = {"slope_per_day": None, "drift_total": None, "slope_tstat": None,
             "intercept": None, "widening": None, "n": int(arr.size)}
    if arr.size < 10:
        return empty

    t = np.arange(arr.size, dtype=float)
    fit = _ols(arr, t.reshape(-1, 1))
    if fit is None:
        return empty

    slope = float(fit["beta"][1])
    tstat = float(fit["tstats"][1])
    deviation = float(arr[-1] - arr.mean())
    significant = math.isfinite(tstat) and abs(tstat) >= TREND_TSTAT_THRESHOLD
    return {
        "slope_per_day": _clean(slope),
        "drift_total": _clean(slope * (arr.size - 1)),
        "slope_tstat": _clean(tstat),
        # Fitted value at t=0, so a caller can draw the trend line without
        # refitting: y = intercept + slope * bar_index.
        "intercept": _clean(float(fit["beta"][0])),
        "widening": bool(significant and slope * deviation > 0),
        "n": int(arr.size),
    }


def correlation(y, x, use_log: bool = True) -> Optional[float]:
    """Pearson correlation of returns (not levels) for the aligned pair."""
    ys, xs = align(y, x)
    if ys.size < 3:
        return None
    ya, xa = _as_array(ys, use_log), _as_array(xs, use_log)
    ry, rx = np.diff(ya), np.diff(xa)
    mask = np.isfinite(ry) & np.isfinite(rx)
    ry, rx = ry[mask], rx[mask]
    if ry.size < 3 or ry.std() == 0 or rx.std() == 0:
        return None
    return _clean(float(np.corrcoef(ry, rx)[0, 1]))


def analyze_pair(y, x, use_log: bool = True,
                 zscore_window: Optional[int] = None) -> dict:
    """Full statistical workup for one pair — the single entry point services use."""
    ys, xs = align(y, x)
    eg = engle_granger(ys, xs, use_log=use_log)
    spread = eg.pop("spread", None)
    if spread is None or len(spread) == 0:
        return {
            "n": eg.get("n", 0), "hedge_ratio": eg.get("hedge_ratio"),
            "cointegration": {k: eg[k] for k in
                              ("cointegrated", "reject_at", "statistic",
                               "critical_values")},
            "zscore": {"z": None}, "half_life": {"half_life_days": None},
            "trend": {"widening": None}, "correlation": None,
        }
    return {
        "n": int(len(spread)),
        "hedge_ratio": eg["hedge_ratio"],
        "cointegration": {
            "cointegrated": eg["cointegrated"],
            "reject_at": eg["reject_at"],
            "statistic": eg["statistic"],
            "critical_values": eg["critical_values"],
        },
        "zscore": zscore(spread, window=zscore_window),
        "half_life": half_life(spread),
        "trend": spread_trend(spread),
        "correlation": correlation(ys, xs, use_log=use_log),
    }
