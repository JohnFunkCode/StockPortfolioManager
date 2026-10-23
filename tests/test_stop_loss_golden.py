"""Characterization tests pinning the stop-loss, support-confluence and trade
recommendation outputs before their maths moves into ``quantcore/analytics``
(#399, PR 1 of docs/proposals/stop-loss-card-plan.md).

Each scenario feeds fixed stub inputs and compares the **whole** output dict
against a frozen JSON file under ``tests/golden/recommendations/``. The refactor
must leave every file byte-for-byte unchanged; a later PR that changes behaviour
on purpose regenerates them with::

    UPDATE_GOLDEN=1 python -m unittest tests.test_stop_loss_golden

and the diff of the JSON files is the review of that behaviour change.
"""

import json
import os
import unittest
from pathlib import Path

from quantcore.services.recommendations import RecommendationsService
from tests.test_support_confluence import _FULL_OPTIONS, _FULL_PRICES

_GOLDEN_DIR = Path(__file__).parent / "golden" / "recommendations"
_UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


class _Fake:
    """Stub collaborator: canned value per method, or a callable dispatched
    with the call's args. Unknown methods raise AttributeError."""

    def __init__(self, **canned):
        self._canned = canned

    def __getattr__(self, name):
        if name.startswith("_") or name not in self.__dict__.get("_canned", {}):
            raise AttributeError(name)
        value = self._canned[name]
        if callable(value):
            return value
        return lambda *a, **k: value


def _build(prices=None, options=None, microstructure=None, sentiment=None,
           fundamentals=None, yf=None) -> RecommendationsService:
    return RecommendationsService(
        prices=_Fake(**(prices or {})),
        options=_Fake(**(options or {})),
        microstructure=_Fake(**(microstructure or {})),
        sentiment=_Fake(**(sentiment or {})),
        fundamentals=_Fake(**(fundamentals or {})),
        ohlcv_repository=_Fake(),
        yfinance_gateway=_Fake(close_thread_caches=None, **(yf or {})),
    )


_DRAWDOWN = {
    "trailing_stop_pct": 6.5,
    "max_1day_drawdown_pct": -6.1,
    "max_5day_drawdown_pct": -11.4,
    "max_intraday_drop_pct": -7.8,
    "recent_max_1day_pct": -4.2,
}


def _stop_prices(vwap=175.8, position="above_vwap", bars=8, rsi=61.0,
                 crossover="bullish", bb=(190.0, 176.0, 162.0), drawdown=None):
    upper, middle, lower = bb
    return {
        "get_stock_price": {
            "price": 180.0,
            "bollinger_bands": {"upper": upper, "middle": middle, "lower": lower},
        },
        "get_vwap": {"vwap": vwap, "position": position,
                     "consecutive_bars_above": bars, "consecutive_bars_below": bars},
        "get_macd": {"crossover": crossover},
        "get_rsi": {"rsi": rsi},
        "get_historical_drawdown": dict(drawdown or _DRAWDOWN),
    }


def _si(short_float, ratio):
    return {"info": {"shortPercentOfFloat": short_float, "shortRatio": ratio}}


# name -> (builder kwargs, get_stop_loss_analysis kwargs)
_STOP_SCENARIOS = {
    "stop_gamma_wall_support": (
        dict(prices=_stop_prices(),
             options={"get_delta_adjusted_oi": {"gamma_wall_strike": 178.0}},
             yf=_si(0.05, 2.0)),
        {},
    ),
    "stop_no_supports_bb_fallback": (
        dict(prices=_stop_prices(vwap=185.0, position="below_vwap", rsi=30.0,
                                 crossover="bearish", bb=(200.0, 190.0, 181.0)),
             yf=_si(0.02, 1.0)),
        {},
    ),
    "stop_high_si_uptrend": (
        dict(prices=_stop_prices(rsi=75.0, crossover="bullish_crossover"),
             options={"get_delta_adjusted_oi": {"gamma_wall_strike": 172.5}},
             yf=_si(0.22, 5.4)),
        {},
    ),
    "stop_high_si_downtrend": (
        dict(prices=_stop_prices(vwap=182.0, position="below_vwap",
                                 crossover="bearish_crossover",
                                 drawdown={**_DRAWDOWN, "recent_max_1day_pct": -9.5}),
             yf=_si(0.16, 4.0)),
        {},
    ),
    "stop_with_position": (
        dict(prices=_stop_prices(),
             options={"get_delta_adjusted_oi": {"gamma_wall_strike": 172.5}},
             yf=_si(0.12, 3.0)),
        dict(cost_basis=142.5, shares=120),
    ),
    "stop_outside_noise_floor_cost_basis_only": (
        dict(prices=_stop_prices(drawdown={**_DRAWDOWN, "max_1day_drawdown_pct": -1.5,
                                           "recent_max_1day_pct": -1.0}),
             options={"get_delta_adjusted_oi": {"gamma_wall_strike": 172.5}},
             yf=_si(0.0, 0.0)),
        dict(cost_basis=150.0),
    ),
}

