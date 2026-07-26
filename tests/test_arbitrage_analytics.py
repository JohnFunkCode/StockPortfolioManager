"""Pure-math tests for quantcore/analytics/pairs.py.

Constructed series with known properties: a cointegrated pair must be
detected, independent random walks must not, and a synthetic Ornstein-
Uhlenbeck process must give back the half-life it was built with. No I/O,
no database, no network — these run on numpy alone.
"""
import unittest

import numpy as np
import pandas as pd

from quantcore.analytics import pairs

SEED = 20260726


def ar1(n: int, phi: float, sigma: float = 0.02, seed: int = SEED) -> np.ndarray:
    """AR(1) process: s_t = phi*s_{t-1} + e_t. Stationary for |phi| < 1."""
    rng = np.random.default_rng(seed)
    out = np.zeros(n)
    for t in range(1, n):
        out[t] = phi * out[t - 1] + rng.normal(0, sigma)
    return out


def random_walk(n: int, seed: int = SEED, start: float = 100.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(0, 0.01, n)))


class AlignTest(unittest.TestCase):
    def test_inner_joins_and_drops_non_finite(self):
        y = pd.Series([1.0, 2.0, np.nan, 4.0], index=[1, 2, 3, 4])
        x = pd.Series([1.0, 2.0, 3.0], index=[2, 3, 4])
        ys, xs = pairs.align(y, x)
        # index 1 missing from x, index 3 is NaN in y -> only 2 and 4 survive.
        self.assertEqual(list(ys.index), [2, 4])
        self.assertEqual(list(xs.values), [1.0, 3.0])


class HedgeRatioTest(unittest.TestCase):
    def test_recovers_a_known_beta(self):
        x = random_walk(400)
        # y = x**1.5 in log space -> beta of 1.5 on log prices.
        y = np.exp(1.5 * np.log(x))
        hr = pairs.hedge_ratio(pd.Series(y), pd.Series(x))
        self.assertAlmostEqual(hr["beta"], 1.5, places=3)
        self.assertGreater(hr["r_squared"], 0.99)
        self.assertEqual(hr["n"], 400)

    def test_unstable_beta_is_flagged_by_stability(self):
        rng = np.random.default_rng(SEED)
        x = random_walk(400)
        # Beta flips from 1.0 to 2.5 halfway through the sample.
        half = 200
        y = np.concatenate([
            np.exp(1.0 * np.log(x[:half])),
            np.exp(2.5 * np.log(x[half:])),
        ]) * np.exp(rng.normal(0, 0.001, 400))
        hr = pairs.hedge_ratio(pd.Series(y), pd.Series(x), stability_window=60)
        self.assertIsNotNone(hr["beta_stability"])
        self.assertGreater(hr["beta_stability"], 0.1)

    def test_empty_input_returns_nulls_not_an_exception(self):
        hr = pairs.hedge_ratio(pd.Series(dtype=float), pd.Series(dtype=float))
        self.assertIsNone(hr["beta"])
        self.assertEqual(hr["n"], 0)


