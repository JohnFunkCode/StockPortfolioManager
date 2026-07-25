"""Unit tests for RecommendationsService.get_support_confluence (issue #93 Phase 6).

Fully offline, per the test_recommendations_service.py pattern: stub
collaborators return canned dicts (or canned callables for arg-dependent
methods like get_history); a method absent from the canned map raises
AttributeError, which the per-source ``try/except`` records in
``methods_failed`` — exactly the production behavior when an upstream
service errors.
"""

import unittest

import pandas as pd

from quantcore.services.recommendations import RecommendationsService


class _Fake:
    """Stub collaborator: ``obj.method(...)`` returns the canned value for
    ``method`` (ignoring args) — or, when the canned value is itself a
    callable, dispatches to it with the call's args. Unknown methods raise
    AttributeError."""

    def __init__(self, **canned):
        self._canned = canned

    def __getattr__(self, name):
        if name.startswith("_") or name not in self.__dict__.get("_canned", {}):
            raise AttributeError(name)
        value = self._canned[name]
        if callable(value):
            return value
        return lambda *a, **k: value


def _build(prices=None, options=None) -> RecommendationsService:
    return RecommendationsService(
        prices=_Fake(**(prices or {})),
        options=_Fake(**(options or {})),
        microstructure=_Fake(),
        sentiment=_Fake(),
        fundamentals=_Fake(),
        ohlcv_repository=_Fake(),
        yfinance_gateway=_Fake(),
    )


def _price(**overrides):
    data = {
        "price": 100.0,
        "bollinger_bands": {"upper": 110.0, "middle": 99.8, "lower": 90.0},
    }
    data.update(overrides)
    return data


def _ohlcv_df(closes, start="2026-06-01", freq="B"):
    idx = pd.date_range(start, periods=len(closes), freq=freq)
    return pd.DataFrame(
        {
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": list(closes),
            "Volume": [1_000_000] * len(closes),
        },
        index=idx,
    )


# V-shaped daily series: confirmed swing low at the trough, prior-day bar −2
# lands near 100 so its H/L joins the near-spot zones.
_DAILY = _ohlcv_df(
    [104, 103, 102, 101, 100, 99, 98, 97, 96, 95,
     96, 97, 98, 99, 100, 101, 102, 101, 100, 100]
)
_WEEKLY = _ohlcv_df([98, 102, 100], freq="W")   # bar −2: H 103 / L 101
_MONTHLY = _ohlcv_df([95, 105, 100], freq="MS")  # bar −2: H 106 / L 104


def _history(dfs):
    def fn(symbol, interval="1d", days=365):
        return dfs[interval]
    return fn


_FULL_PRICES = {
    "get_stock_price": _price(),
    "get_volume_profile": {
        "poc": 95.0,
        "value_area": {"low": 90.5, "high": 104.0},
        "hvns": [{"price": 95.2, "volume": 500}],
    },
    "get_anchored_vwap": {
        "anchors": [{"type": "earnings", "label": "earnings 2026-04-30", "avwap": 94.8}],
    },
    "get_vwap": {"vwap": 98.0, "position": "above_vwap"},
    "get_technicals_table": {
        "ticker": "XYZ",
        "indicators": [{"ma50": 96.0, "ma100": 93.0, "ma200": 88.0}],
    },
    "get_history": _history({"1d": _DAILY, "1wk": _WEEKLY, "1mo": _MONTHLY}),
    "get_atr_bands": {"lower_band": 95.5, "upper_band": 104.5, "chandelier_stop": 94.5},
}

_FULL_OPTIONS = {
    "get_delta_adjusted_oi": {"gamma_wall_strike": 95.0},
    "get_gex_profile": {
        "top_positive_gex_strike": 105.0,
        "top_negative_gex_strike": 95.0,
        "zero_gamma_level": 97.5,
    },
    "get_oi_change_analysis": {
        "put_oi_support_strikes": [{"strike": 95.0, "oi_build": 5000}],
        "call_oi_resistance_strikes": [{"strike": 105.0, "oi_build": 4000}],
    },
    "get_options_analytics": {
        "ticker": "XYZ",
        "analytics": [
            {"expiration": "2026-08-21", "lower_bound": 93.0, "upper_bound": 107.0}
        ],
    },
}

_ALL_SOURCES = [
    "gamma_wall", "gex_profile", "volume_profile", "anchored_vwap", "oi_change",
    "expected_move", "rolling_vwap", "moving_averages", "prev_day_hl",
    "prev_week_hl", "prev_month_hl", "bollinger_sma20", "atr_bands", "fibonacci",
]