_TRADE_REC = dict(
    prices={
        "get_stock_price": {
            "price": 100.0,
            "bollinger_bands": {"upper": 110.0, "middle": 100.0, "lower": 90.0},
            "options": {
                "put_call_ratio": 1.0,
                "calls": {"avg_iv_pct": 20.0,
                          "atm_contracts": [{"strike": 100, "ask": 2.0}]},
                "puts": {"atm_contracts": [{"strike": 100, "ask": 2.0}]},
            },
        },
        "get_rsi": {"rsi": 25.0},
        "get_macd": {"crossover": "bullish_crossover"},
        "get_stochastic": {"k": 20.0},
        "get_volume_analysis": {"bottom_signal": "strong bottom",
                                "obv_divergence": True, "climax_events": []},
        "get_candlestick_patterns": {"patterns_found": [
            {"pattern": "hammer", "bias": "bullish",
             "strength": "strong", "strength_score": 5}]},
        "get_vwap": {"vwap": 98.0, "position": "above_vwap",
                     "consecutive_bars_above": 4},
        "get_historical_drawdown": {"trailing_stop_pct": 8.0,
                                    "max_1day_drawdown_pct": -5,
                                    "max_5day_drawdown_pct": -10,
                                    "max_intraday_drop_pct": -6,
                                    "recent_max_1day_pct": -4},
    },
    options={
        "get_unusual_calls": {"sweep_signal": "strong"},
        "get_delta_adjusted_oi": {"signal": "strong", "mm_hedge_bias": "buy_on_dip",
                                  "gamma_wall_strike": 95.0,
                                  "net_daoi_shares": 100_000},
    },
    microstructure={
        "get_short_interest": {"squeeze_potential": "LOW"},
        "get_dark_pool": {"net_signal": "accumulation"},
        "get_bid_ask_spread": {"spread_vs_norm": "narrowing"},
    },
    fundamentals={
        "get_earnings_calendar": {"days_to_earnings": 30, "risk_level": "LOW",
                                  "pre_earnings_setup": False},
        "get_fundamental_score": {"composite_score": 9, "fundamental_label": "elite"},
        "get_revenue_growth": {"trajectory": "accelerating"},
        "get_earnings_acceleration": {"acceleration_score": 2,
                                      "acceleration_label": "accelerating"},
    },
    sentiment={"get_news": {"sentiment_summary": {
        "scored_count": 5, "positive_count": 4, "negative_count": 0}}},
    yf={"info": {"sector": "Technology", "shortPercentOfFloat": 0.05,
                 "shortRatio": 2}},
)


class GoldenOutputTests(unittest.TestCase):
    maxDiff = None

    def _check(self, name: str, result: dict) -> None:
        # Round-trip through JSON so tuples/ints compare the way the file stores them.
        actual = json.loads(json.dumps(result, sort_keys=True, default=str))
        path = _GOLDEN_DIR / f"{name}.json"
        if _UPDATE:
            _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
            return
        self.assertTrue(path.exists(), f"missing golden file {path}; run with UPDATE_GOLDEN=1")
        self.assertEqual(actual, json.loads(path.read_text()))

    def test_stop_loss_scenarios(self):
        for name, (build_kwargs, call_kwargs) in _STOP_SCENARIOS.items():
            with self.subTest(name):
                result = _build(**build_kwargs).get_stop_loss_analysis("xyz", **call_kwargs)
                self._check(name, result)

    def test_support_confluence_all_sources(self):
        svc = _build(prices=dict(_FULL_PRICES), options=dict(_FULL_OPTIONS))
        self._check("confluence_all_sources", svc.get_support_confluence("xyz"))

    def test_support_confluence_options_failed(self):
        svc = _build(prices=dict(_FULL_PRICES), options={})
        self._check("confluence_options_failed", svc.get_support_confluence("xyz"))

    def test_trade_recommendation(self):
        rec = _build(**_TRADE_REC).get_trade_recommendation("XYZ", capital=5000.0)
        self._check("trade_recommendation_bull", rec)


if __name__ == "__main__":
    unittest.main()