class AdfTest(unittest.TestCase):
    def test_stationary_series_rejects_the_unit_root(self):
        for seed in range(10):
            adf = pairs.adf_statistic(ar1(500, phi=0.5, seed=seed))
            self.assertTrue(adf["stationary"], f"seed {seed}")
            self.assertLess(adf["statistic"], adf["critical_values"][0.01])

    def test_random_walks_reject_no_more_often_than_the_test_size(self):
        """Size check: a unit-root series must reject only at the nominal rate.

        Asserted across seeds rather than one draw — at the 10% level roughly
        one walk in ten rejects by construction, so a single-seed assertion
        would be testing the seed, not the estimator.
        """
        rejects = sum(
            pairs.adf_statistic(np.log(random_walk(500, seed=seed)))["stationary"]
            for seed in range(20)
        )
        self.assertLessEqual(rejects, 5)

    def test_lag_order_is_selected_not_maxed(self):
        """An AR(1) residual needs zero augmenting lags; Schwert's bound is ~17."""
        adf = pairs.adf_statistic(ar1(500, phi=0.5, seed=3))
        self.assertEqual(adf["lags"], 0)
        self.assertLess(adf["lags"], pairs._max_lags(500))

    def test_explicit_lag_order_is_honoured(self):
        adf = pairs.adf_statistic(ar1(500, phi=0.5, seed=3), lags=4)
        self.assertEqual(adf["lags"], 4)

    def test_short_series_returns_nulls(self):
        adf = pairs.adf_statistic([1.0, 2.0, 3.0])
        self.assertIsNone(adf["statistic"])
        self.assertFalse(adf["stationary"])

    def test_critical_values_are_finite_sample_adjusted(self):
        adf = pairs.adf_statistic(ar1(200, phi=0.5))
        # Finite-sample criticals sit below (more negative than) the asymptote.
        self.assertLess(adf["critical_values"][0.05], -2.86154)


class EngleGrangerTest(unittest.TestCase):
    def test_cointegrated_pairs_are_detected(self):
        """y shares x's stochastic trend plus an independent stationary wobble.

        The wobble must be drawn from its own seed: reusing x's generator
        couples the innovations, drags the fitted beta away from 1, and leaves
        a contaminated residual that fails the test for the wrong reason.
        """
        for seed in range(10):
            x = random_walk(500, seed=100 + seed)
            y = np.exp(np.log(x) + ar1(500, phi=0.9, seed=200 + seed) + 0.5)
            eg = pairs.engle_granger(pd.Series(y), pd.Series(x))
            self.assertTrue(eg["cointegrated"], f"seed {seed}")
            # Every seed clears 5%; most clear 1%, but pinning the tighter
            # level would be asserting sampling luck rather than detection.
            self.assertLess(eg["statistic"], eg["critical_values"][0.05])

    def test_independent_random_walks_rarely_cointegrate(self):
        rejects = sum(
            pairs.engle_granger(pd.Series(random_walk(500, seed=300 + s)),
                                pd.Series(random_walk(500, seed=400 + s)))["cointegrated"]
            for s in range(20)
        )
        self.assertLessEqual(rejects, 5)

    def test_uses_the_stricter_cointegration_criticals(self):
        """The residual table must be harder to clear than the plain ADF one."""
        eg = pairs.engle_granger(pd.Series(random_walk(300, seed=3)),
                                 pd.Series(random_walk(300, seed=4)))
        self.assertEqual(eg["critical_values"][0.05], -3.34)
        adf = pairs.adf_statistic(ar1(300, phi=0.5))
        self.assertGreater(adf["critical_values"][0.05], -3.34)


class HalfLifeTest(unittest.TestCase):
    def test_recovers_the_half_life_it_was_built_with(self):
        phi = 0.9
        expected = -np.log(2) / np.log(phi)  # ~6.58 days
        # OLS on AR(1) is biased in small samples; 20k observations puts the
        # estimate inside a day of truth without the test tracking the bias.
        hl = pairs.half_life(ar1(20000, phi=phi, seed=11))
        self.assertTrue(hl["mean_reverting"])
        self.assertAlmostEqual(hl["half_life_days"], expected, delta=1.0)

    def test_half_life_alone_does_not_prove_mean_reversion(self):
        """A random walk yields a plausible-looking finite half-life.

        This is exactly why ArbitrageService gates on cointegration and treats
        half-life only as a horizon check: OLS bias makes lambda slightly
        negative on unit-root data, and a naive reader sees a ~30-day reversion
        that does not exist.
        """
        hl = pairs.half_life(np.log(random_walk(500, seed=SEED)))
        adf = pairs.adf_statistic(np.log(random_walk(500, seed=SEED)))
        if hl["mean_reverting"]:
            self.assertIsNotNone(hl["half_life_days"])
            self.assertFalse(adf["stationary"])  # the gate that catches it

    def test_short_series_returns_none(self):
        self.assertIsNone(pairs.half_life([1.0, 2.0, 1.5])["half_life_days"])


