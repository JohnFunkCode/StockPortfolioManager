"""Unit tests for OptionsScreeningService — the watchlist options screener.

Per the Phase 1 plan, the analytics extracted from ``options_analysis.py`` move
into ``quantcore.services.options_screening``. These tests are fully offline —
no DB, no yfinance — exercising the pure scoring/guardrail/ranking logic by
constructing ``SecurityAnalysis`` objects directly and feeding dicts to the
budget allocator. The repository/gateway collaborators are unused by these code
paths, so trivial stubs are injected.

The live-fetch methods (``fetch_security``, ``analyze_watchlist``,
``analyze_symbol``) hit yfinance and are covered by the manual parity diffs
called for in the plan's testing strategy, not here.
"""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quantcore.services.options_screening import (
    OptionsScreeningService,
    SecurityAnalysis,
    BollingerBands,
    OptionsSummary,
    IVAnalysis,
    PutCallAnalysis,
    CONVICTION_WEIGHT,
    ROI_WEIGHT,
    ROI_CAP_FOR_RANKING,
    MAX_PUT_SCORE,
    _chain_pc,
)


class _Stub:
    """Inert collaborator — the pure code paths never touch it."""


def _svc() -> OptionsScreeningService:
    return OptionsScreeningService(ohlcv_repository=_Stub(), yfinance_gateway=_Stub())


def _security(**overrides) -> SecurityAnalysis:
    """A neutral mid-band security with no options; override to shape signals."""
    data = dict(
        symbol="XYZ",
        name="XYZ Corp",
        tags=[],
        price=100.0,
        bands=BollingerBands(upper=110.0, middle=100.0, lower=90.0),
        options=None,
    )
    data.update(overrides)
    return SecurityAnalysis(**data)


def _options(**overrides) -> OptionsSummary:
    data = dict(
        expiration="2026-07-17",
        put_call_ratio=1.0,
        total_call_oi=1000,
        total_put_oi=1000,
        total_call_volume=1000,
        total_put_volume=1000,
        avg_call_iv=30.0,
        avg_put_iv=30.0,
    )
    data.update(overrides)
    return OptionsSummary(**data)


class ScoreTests(unittest.TestCase):
    def test_oversold_bullish_options_drive_long_score(self):
        svc = _svc()
        sec = _security(
            price=88.0,  # below lower band (90) → oversold +3
            options=_options(put_call_ratio=0.4, total_call_volume=60_000),  # very bullish +3, huge vol +1
            iv=IVAnalysis(current_iv=80, hv_30=40, iv_vs_hv=2.0, hv_52w_low=20,
                          hv_52w_high=80, iv_rank=85.0, iv_percentile=95.0,
                          label="extreme fear"),  # IV rank >=80 → +3
        )
        svc.score(sec)
        # 3 (BB) + 3 (P/C) + 1 (call vol) + 3 (IV rank) = 10
        self.assertEqual(sec.long_score, 10)
        self.assertEqual(sec.put_score, 0)
        self.assertIn("below lower BB", sec.long_reason)

    def test_overbought_bearish_options_drive_put_score(self):
        svc = _svc()
        sec = _security(
            price=112.0,  # above upper band (110) → overbought +3
            options=_options(put_call_ratio=2.5, total_put_oi=60_000, total_call_oi=1000),
            iv=IVAnalysis(current_iv=10, hv_30=15, iv_vs_hv=0.7, hv_52w_low=10,
                          hv_52w_high=60, iv_rank=8.0, iv_percentile=5.0,
                          label="complacent"),  # IV rank <=10 → +3
        )
        svc.score(sec)
        # 3 (BB) + 3 (P/C >2.0) + 1 (put OI > call OI) + 1 (massive put OI) + 3 (IV rank<=10) = 11
        self.assertEqual(sec.put_score, 11)
        self.assertEqual(sec.long_score, 0)
        self.assertIn("above upper BB", sec.put_reason)

    def test_news_signal_adjusts_directional_score(self):
        svc = _svc()
        bull = _security(news_signal="BULLISH")
        svc.score(bull)
        self.assertEqual(bull.long_score, 2)
        bear = _security(news_signal="BEARISH")
        svc.score(bear)
        self.assertEqual(bear.put_score, 2)

    def test_midband_no_options_is_no_signal(self):
        svc = _svc()
        sec = _security()
        svc.score(sec)
        self.assertEqual(sec.long_score, 0)
        self.assertEqual(sec.put_score, 0)
        self.assertEqual(sec.long_reason, "no signal")


