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

from quantcore.services.options_screening import (
    OptionsScreeningService,
    SecurityAnalysis,
    BollingerBands,
    OptionsSummary,
    IVAnalysis,
    CONVICTION_WEIGHT,
    ROI_WEIGHT,
    ROI_CAP_FOR_RANKING,
    MAX_PUT_SCORE,
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


if __name__ == "__main__":
    unittest.main()