class SpreadTrendTest(unittest.TestCase):
    def test_persistent_move_away_from_the_mean_is_widening(self):
        # Monotonically falling series: ends far below its own mean, still falling.
        trend = pairs.spread_trend(np.linspace(0, -50, 200))
        self.assertTrue(trend["widening"])
        self.assertLess(trend["slope_per_day"], 0)

    def test_move_back_toward_the_mean_is_not_widening(self):
        # Long rise, then a pullback through its own mean: the slope is clearly
        # positive (t = 15.2) while the latest deviation is negative, so the two
        # disagree with real magnitude rather than by a rounding accident.
        series = np.concatenate([np.linspace(0, 60, 150), np.linspace(60, 20, 50)])
        trend = pairs.spread_trend(series)
        self.assertFalse(trend["widening"])
        self.assertGreater(abs(trend["slope_tstat"]), 2.0)

    def test_insignificant_slope_never_reports_a_direction(self):
        """A symmetric series has a true slope of exactly zero.

        What comes out of lstsq is ~1e-16 whose sign differs between macOS
        Accelerate and Linux OpenBLAS. Before the significance gate this made
        `widening` platform-dependent — green locally, red in CI — and let
        rounding noise dock a candidate 25% of its score.
        """
        series = np.concatenate([np.linspace(0, -50, 100), np.linspace(-50, 0, 100)])
        trend = pairs.spread_trend(series)
        self.assertLess(abs(trend["slope_tstat"]), 2.0)
        self.assertFalse(trend["widening"])

    def test_short_series_returns_none(self):
        trend = pairs.spread_trend([1.0, 2.0])
        self.assertIsNone(trend["widening"])
        self.assertIsNone(trend["slope_tstat"])


class ZScoreTest(unittest.TestCase):
    def test_latest_value_against_full_sample(self):
        z = pairs.zscore([0.0] * 99 + [10.0])
        self.assertIsNotNone(z["z"])
        self.assertGreater(z["z"], 5)

    def test_constant_series_has_no_zscore(self):
        self.assertIsNone(pairs.zscore([5.0] * 50)["z"])

    def test_window_limits_the_reference_sample(self):
        series = list(range(100))
        full = pairs.zscore(series)
        windowed = pairs.zscore(series, window=10)
        self.assertEqual(windowed["n"], 10)
        self.assertGreater(full["z"], windowed["z"])

    def test_empty_series(self):
        self.assertIsNone(pairs.zscore([])["z"])


class CorrelationTest(unittest.TestCase):
    def test_identical_series_correlate_perfectly(self):
        s = pd.Series(random_walk(200))
        self.assertAlmostEqual(pairs.correlation(s, s), 1.0, places=6)

    def test_short_series_returns_none(self):
        self.assertIsNone(pairs.correlation(pd.Series([1.0, 2.0]),
                                            pd.Series([1.0, 2.0])))


class AnalyzePairTest(unittest.TestCase):
    def test_full_workup_shape(self):
        x = random_walk(400, seed=100)
        y = np.exp(np.log(x) + ar1(400, phi=0.9, seed=200))
        result = pairs.analyze_pair(pd.Series(y), pd.Series(x))
        for key in ("hedge_ratio", "cointegration", "zscore", "half_life",
                    "trend", "correlation", "n"):
            self.assertIn(key, result)
        self.assertNotIn("spread", result)  # series must not leak into payloads
        self.assertTrue(result["cointegration"]["cointegrated"])

    def test_insufficient_overlap_degrades_cleanly(self):
        result = pairs.analyze_pair(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0]))
        self.assertIsNone(result["zscore"]["z"])
        self.assertIsNone(result["half_life"]["half_life_days"])


if __name__ == "__main__":
    unittest.main()