class GuardrailTests(unittest.TestCase):
    def test_earnings_blackout_blocks_puts_and_calls(self):
        svc = _svc()
        sec = _security(days_to_earnings=3)
        self.assertIn("earnings in 3d", svc.put_guardrail_reason(sec))
        self.assertIn("earnings in 3d", svc.call_guardrail_reason(sec))

    def test_positive_catalyst_blocks_puts_only(self):
        svc = _svc()
        sec = _security(recent_positive_catalyst=True,
                        catalyst_headline="Analyst upgrade to Buy")
        self.assertIn("positive catalyst", svc.put_guardrail_reason(sec))
        self.assertEqual(svc.call_guardrail_reason(sec), "")

    def test_contradiction_guard_blocks_puts(self):
        svc = _svc()
        sec = _security(long_score=4, put_score=4)
        self.assertIn("ambiguous signal", svc.put_guardrail_reason(sec))

    def test_bearish_news_blocks_calls(self):
        svc = _svc()
        sec = _security(news_signal="BEARISH", news_top_headline="Guidance cut")
        self.assertIn("BEARISH news signal", svc.call_guardrail_reason(sec))

    def test_clean_security_has_no_guardrail(self):
        svc = _svc()
        sec = _security()
        self.assertEqual(svc.put_guardrail_reason(sec), "")
        self.assertEqual(svc.call_guardrail_reason(sec), "")


class RankingAndBudgetTests(unittest.TestCase):
    def _trade(self, **overrides) -> dict:
        data = dict(symbol="XYZ", strike=100, expiration="2026-07-17",
                    ask=2.0, roi_at_target_pct=100.0, put_score=10, long_score=10)
        data.update(overrides)
        return data

    def test_combined_put_rank_blends_conviction_and_roi(self):
        svc = _svc()
        t = self._trade(roi_at_target_pct=ROI_CAP_FOR_RANKING, put_score=MAX_PUT_SCORE)
        # Both terms normalise to 1.0 → full weight sum.
        self.assertAlmostEqual(svc.combined_put_rank_score(t),
                               CONVICTION_WEIGHT + ROI_WEIGHT)

    def test_roi_is_capped_in_ranking(self):
        svc = _svc()
        capped = svc.combined_put_rank_score(self._trade(roi_at_target_pct=ROI_CAP_FOR_RANKING))
        over = svc.combined_put_rank_score(self._trade(roi_at_target_pct=ROI_CAP_FOR_RANKING * 5))
        self.assertEqual(capped, over)

    def test_greedy_fill_respects_budget_and_ranks(self):
        svc = _svc()
        trades = [
            self._trade(symbol="HI", ask=2.0, roi_at_target_pct=200.0, put_score=MAX_PUT_SCORE),
            self._trade(symbol="LO", ask=2.0, roi_at_target_pct=10.0, put_score=1),
        ]
        selected = svc.greedy_fill(trades, total_budget=300.0, rank_fn=svc.combined_put_rank_score)
        # Highest-ranked trade is filled first.
        self.assertEqual(selected[0]["symbol"], "HI")
        self.assertLessEqual(sum(t["total_cost"] for t in selected), 300.0)
        for t in selected:
            self.assertGreaterEqual(t["contracts"], 1)
            self.assertIn("rank_score", t)

    def test_greedy_fill_skips_unaffordable(self):
        svc = _svc()
        trades = [self._trade(ask=10.0)]  # one contract = $1000 > budget
        selected = svc.greedy_fill(trades, total_budget=300.0, rank_fn=svc.combined_put_rank_score)
        self.assertEqual(selected, [])


