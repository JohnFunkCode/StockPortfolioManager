"""Service tests for the stop-loss additions of #399 PR 2: the opt-in confluence
stop, the drawdown replay, the shared gamma-wall fetch, and ``owner`` position
lookup (docs/proposals/stop-loss-card-plan.md).

Offline, with the stub collaborators of the confluence and golden tests. The
fixture's price is 100 and its confluence zones sit at 99.4 (2 methods),
97.71 (2), 95.0 (6), 93.0 (2) and 90.32 (2).
"""

import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from quantcore.services import recommendations as rec_module
from quantcore.services.prices import PricesService
from tests.test_stop_loss_golden import _DRAWDOWN, _Fake, _build, _si
from tests.test_prices_service import bars
from tests.test_support_confluence import _FULL_OPTIONS, _FULL_PRICES, _price

# Noise floors that make the nearest zone's stop (98.21, −1.79%) sit inside
# it (6.1%) or clear it (1.5%).
_WIDE_NOISE = dict(_DRAWDOWN)
_NARROW_NOISE = {**_DRAWDOWN, "max_1day_drawdown_pct": -1.5}


def _prices(drawdown=None, **overrides):
    prices = {
        **_FULL_PRICES,
        "get_vwap": {"vwap": 98.0, "position": "above_vwap", "consecutive_bars_above": 3},
        "get_macd": {"crossover": "bullish"},
        "get_rsi": {"rsi": 55.0},
        "get_historical_drawdown": dict(drawdown or _WIDE_NOISE),
    }
    prices.update(overrides)
    return prices


class _Counter:
    """A canned result that counts its calls."""

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.value


class _Lots:
    """PortfolioRepository stand-in recording each lots lookup."""

    def __init__(self, lots):
        self.lots = lots
        self.calls = []

    def list_lots_for_symbol(self, owner, ticker, status="OPEN"):
        self.calls.append((owner, ticker, status))
        return self.lots


def _service(prices=None, options=None, portfolio=None):
    svc = _build(prices=_prices() if prices is None else prices,
                 options=_FULL_OPTIONS if options is None else options,
                 yf=_si(0.05, 2.0))
    svc._portfolio = portfolio
    return svc


class ConfluenceModeTests(unittest.TestCase):
    def test_none_skips_the_confluence_sources(self):
        gex = _Counter(_FULL_OPTIONS["get_gex_profile"])
        result = _service(options={**_FULL_OPTIONS, "get_gex_profile": gex}) \
            .get_stop_loss_analysis("xyz")
        self.assertEqual(gex.calls, 0)
        self.assertNotIn("confluence_stop", result["stops"])
        self.assertNotIn("confluence", result)
        self.assertTrue(all(r["hits_confluence"] is None for r in result["drawdown_replay"]))

    def test_stop_steps_past_zones_inside_the_noise_floor(self):
        result = _service().get_stop_loss_analysis("xyz", confluence="stop")
        stop = result["stops"]["confluence_stop"]
        self.assertEqual((stop["center"], stop["stop"]), (95.0, 93.74))
        self.assertTrue(stop["stepped_past_noise"])
        self.assertIn("$99.40, $97.71", result["stops"]["confluence_stop_reason"])
        self.assertNotIn("confluence", result)   # only "full" embeds it

    def test_nearest_zone_when_it_clears_the_noise_floor(self):
        result = _service(_prices(_NARROW_NOISE)).get_stop_loss_analysis(
            "xyz", confluence="stop")
        stop = result["stops"]["confluence_stop"]
        self.assertEqual((stop["center"], stop["stop"], stop["method_count"]),
                         (99.4, 98.21, 2))
        self.assertFalse(stop["stepped_past_noise"])
        self.assertIsNone(result["stops"]["confluence_stop_reason"])

    def test_full_embeds_the_confluence_result_with_the_stop_zone_marked(self):
        svc = _service()
        result = svc.get_stop_loss_analysis("xyz", confluence="full")
        embedded = result["confluence"]
        marked = [z for z in embedded["support_zones"] if z.get("is_stop_zone")]
        self.assertEqual([z["center"] for z in marked], [95.0])

        # Apart from the marker, it is exactly what /support-confluence returns.
        standalone = svc.get_support_confluence("xyz")
        unmarked = [{k: v for k, v in z.items() if k != "is_stop_zone"}
                    for z in embedded["support_zones"]]
        self.assertEqual({**embedded, "support_zones": unmarked}, standalone)

    def test_stop_zone_cut_from_the_top_n_is_appended(self):
        with patch.object(rec_module, "_EMBEDDED_MAX_ZONES", 1):
            result = _service(_prices(_NARROW_NOISE)).get_stop_loss_analysis(
                "xyz", confluence="full")
        zones = result["confluence"]["support_zones"]
        self.assertEqual([z["center"] for z in zones], [95.0, 99.4])
        self.assertEqual((zones[1].get("is_stop_zone"), zones[1].get("outside_top")),
                         (True, True))
        self.assertNotIn("is_stop_zone", zones[0])

    def test_replay_judges_the_confluence_stop(self):
        result = _service().get_stop_loss_analysis("xyz", confluence="stop")
        by_key = {r["key"]: r for r in result["drawdown_replay"]}
        # Worst 1-day reaches 93.90: above the 93.74 confluence stop.
        self.assertEqual(by_key["max_1day"]["price"], 93.9)
        self.assertFalse(by_key["max_1day"]["hits_confluence"])
        self.assertTrue(by_key["max_5day"]["hits_confluence"])

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            _service().get_stop_loss_analysis("xyz", confluence="partial")


