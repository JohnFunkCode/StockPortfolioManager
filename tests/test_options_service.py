"""Unit tests for OptionsService's analytics surfaces (wave 2 coverage).

Everything is driven by literal chain fixtures with analytically known
outcomes: a 2.5x vol/OI call filled at the ask 8% OTM MUST score a strong
sweep; a put-heavy book MUST flip market makers to buy-on-rally; the ATM
straddle at 100 with last prices 4 + 3.5 MUST read a 7.5% expected move.
Gateway/repositories are Mocks; the Polygon-backed backfill and the
refresh orchestrator are integration surfaces left to wave 3.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from quantcore.services.options import OptionsService

TODAY_EXP = "2026-08-14"  # ~30 days out from the July 2026 test runs


def call_row(strike, volume, oi, last, bid, ask, iv=0.45, itm=False):
    return {
        "strike": float(strike), "volume": volume, "openInterest": oi,
        "lastPrice": last, "bid": bid, "ask": ask,
        "impliedVolatility": iv, "inTheMoney": itm,
    }


def chain_frames(calls_rows, puts_rows=()):
    cols = ["strike", "volume", "openInterest", "lastPrice", "bid", "ask",
            "impliedVolatility", "inTheMoney"]
    calls = pd.DataFrame(list(calls_rows), columns=cols)
    puts = pd.DataFrame(list(puts_rows), columns=cols)
    return SimpleNamespace(calls=calls, puts=puts)


class OptionsServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.yf = Mock()
        self.options = Mock()
        self.service = OptionsService(
            ohlcv_repository=Mock(),
            yfinance_gateway=self.yf,
            options_repository=self.options,
            polygon_gateway=Mock(),
            prices=Mock(),
        )

    def arm_price(self, price=100.0, expirations=(TODAY_EXP,)):
        self.yf.fast_info.return_value = SimpleNamespace(last_price=price)
        self.yf.expirations.return_value = tuple(expirations)


class TestUnusualCalls(OptionsServiceTestBase):
    def test_aggressive_otm_sweep_scores_strong(self):
        self.arm_price()
        self.yf.option_chain.return_value = chain_frames([
            # 2.5x vol/OI (+3), last >= ask (+2), 8% OTM (+2) => score 7.
            call_row(108, volume=500, oi=200, last=2.60, bid=2.40, ask=2.50),
        ])
        out = self.service.get_unusual_calls("intc")
        self.assertEqual(out["sweep_signal"], "strong")
        top = out["unusual_calls"][0]
        self.assertEqual(top["sweep_score"], 7)
        self.assertEqual(top["conviction"], "very high")
        self.assertIn("aggressive sweep fill", " ".join(top["notes"]))

    def test_itm_hedge_flow_reads_weak(self):
        self.arm_price()
        self.yf.option_chain.return_value = chain_frames([
            # High vol/OI but ITM and filled below mid: hedge, not a sweep.
            call_row(90, volume=300, oi=100, last=9.0, bid=9.5, ask=10.5, itm=True),
        ])
        out = self.service.get_unusual_calls("INTC")
        self.assertEqual(out["sweep_signal"], "weak")
        self.assertEqual(out["unusual_calls"][0]["conviction"], "low")
        self.assertIn("hedge", " ".join(out["unusual_calls"][0]["notes"]))

    def test_thresholds_filter_everything_out(self):
        self.arm_price()
        self.yf.option_chain.return_value = chain_frames([
            call_row(105, volume=50, oi=1000, last=1.0, bid=0.9, ask=1.1),
        ])
        out = self.service.get_unusual_calls("INTC")
        self.assertEqual(out["sweep_signal"], "none")
        self.assertEqual(out["unusual_call_count"], 0)

    def test_no_expirations_and_bad_price(self):
        self.arm_price(expirations=())
        out = self.service.get_unusual_calls("INTC")
        self.assertEqual(out["sweep_signal"], "none")
        self.yf.fast_info.return_value = SimpleNamespace(last_price=None)
        with self.assertRaises(ValueError):
            self.service.get_unusual_calls("INTC")


class TestDeltaAdjustedOi(OptionsServiceTestBase):
    def test_put_heavy_book_flips_mm_to_buy_on_rally_strong(self):
        self.arm_price()
        self.yf.option_chain.return_value = chain_frames(
            calls_rows=[call_row(100, volume=0, oi=100, last=4, bid=3.9, ask=4.1, iv=0.3)],
            puts_rows=[call_row(100, volume=0, oi=40_000, last=3.5, bid=3.4, ask=3.6, iv=0.3)],
        )
        out = self.service.get_delta_adjusted_oi("intc", max_expirations=1)
        self.assertLess(out["net_daoi_shares"], 0)
        self.assertEqual(out["mm_hedge_bias"], "buy_on_rally")
        self.assertEqual(out["signal"], "strong")     # near flip (same strike) + big magnitude
        self.assertEqual(out["delta_flip_strike"], 100.0)
        self.assertEqual(out["gamma_wall_strike"], 100.0)
        self.assertEqual(out["gamma_wall_method"], "bs_gamma_oi")
        self.options.save_gamma_wall.assert_called_once()

    def test_call_heavy_book_reads_sell_on_rally_none(self):
        self.arm_price()
        self.yf.option_chain.return_value = chain_frames(
            calls_rows=[call_row(100, volume=0, oi=40_000, last=4, bid=3.9, ask=4.1, iv=0.3)],
        )
        out = self.service.get_delta_adjusted_oi("INTC", max_expirations=1)
        self.assertEqual(out["mm_hedge_bias"], "sell_on_rally")
        self.assertEqual(out["signal"], "none")

    def test_no_expirations_short_circuits(self):
        self.arm_price(expirations=())
        out = self.service.get_delta_adjusted_oi("INTC")
        self.assertEqual(out["signal"], "none")
        self.options.save_gamma_wall.assert_not_called()


class TestRepoBackedSurfaces(OptionsServiceTestBase):
    def test_gamma_wall_history_notes(self):
        self.options.get_gamma_wall_history.return_value = [{"d": 1}]
        out = self.service.get_gamma_wall_history("intc")
        self.assertEqual(out["data_points"], 1)
        self.assertIn("One row per calendar day", out["note"])
        self.options.get_gamma_wall_history.return_value = []
        out = self.service.get_gamma_wall_history("INTC")
        self.assertIn("No history yet", out["note"])

    def test_options_latest(self):
        self.options.get_latest_snapshot.return_value = None
        self.assertIsNone(self.service.get_options_latest("intc")["snapshot"])
        self.options.get_latest_snapshot.return_value = {"price": 1}
        self.assertEqual(self.service.get_options_latest("INTC")["snapshot"], {"price": 1})

    def test_options_history_collapses_intraday_rows(self):
        self.options.get_pc_history.return_value = [
            {"captured_at": "2026-07-14T10:00:00Z", "price": 99.0, "put_call_ratio": 1.0},
            {"captured_at": "2026-07-14T15:00:00Z", "price": 101.0, "put_call_ratio": 2.0},
            {"captured_at": "2026-07-13T15:00:00Z", "price": 98.0, "put_call_ratio": None},
        ]
        out = self.service.get_options_history("INTC")
        self.assertEqual(len(out["history"]), 2)
        d13, d14 = out["history"][0], out["history"][1]
        self.assertIsNone(d13["put_call_ratio"])
        self.assertEqual(d14["put_call_ratio"], 1.5)      # intraday average
        self.assertEqual(d14["price"], 101.0)             # latest row wins
        self.assertEqual(d14["captured_at"], "2026-07-14T15:00:00Z")

    def full_chain_fixture(self):
        contracts = [
            {"kind": "call", "strike": 95.0, "open_interest": 100, "last_price": 7.0},
            {"kind": "call", "strike": 100.0, "open_interest": 500, "last_price": 4.0},
            {"kind": "call", "strike": 105.0, "open_interest": 100, "last_price": 2.0},
            {"kind": "put", "strike": 95.0, "open_interest": 100, "last_price": 1.5},
            {"kind": "put", "strike": 100.0, "open_interest": 500, "last_price": 3.5},
            {"kind": "put", "strike": 105.0, "open_interest": 100, "last_price": 6.0},
        ]
        return {
            "price": 100.0,
            "captured_at": "2026-07-14T15:00:00Z",
            "expirations": [{
                "expiration": TODAY_EXP,
                "contracts": contracts,
                "total_call_oi": 700, "total_put_oi": 700, "put_call_ratio": 1.0,
            }],
        }

    def test_options_analytics_max_pain_and_expected_move(self):
        self.options.get_full_chain.return_value = self.full_chain_fixture()
        out = self.service.get_options_analytics("intc")
        row = out["analytics"][0]
        self.assertEqual(row["max_pain"], 100.0)          # OI concentrated at 100
        self.assertEqual(row["atm_strike"], 100.0)
        self.assertEqual(row["expected_move_dollar"], 7.5)  # 4.0 + 3.5 straddle
        self.assertEqual(row["expected_move_pct"], 7.5)
        self.assertEqual(row["upper_bound"], 107.5)
        self.assertEqual(row["lower_bound"], 92.5)
        self.assertTrue(row["pain_curve"])

    def test_options_analytics_without_chain(self):
        self.options.get_full_chain.return_value = None
        out = self.service.get_options_analytics("INTC")
        self.assertIsNone(out["analytics"])

    def test_options_chain_expiration_filter(self):
        chain = self.full_chain_fixture()
        chain["expirations"].append({"expiration": "2026-09-18", "contracts": []})
        self.options.get_full_chain.return_value = chain
        out = self.service.get_options_chain("INTC", expiration=TODAY_EXP)
        self.assertEqual(len(out["chain"]["expirations"]), 1)
        self.assertEqual(out["chain"]["expirations"][0]["expiration"], TODAY_EXP)

    def test_iv_rank_math(self):
        self.options.get_iv_history.return_value = [
            {"composite_iv": 20.0}, {"composite_iv": 60.0},
            {"composite_iv": None}, {"composite_iv": 40.0},
        ]
        out = self.service.get_iv_rank("intc")
        self.assertEqual(out["current_iv"], 40.0)
        self.assertEqual(out["iv_rank"], 50.0)            # (40-20)/(60-20)
        self.assertEqual(out["iv_percentile"], 50.0)      # 1 of 2 past values below
        self.assertEqual(out["iv_52w_high"], 60.0)
        self.assertEqual(out["iv_52w_low"], 20.0)

    def test_iv_rank_insufficient_history(self):
        self.options.get_iv_history.return_value = [{"composite_iv": 33.0}]
        out = self.service.get_iv_rank("INTC")
        self.assertIsNone(out["iv_rank"])
        self.assertEqual(out["current_iv"], 33.0)


class TestFlowSignalsAndPortfolioDelta(OptionsServiceTestBase):
    def test_flow_signals_isolate_failures(self):
        ok = {"sweep_signal": "none"}
        with unittest.mock.patch.object(self.service, "get_unusual_calls",
                                        return_value=ok), \
             unittest.mock.patch.object(self.service, "get_delta_adjusted_oi",
                                        side_effect=RuntimeError("daoi boom")):
            out = self.service.get_options_flow_signals("intc")
        self.assertEqual(out["unusual_calls"], ok)
        self.assertIsNone(out["delta_adjusted_oi"])
        self.assertIn("daoi boom", out["_errors"]["delta_adjusted_oi"])

    def test_portfolio_delta_exposure_put_heavy_position(self):
        chain = {
            "price": 100.0,
            "captured_at": "t",
            "expirations": [{
                "expiration": TODAY_EXP,
                "contracts": [
                    {"kind": "put", "strike": 100.0, "open_interest": 10_000,
                     "implied_vol": 30.0},   # percent form exercises normalization
                ],
            }],
        }
        self.options.get_full_chain.side_effect = lambda sym: (
            chain if sym == "INTC" else None
        )
        out = self.service.get_portfolio_delta_exposure([
            {"symbol": "INTC", "name": "Intel", "quantity": 500},
            {"symbol": "NOCHAIN", "name": "Nothing", "quantity": 1},
        ])
        self.assertEqual(len(out["positions"]), 1)
        pos = out["positions"][0]
        self.assertEqual(pos["stock_delta"], 500.0)
        self.assertLess(pos["net_daoi_shares"], 0)         # puts carry negative delta
        self.assertEqual(pos["mm_hedge_bias"], "buy_on_rally")
        self.assertEqual(out["portfolio_net_daoi"], pos["net_daoi_shares"])


class TestFullChainFetch(OptionsServiceTestBase):
    def test_full_chain_persists_and_summarizes(self):
        self.arm_price()
        prices = self.service._prices
        prices.get_history.return_value = pd.DataFrame(
            {"Close": [100.0] * 30},
            index=pd.bdate_range(end="2026-07-14", periods=30),
        )
        self.yf.option_chain.return_value = chain_frames(
            calls_rows=[call_row(100, volume=10, oi=100, last=4, bid=3.9, ask=4.1)],
            puts_rows=[call_row(100, volume=10, oi=200, last=3.5, bid=3.4, ask=3.6)],
        )
        self.options.save_full_chain.return_value = 42
        out = self.service.get_full_options_chain("intc")
        self.assertEqual(out["snapshot_id"], 42)
        self.assertTrue(out["persisted"])
        self.assertEqual(out["expiration_count"], 1)
        self.assertEqual(out["total_contracts"], 2)
        self.assertEqual(out["expirations"], [TODAY_EXP])
        self.assertIsNotNone(out["bollinger_bands"])

    def test_full_chain_duplicate_snapshot_flagged(self):
        self.arm_price(expirations=())
        prices = self.service._prices
        prices.get_history.return_value = pd.DataFrame(
            {"Close": [100.0] * 10},
            index=pd.bdate_range(end="2026-07-14", periods=10),
        )
        out = self.service.get_full_options_chain("INTC")
        self.assertEqual(out["expiration_count"], 0)
        self.assertEqual(out["total_contracts"], 0)


if __name__ == "__main__":
    unittest.main()