class WatchlistHelperTests(unittest.TestCase):
    def test_is_us_listed_filters_foreign_suffixes(self):
        svc = _svc()
        self.assertTrue(svc.is_us_listed("AAPL"))
        self.assertFalse(svc.is_us_listed("BMW.DE"))
        self.assertFalse(svc.is_us_listed("equnr.ol"))

    def test_load_watchlist_parses_symbols_names_tags(self):
        svc = _svc()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "wl.yaml"
            path.write_text(
                "- symbol: AAPL\n"
                "  name: Apple\n"
                "  tags: [tech, core]\n"
                "- symbol: MSFT\n"  # no name → falls back to symbol
                "- name: Nameless\n"  # no symbol → dropped
            )
            entries = svc.load_watchlist(path)
        self.assertEqual([e["symbol"] for e in entries], ["AAPL", "MSFT"])
        self.assertEqual(entries[0]["tags"], ["tech", "core"])
        self.assertEqual(entries[1]["name"], "MSFT")


# ---------------------------------------------------------------------------
# Wave 3 additions: band math, chain P/C helper, PutCallAnalysis-driven score
# paths, and the trade builders (previously untested).
# ---------------------------------------------------------------------------


def _pc(**overrides) -> PutCallAnalysis:
    data = dict(near_expiry="2026-07-17", near_oi_pc=1.0, near_vol_pc=1.0,
                near_atm_pc=None, mid_expiry=None, mid_oi_pc=None,
                term_skew=None, vol_oi_ratio=1.0,
                put_unwinding=False, fresh_put_buying=False,
                near_term_fear=False)
    data.update(overrides)
    return PutCallAnalysis(**data)


class BandMathTests(unittest.TestCase):
    def test_position_normalization_and_breakouts(self):
        b = BollingerBands(upper=110.0, middle=100.0, lower=90.0)
        self.assertEqual(b.position(90.0), 0.0)
        self.assertEqual(b.position(100.0), 0.5)
        self.assertEqual(b.position(110.0), 1.0)
        self.assertGreater(b.position(115.0), 1.0)
        self.assertAlmostEqual(b.pct_from_lower(94.5), 5.0)
        self.assertAlmostEqual(b.pct_from_upper(104.5), -5.0)

    def test_zero_width_bands_read_neutral(self):
        b = BollingerBands(upper=100.0, middle=100.0, lower=100.0)
        self.assertEqual(b.position(100.0), 0.5)


class ChainPcTests(unittest.TestCase):
    def test_oi_and_volume_ratios(self):
        calls = pd.DataFrame({"strike": [95.0, 100.0], "openInterest": [100, 100],
                              "volume": [10, 10]})
        puts = pd.DataFrame({"strike": [95.0, 100.0], "openInterest": [200, 200],
                             "volume": [40, 40]})
        oi_pc, vol_pc, _ = _chain_pc(calls, puts, price=100.0)
        self.assertEqual(oi_pc, 2.0)
        self.assertEqual(vol_pc, 4.0)

    def test_missing_or_empty_frames(self):
        calls = pd.DataFrame({"strike": [100.0], "openInterest": [1], "volume": [1]})
        self.assertEqual(_chain_pc(calls, pd.DataFrame(), 100.0), (None, None, None))
        self.assertEqual(_chain_pc(None, None, 100.0), (None, None, None))


class PutCallScorePathTests(unittest.TestCase):
    def test_put_unwinding_and_fear_skew_add_long_points(self):
        svc = _svc()
        sec = _security(pc=_pc(
            near_oi_pc=1.6, near_vol_pc=1.0,
            put_unwinding=True,                          # +2
            near_term_fear=True, mid_oi_pc=1.1, term_skew=0.5,   # +1
            near_atm_pc=1.0,                             # < 0.85 x total: +1
        ))
        svc.score(sec)
        self.assertEqual(sec.long_score, 4)
        self.assertIn("put unwinding", sec.long_reason)
        self.assertIn("near-term fear", sec.long_reason)
        self.assertIn("near-money calls", sec.long_reason)

    def test_fresh_put_buying_and_atm_hedging_add_put_points(self):
        svc = _svc()
        sec = _security(pc=_pc(
            near_oi_pc=1.0, near_vol_pc=2.0,
            fresh_put_buying=True,                       # +2
            near_atm_pc=1.5,                             # > 1.2 x total: +1
        ))
        svc.score(sec)
        self.assertEqual(sec.put_score, 3)
        self.assertIn("fresh put buying", sec.put_reason)
        self.assertIn("targeted hedging", sec.put_reason)


ATM_PUT = {"strike": 100.0, "ask": 3.0, "iv": 40.0}
ATM_CALL = {"strike": 100.0, "ask": 3.0, "iv": 40.0}