class SharedGatherTests(unittest.TestCase):
    def test_gamma_wall_is_fetched_once_for_both_computations(self):
        daoi = _Counter({"gamma_wall_strike": 95.0})
        result = _service(options={**_FULL_OPTIONS, "get_delta_adjusted_oi": daoi}) \
            .get_stop_loss_analysis("xyz", confluence="full")
        self.assertEqual(daoi.calls, 1)
        self.assertEqual(result["technical"]["gamma_wall"], 95.0)
        self.assertIn("gamma_wall", result["confluence"]["methods_available"])

    def test_gamma_wall_failure_degrades_both_without_failing(self):
        options = {k: v for k, v in _FULL_OPTIONS.items() if k != "get_delta_adjusted_oi"}
        result = _service(options=options).get_stop_loss_analysis("xyz", confluence="full")
        self.assertIsNone(result["technical"]["gamma_wall"])
        self.assertIn("gamma_wall", result["confluence"]["methods_failed"])
        self.assertIsNotNone(result["stops"]["confluence_stop"])

    def test_no_qualifying_zone_keeps_the_other_stops(self):
        # Only the stop inputs: confluence has VWAP and the Bollinger levels,
        # and no two methods agree on a zone below price.
        prices = {k: v for k, v in _prices().items()
                  if k in ("get_stock_price", "get_vwap", "get_macd", "get_rsi",
                           "get_historical_drawdown")}
        result = _service(prices=prices, options={}).get_stop_loss_analysis(
            "xyz", confluence="stop")
        self.assertIsNone(result["stops"]["confluence_stop"])
        self.assertIn("2 or more methods", result["stops"]["confluence_stop_reason"])
        self.assertEqual(result["stops"]["technical_stop"], 98.4)
        self.assertEqual(result["stops"]["trailing_stop_price"], 93.5)

    def test_missing_required_input_still_raises(self):
        prices = {k: v for k, v in _prices().items() if k != "get_rsi"}
        with self.assertRaises(AttributeError):
            _service(prices=prices).get_stop_loss_analysis("xyz", confluence="full")

    def test_every_worker_closes_its_thread_caches(self):
        closes = _Counter(None)
        svc = _service()
        svc._yf = _Fake(close_thread_caches=closes, **_si(0.05, 2.0))
        svc.get_stop_loss_analysis("xyz", confluence="full")
        self.assertEqual(closes.calls, 6 + 14)   # stop inputs + confluence sources


class BreakdownStopTests(unittest.TestCase):
    """Price under every candidate support, the lower Bollinger band included
    (GEV on test, 2026-10-23: $815 against a $834.53 band, stop $817.84)."""

    def test_technical_stop_stays_below_price(self):
        prices = _prices(
            get_stock_price=_price(bollinger_bands={"upper": 130.0, "middle": 120.0,
                                                    "lower": 105.0}),
            get_vwap={"vwap": 112.0, "position": "below_vwap", "consecutive_bars_below": 8},
        )
        options = {**_FULL_OPTIONS, "get_delta_adjusted_oi": {"gamma_wall_strike": 102.0}}
        result = _service(prices, options).get_stop_loss_analysis("xyz", confluence="stop")
        s, t = result["stops"], result["technical"]
        self.assertEqual((t["primary_support"], t["primary_support_price"]), ("price", 100.0))
        self.assertEqual((s["technical_stop"], s["technical_stop_distance_pct"]), (98.0, -2.0))
        self.assertIn("No support below price", result["summary"])
        # A drop now has to reach the stop to count as hitting it.
        self.assertTrue(all(r["hits_technical"] == (r["price"] <= 98.0)
                            for r in result["drawdown_replay"]))