class SupportConfluenceTests(unittest.TestCase):
    def test_full_confluence_every_source_available(self):
        svc = _build(prices=dict(_FULL_PRICES), options=dict(_FULL_OPTIONS))

        result = svc.get_support_confluence("xyz")

        self.assertEqual(result["symbol"], "XYZ")
        self.assertEqual(result["price"], 100.0)
        self.assertEqual(sorted(result["methods_available"]), sorted(_ALL_SOURCES))
        self.assertEqual(result["methods_failed"], [])
        self.assertTrue(result["support_zones"])
        self.assertTrue(result["resistance_zones"])
        self.assertEqual(result["strongest_support"], result["support_zones"][0])
        self.assertTrue(result["interpretation"])

    def test_strongest_support_is_the_multi_method_95_cluster(self):
        # Gamma wall 95, -GEX 95, POC 95, HVN 95.2, AVWAP 94.8, put-OI 95,
        # chandelier 94.5 all agree near 95 — that confluence must dominate.
        svc = _build(prices=dict(_FULL_PRICES), options=dict(_FULL_OPTIONS))

        strongest = svc.get_support_confluence("XYZ")["strongest_support"]

        self.assertIsNotNone(strongest)
        self.assertGreaterEqual(strongest["method_count"], 4)
        self.assertTrue(94.0 <= strongest["center"] <= 96.0)
        methods = {c["method"] for c in strongest["contributors"]}
        self.assertIn("gamma_wall", methods)
        self.assertIn("volume_profile", methods)

    def test_partitioning_support_below_resistance_above_price(self):
        svc = _build(prices=dict(_FULL_PRICES), options=dict(_FULL_OPTIONS))

        result = svc.get_support_confluence("XYZ")

        for z in result["support_zones"]:
            self.assertLessEqual(z["center"], result["price"])
            self.assertLess(z["distance_pct"], 0.5)
        for z in result["resistance_zones"]:
            self.assertGreater(z["center"], result["price"])
            self.assertGreater(z["distance_pct"], 0.0)

    def test_options_failures_land_in_methods_failed_without_failing_call(self):
        svc = _build(prices=dict(_FULL_PRICES), options={})  # every options call errors

        result = svc.get_support_confluence("XYZ")

        for source in ("gamma_wall", "gex_profile", "oi_change", "expected_move"):
            self.assertIn(source, result["methods_failed"])
            self.assertNotIn(source, result["methods_available"])
        # Price-side sources still produce a usable map.
        self.assertTrue(result["support_zones"])
        self.assertIn("Sources unavailable", result["interpretation"])

    def test_missing_price_returns_error_payload(self):
        result = _build().get_support_confluence("XYZ")

        self.assertEqual(result["symbol"], "XYZ")
        self.assertIn("error", result)

    def test_clustering_merges_within_tolerance_and_splits_below_it(self):
        # Two levels 95.0 (rolling VWAP) and 95.5 (SMA50): inside 1% → one
        # zone; with tolerance 0.4% (95.0·1.004 = 95.38 < 95.5) → two zones.
        prices = {
            "get_stock_price": {"price": 100.0},
            "get_vwap": {"vwap": 95.0},
            "get_technicals_table": {
                "ticker": "XYZ",
                "indicators": [{"ma50": 95.5, "ma100": None, "ma200": None}],
            },
        }

        merged = _build(prices=dict(prices)).get_support_confluence(
            "XYZ", tolerance_pct=1.0
        )
        self.assertEqual(len(merged["support_zones"]), 1)
        self.assertEqual(merged["support_zones"][0]["method_count"], 2)
        self.assertEqual(merged["support_zones"][0]["zone_low"], 95.0)
        self.assertEqual(merged["support_zones"][0]["zone_high"], 95.5)

        split = _build(prices=dict(prices)).get_support_confluence(
            "XYZ", tolerance_pct=0.4
        )
        self.assertEqual(len(split["support_zones"]), 2)
        for z in split["support_zones"]:
            self.assertEqual(z["method_count"], 1)

    def test_independent_methods_outscore_a_single_method_stack(self):
        # Zone at 90: two volume_profile levels (one method) → 0.9 + 0.2 = 1.1.
        # Zone at 95: rolling VWAP + SMA50 (two methods) → 0.7 + 0.6 = 1.3.
        prices = {
            "get_stock_price": {"price": 100.0},
            "get_volume_profile": {
                "poc": 90.0,
                "value_area": {},
                "hvns": [{"price": 90.0, "volume": 500}],
            },
            "get_vwap": {"vwap": 95.0},
            "get_technicals_table": {
                "ticker": "XYZ",
                "indicators": [{"ma50": 95.0, "ma100": None, "ma200": None}],
            },
        }

        result = _build(prices=dict(prices)).get_support_confluence("XYZ")

        self.assertEqual(len(result["support_zones"]), 2)
        strongest, runner_up = result["support_zones"]
        self.assertEqual(strongest["center"], 95.0)
        self.assertAlmostEqual(strongest["score"], 1.3)
        self.assertEqual(runner_up["center"], 90.0)
        self.assertAlmostEqual(runner_up["score"], 1.1)

    def test_levels_beyond_25pct_of_spot_are_dropped(self):
        prices = {
            "get_stock_price": {"price": 100.0},
            "get_technicals_table": {
                "ticker": "XYZ",
                # 140 (> +25%) and 74 (< −25%) must be filtered; 95 survives.
                "indicators": [{"ma50": 95.0, "ma100": 74.0, "ma200": 140.0}],
            },
        }

        result = _build(prices=dict(prices)).get_support_confluence("XYZ")

        all_zones = result["support_zones"] + result["resistance_zones"]
        self.assertEqual(len(all_zones), 1)
        self.assertEqual(all_zones[0]["center"], 95.0)

    def test_max_zones_caps_each_side(self):
        # Five well-separated support levels from distinct-strike put-OI builds.
        options = {
            "get_oi_change_analysis": {
                "put_oi_support_strikes": [
                    {"strike": s, "oi_build": 1000} for s in (80, 84, 88, 92, 96)
                ],
                "call_oi_resistance_strikes": [],
            },
        }
        prices = {"get_stock_price": {"price": 100.0}}

        result = _build(prices=prices, options=options).get_support_confluence(
            "XYZ", max_zones=3
        )

        self.assertEqual(len(result["support_zones"]), 3)


if __name__ == "__main__":
    unittest.main()