class BuildPutTradeTests(unittest.TestCase):
    def bearish(self, **overrides):
        sec = _security(
            options=_options(atm_puts=[dict(ATM_PUT)], put_call_ratio=2.2,
                             total_put_oi=5_000),
            **overrides,
        )
        sec.put_score = 8.0
        return sec

    def test_happy_path_targets_the_lower_band(self):
        trade = _svc().build_put_trade(self.bearish())
        self.assertEqual(trade["strike"], 100.0)
        self.assertEqual(trade["total_cost"], 300.0)            # 1 x $3.00 x 100
        self.assertEqual(trade["target_price"], 90.0)           # the lower band
        self.assertEqual(trade["profit_at_target_per_contract"], 700.0)  # (10-3)x100
        self.assertAlmostEqual(trade["roi_at_target_pct"], 233.3, places=1)
        self.assertFalse(trade["suggest_spread"])

    def test_breakdown_extends_target_below_the_band(self):
        trade = _svc().build_put_trade(self.bearish(price=85.0))
        self.assertEqual(trade["target_price"], 85.5)           # lower band x 0.95

    def test_high_iv_suggests_a_spread(self):
        sec = self.bearish()
        sec.options.atm_puts[0]["iv"] = 70.0
        self.assertTrue(_svc().build_put_trade(sec)["suggest_spread"])

    def test_guardrails_veto_the_trade(self):
        svc = _svc()
        self.assertIsNone(svc.build_put_trade(self.bearish(days_to_earnings=5)))
        self.assertIsNone(svc.build_put_trade(
            self.bearish(recent_positive_catalyst=True)))
        contradicted = self.bearish()
        contradicted.long_score = 4.0
        contradicted.put_score = 4.0
        self.assertIsNone(svc.build_put_trade(contradicted))

    def test_economic_vetoes(self):
        svc = _svc()
        rich = self.bearish()
        rich.options.atm_puts[0]["ask"] = 15.0                  # $1500 > $1000 budget
        self.assertIsNone(svc.build_put_trade(rich, total_budget=1000.0))

        no_edge = self.bearish(
            bands=BollingerBands(upper=110.0, middle=104.0, lower=98.0)
        )
        self.assertIsNone(svc.build_put_trade(no_edge))         # intrinsic 2 < ask 3

        crossed = self.bearish()
        crossed.options.atm_puts[0]["ask"] = 0.0
        self.assertIsNone(svc.build_put_trade(crossed))

        self.assertIsNone(svc.build_put_trade(_security()))     # no puts at all


class BuildCallTradeTests(unittest.TestCase):
    def bullish(self, **overrides):
        sec = _security(
            options=_options(atm_calls=[dict(ATM_CALL)], put_call_ratio=0.5),
            **overrides,
        )
        sec.long_score = 8.0
        return sec

    def test_happy_path_targets_the_upper_band(self):
        trade = _svc().build_call_trade(self.bullish())
        self.assertEqual(trade["target_price"], 110.0)
        self.assertEqual(trade["profit_at_target_per_contract"], 700.0)
        self.assertEqual(trade["long_score"], 8.0)

    def test_breakout_extends_target_above_the_band(self):
        trade = _svc().build_call_trade(self.bullish(price=112.0))
        self.assertEqual(trade["target_price"], 115.5)          # upper band x 1.05

    def test_earnings_blackout_applies_but_catalyst_supports(self):
        svc = _svc()
        self.assertIsNone(svc.build_call_trade(self.bullish(days_to_earnings=3)))
        # A positive catalyst SUPPORTS the call thesis — trade still builds.
        self.assertIsNotNone(
            svc.build_call_trade(self.bullish(recent_positive_catalyst=True))
        )

    def test_economic_vetoes(self):
        svc = _svc()
        rich = self.bullish()
        rich.options.atm_calls[0]["ask"] = 15.0
        self.assertIsNone(svc.build_call_trade(rich, total_budget=1000.0))
        no_edge = self.bullish(
            bands=BollingerBands(upper=102.0, middle=96.0, lower=90.0)
        )
        self.assertIsNone(svc.build_call_trade(no_edge))        # intrinsic 2 < ask 3


if __name__ == "__main__":
    unittest.main()