class OwnerPositionTests(unittest.TestCase):
    _LOTS = [
        {"purchase_price": Decimal("80.00"), "quantity": Decimal("10")},
        {"purchase_price": Decimal("95.00"), "quantity": Decimal("30")},
    ]

    def test_position_comes_from_the_owners_open_lots(self):
        lots = _Lots(self._LOTS)
        result = _service(portfolio=lots).get_stop_loss_analysis("xyz", owner="alice")
        self.assertEqual(lots.calls, [("alice", "XYZ", "OPEN")])
        self.assertEqual(result["position_source"], "lots")
        self.assertEqual(result["position"]["cost_basis"], 91.25)
        self.assertEqual(result["position"]["shares"], 40)
        self.assertEqual(result["position"]["total_unrealized_pnl"], 350.0)

    def test_pnl_at_the_confluence_stop_for_a_held_position(self):
        result = _service(portfolio=_Lots(self._LOTS)).get_stop_loss_analysis(
            "xyz", confluence="stop", owner="alice")
        # 40 shares from 91.25 to the 93.74 confluence stop.
        self.assertEqual(result["position"]["pnl_at_confluence_stop"], 99.6)

    def test_no_confluence_pnl_without_a_confluence_stop_or_a_position(self):
        held = _service(portfolio=_Lots(self._LOTS)).get_stop_loss_analysis(
            "xyz", owner="alice")
        self.assertNotIn("pnl_at_confluence_stop", held["position"])
        unheld = _service(portfolio=_Lots([])).get_stop_loss_analysis(
            "xyz", confluence="stop", owner="alice")
        self.assertEqual(unheld["position"], {})

    def test_explicit_position_wins_and_skips_the_lookup(self):
        lots = _Lots(self._LOTS)
        result = _service(portfolio=lots).get_stop_loss_analysis(
            "xyz", cost_basis=50.0, shares=5, owner="alice")
        self.assertEqual(lots.calls, [])
        self.assertEqual(result["position_source"], "explicit")
        self.assertEqual(result["position"]["cost_basis"], 50.0)

    def test_nothing_held_means_no_position(self):
        result = _service(portfolio=_Lots([])).get_stop_loss_analysis("xyz", owner="alice")
        self.assertEqual(result["position"], {})
        self.assertIsNone(result["position_source"])

    def test_fractional_shares_survive(self):
        lots = _Lots([{"purchase_price": Decimal("90"), "quantity": Decimal("2.5")}])
        result = _service(portfolio=lots).get_stop_loss_analysis("xyz", owner="alice")
        self.assertEqual(result["position"]["shares"], 2.5)

    def test_without_an_owner_the_repository_is_never_read(self):
        lots = _Lots(self._LOTS)
        result = _service(portfolio=lots).get_stop_loss_analysis("xyz")
        self.assertEqual(lots.calls, [])
        self.assertIsNone(result["position_source"])

    def test_owner_without_a_repository_is_a_wiring_error(self):
        with self.assertRaises(RuntimeError):
            _service().get_stop_loss_analysis("xyz", owner="alice")


class RealDrawdownReplayTests(unittest.TestCase):
    """The replay fed by the real ``get_historical_drawdown`` (#418 review): its
    fields are signed worst returns, so a window with only gains is positive."""

    def _drawdown(self, closes):
        prices = PricesService(ohlcv_repository=Mock(), yfinance_gateway=Mock(),
                               options_repository=Mock(), sentiment_repository=Mock())
        with patch.object(prices, "get_history", return_value=bars(closes)):
            return prices.get_historical_drawdown("XYZ", lookback_days=len(closes))

    def test_a_rising_recent_window_replays_as_no_drop(self):
        # An 8% selloff, then 35 sessions each up 0.5%: the last 30 bars hold no loss.
        closes = [100.0] * 20 + [92.0]
        for _ in range(35):
            closes.append(round(closes[-1] * 1.005, 4))
        dd = self._drawdown(closes)
        self.assertGreater(dd["recent_max_1day_pct"], 0)   # the producer's sign

        result = _service(_prices(dd)).get_stop_loss_analysis("xyz")
        rows = {r["key"]: r for r in result["drawdown_replay"]}
        self.assertEqual((rows["recent_1day"]["drop_pct"], rows["recent_1day"]["price"]),
                         (0.0, 100.0))
        self.assertFalse(rows["recent_1day"]["hits_technical"])
        self.assertFalse(rows["recent_1day"]["hits_trailing"])
        # The selloff still replays as the worst 1-day drop.
        self.assertEqual(rows["max_1day"]["drop_pct"], -8.0)
        self.assertEqual(rows["max_1day"]["price"], 92.0)

    def test_an_all_gain_history_hits_nothing(self):
        closes = [round(100.0 * 1.004 ** i, 4) for i in range(40)]
        dd = self._drawdown(closes)
        result = _service(_prices(dd)).get_stop_loss_analysis("xyz", confluence="stop")
        for row in result["drawdown_replay"]:
            with self.subTest(row["key"]):
                if row["key"] == "max_intraday":
                    continue   # high-to-low inside a bar is a real loss even in a rally
                self.assertEqual((row["drop_pct"], row["price"]), (0.0, 100.0))
                self.assertFalse(row["hits_technical"] or row["hits_trailing"]
                                 or row["hits_confluence"])


if __name__ == "__main__":
    unittest.main()
