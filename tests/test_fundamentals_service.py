"""Unit tests for FundamentalsService's pure scoring helpers and the
cache-orchestration surface.

Coverage uplift (July 2026). The `_compute_*` bodies that parse raw yfinance
financial statements are exercised only through their pure extracted helpers
here (statement-shaped fixtures for those internals are a follow-up); the
cache wrappers, batch scorer, profile composer, and rankings are covered
fully with a mocked repository.
"""
import math
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from quantcore.services.fundamentals import (
    FundamentalsService,
    _compute_earnings_acceleration,
    _fcf_margin_3y,
    _get_annual_cfo_and_capex,
    _get_annual_revenue_and_operating_income,
    _get_quarterly_revenue,
    _mom_12_1,
    _op_margin_3y_and_trend,
    _rev_accel,
    _rev_cagr_3y,
    _score_metric,
    _valuation_metric,
)


def series(values):
    return pd.Series([float(v) for v in values],
                     index=pd.period_range("2022", periods=len(values), freq="Y"))


class TestStatementExtractors(unittest.TestCase):
    def test_revenue_and_operating_income(self):
        fin = pd.DataFrame(
            [[100.0, 120.0], [10.0, 18.0]],
            index=["Total Revenue", "Operating Income"],
            columns=pd.to_datetime(["2024-12-31", "2025-12-31"]),
        )
        rev, op = _get_annual_revenue_and_operating_income(fin)
        self.assertEqual(list(rev.values), [100.0, 120.0])
        self.assertEqual(list(op.values), [10.0, 18.0])

    def test_missing_revenue_row_returns_nones(self):
        fin = pd.DataFrame([[1.0]], index=["Something Else"],
                           columns=pd.to_datetime(["2025-12-31"]))
        self.assertEqual(_get_annual_revenue_and_operating_income(fin), (None, None))
        self.assertEqual(_get_annual_revenue_and_operating_income(None), (None, None))

    def test_cfo_capex_label_variants(self):
        cf = pd.DataFrame(
            [[30.0], [10.0]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=pd.to_datetime(["2025-12-31"]),
        )
        cfo, capex = _get_annual_cfo_and_capex(cf)
        self.assertEqual(float(cfo.iloc[0]), 30.0)
        self.assertEqual(float(capex.iloc[0]), 10.0)
        self.assertEqual(_get_annual_cfo_and_capex(None), (None, None))

    def test_quarterly_revenue(self):
        qf = pd.DataFrame([[50.0, 60.0]], index=["Total Revenue"],
                          columns=pd.to_datetime(["2026-03-31", "2025-12-31"]))
        qrev = _get_quarterly_revenue(qf)
        self.assertEqual(len(qrev), 2)
        self.assertIsNone(_get_quarterly_revenue(None))


class TestGrowthMath(unittest.TestCase):
    def test_cagr_3y_exact(self):
        cagr = _rev_cagr_3y(series([100, 120, 150, 200]))
        self.assertAlmostEqual(cagr, 2.0 ** (1 / 3) - 1, places=9)

    def test_cagr_needs_four_years_and_positive_base(self):
        self.assertIsNone(_rev_cagr_3y(series([100, 120, 150])))
        self.assertIsNone(_rev_cagr_3y(series([0, 120, 150, 200])))
        self.assertIsNone(_rev_cagr_3y(None))

    def test_revenue_acceleration_delta(self):
        self.assertAlmostEqual(_rev_accel(series([100, 120, 150])), 0.05, places=9)
        self.assertIsNone(_rev_accel(series([100, 120])))

    def test_op_margin_mean_and_trend(self):
        mean_om, trend = _op_margin_3y_and_trend(
            series([100, 100, 100]), series([10, 20, 30])
        )
        self.assertAlmostEqual(mean_om, 0.2, places=9)
        self.assertAlmostEqual(trend, 0.3 - 0.15, places=9)
        self.assertEqual(_op_margin_3y_and_trend(None, None), (None, None))

    def test_fcf_margin(self):
        margin = _fcf_margin_3y(
            series([100, 100, 100]), series([30, 30, 30]), series([10, 10, 10])
        )
        self.assertAlmostEqual(margin, 0.2, places=9)
        self.assertIsNone(_fcf_margin_3y(series([100, 100]), series([30, 30]),
                                         series([10, 10])))

    def test_valuation_metric_preference_order(self):
        val, label = _valuation_metric({"enterpriseToRevenue": 5.0, "trailingPE": 30.0})
        self.assertEqual(label, "EV/Sales")
        self.assertAlmostEqual(val, math.log(5.0), places=9)
        val, label = _valuation_metric({"trailingPE": 30.0})
        self.assertEqual(label, "P/E")
        self.assertEqual(_valuation_metric({}), (None, "NA"))

    def test_momentum_12_1(self):
        closes = pd.DataFrame({"Close": [float(100 + i * 0.1) for i in range(300)]})
        mom = _mom_12_1(closes)
        p252, p21 = 100 + (300 - 252) * 0.1, 100 + (300 - 21) * 0.1
        self.assertAlmostEqual(mom, p21 / p252 - 1, places=9)
        self.assertIsNone(_mom_12_1(pd.DataFrame({"Close": [1.0] * 100})))
        self.assertIsNone(_mom_12_1(None))


class TestScoreMetric(unittest.TestCase):
    CASES = [
        ("RevCAGR3Y", 0.30, 2), ("RevCAGR3Y", 0.15, 1), ("RevCAGR3Y", 0.05, 0),
        ("RevCAGR3Y", -0.10, -1),
        ("RevAccel", 0.10, 1), ("RevAccel", -0.10, -1), ("RevAccel", 0.0, 0),
        ("OpMargin3Y", 0.25, 2), ("OpMargin3Y", 0.12, 1), ("OpMargin3Y", 0.05, 0),
        ("OpMargin3Y", -0.05, -1),
        ("OpMarginTrend", 0.05, 1), ("OpMarginTrend", -0.05, -1), ("OpMarginTrend", 0.0, 0),
        ("FCFMargin3Y", 0.20, 2), ("FCFMargin3Y", 0.08, 1), ("FCFMargin3Y", 0.01, 0),
        ("FCFMargin3Y", -0.10, -1),
        ("ValMetric", 1.0, 2), ("ValMetric", 2.0, 1), ("ValMetric", 3.0, 0),
        ("ValMetric", 4.0, -1),
        ("Mom12_1", 0.30, 2), ("Mom12_1", 0.05, 1), ("Mom12_1", -0.05, 0),
        ("Mom12_1", -0.30, -1),
    ]

    def test_threshold_table(self):
        for metric, value, expected in self.CASES:
            score, detail = _score_metric(value, metric)
            self.assertEqual(score, expected, f"{metric}={value} -> {detail}")

    def test_missing_value_scores_zero(self):
        self.assertEqual(_score_metric(None, "RevCAGR3Y"), (0, "no data"))


def quarterly_income(newest_first):
    return pd.DataFrame(
        [list(map(float, newest_first))],
        index=["Net Income"],
        columns=pd.to_datetime(
            [f"202{6 - i}-03-31" for i in range(len(newest_first))]
        ),
    )


class TestEarningsAcceleration(unittest.TestCase):
    def test_accelerating_growth_counts_all_deltas(self):
        count, total, avg_delta, rates, incomes = _compute_earnings_acceleration(
            quarterly_income([100, 50, 30, 20, 15])  # oldest->newest: 15,20,30,50,100
        )
        self.assertEqual((count, total), (3, 3))
        self.assertGreater(avg_delta, 0)
        self.assertEqual(len(rates), 4)
        self.assertEqual(incomes[0], 15.0)

    def test_decelerating_growth_counts_none(self):
        count, total, avg_delta, _, _ = _compute_earnings_acceleration(
            quarterly_income([50, 40, 30, 20, 10])  # steady adds, falling growth rate
        )
        self.assertEqual(count, 0)
        self.assertLess(avg_delta, 0)

    def test_too_few_quarters(self):
        count, total, avg_delta, rates, incomes = _compute_earnings_acceleration(
            quarterly_income([30, 20, 10])
        )
        self.assertIsNone(count)
        self.assertEqual(len(incomes), 3)

    def test_missing_row(self):
        df = pd.DataFrame([[1.0]], index=["Revenue"],
                          columns=pd.to_datetime(["2026-03-31"]))
        self.assertEqual(_compute_earnings_acceleration(df), (None, None, None, [], []))


class FundamentalsServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.repo = Mock()
        self.service = FundamentalsService(
            fundamentals_repository=self.repo, yfinance_gateway=Mock()
        )


class TestCacheOrchestration(FundamentalsServiceTestBase):
    def test_cache_hit_skips_compute(self):
        self.repo.get.return_value = {"composite_score": 7}
        with patch.object(self.service, "_compute_fundamental_score",
                          side_effect=AssertionError("must not compute")):
            out = self.service.get_fundamental_score("intc")
        self.assertEqual(out["composite_score"], 7)
        self.repo.set.assert_not_called()

    def test_cache_miss_computes_and_stores(self):
        self.repo.get.return_value = None
        with patch.object(self.service, "_compute_fundamental_score",
                          return_value={"composite_score": 3}) as compute:
            out = self.service.get_fundamental_score("intc")
        compute.assert_called_once_with("INTC")
        self.repo.set.assert_called_once()
        self.assertEqual(out["composite_score"], 3)

    def test_all_four_wrappers_share_the_pattern(self):
        self.repo.get.return_value = {"cached": True}
        for method in ("get_earnings_calendar", "get_revenue_growth",
                       "get_earnings_acceleration"):
            self.assertEqual(getattr(self.service, method)("intc"), {"cached": True})


class TestBatchScoring(FundamentalsServiceTestBase):
    def test_batch_mixes_hits_computes_and_errors(self):
        def repo_get(sym, kind):
            return {"symbol": "AAA", "composite_score": 2} if sym == "AAA" else None

        self.repo.get.side_effect = repo_get

        def compute(sym):
            if sym == "CCC":
                raise RuntimeError("yahoo choked")
            return {"symbol": sym, "composite_score": 9}

        with patch.object(self.service, "_compute_fundamental_score",
                          side_effect=compute):
            out = self.service.get_fundamental_scores_batch(["aaa", "bbb", "ccc"])

        self.assertEqual(out["requested"], 3)
        self.assertEqual(out["cache_hits"], 1)
        self.assertEqual(out["fetched"], 1)
        self.assertEqual(out["errors"], 1)
        # Sorted by composite descending; flags mark provenance.
        self.assertEqual([r["symbol"] for r in out["results"]], ["BBB", "AAA"])
        self.assertFalse(out["results"][0]["cache_hit"])
        self.assertTrue(out["results"][1]["cache_hit"])


class TestFullProfile(FundamentalsServiceTestBase):
    def arm(self, composite=9, trajectory="accelerating", accel="strong",
            risk="LOW", days_to_earnings=None, cagr=0.3):
        patches = [
            patch.object(self.service, "get_earnings_calendar", return_value={
                "risk_level": risk, "days_to_earnings": days_to_earnings}),
            patch.object(self.service, "get_fundamental_score", return_value={
                "composite_score": composite}),
            patch.object(self.service, "get_revenue_growth", return_value={
                "trajectory": trajectory, "cagr_3y": cagr}),
            patch.object(self.service, "get_earnings_acceleration", return_value={
                "acceleration_label": accel}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_strong_everything_reads_bullish_with_highlights(self):
        self.arm()
        out = self.service.get_full_fundamental_profile("intc")
        self.assertEqual(out["summary"]["overall_signal"], "bullish")
        joined = " ".join(out["summary"]["highlights"])
        self.assertIn("Strong fundamentals", joined)
        self.assertIn("Revenue accelerating", joined)
        self.assertIn("EPS acceleration", joined)
        self.assertIn("Strong 3Y CAGR", joined)

    def test_deteriorating_reads_bearish(self):
        self.arm(composite=-5, trajectory="decelerating", accel="none", cagr=None)
        out = self.service.get_full_fundamental_profile("INTC")
        self.assertEqual(out["summary"]["overall_signal"], "bearish")

    def test_imminent_earnings_overrides_to_caution(self):
        self.arm(risk="HIGH", days_to_earnings=3)
        out = self.service.get_full_fundamental_profile("INTC")
        self.assertEqual(out["summary"]["overall_signal"], "caution")
        self.assertIn("options risk", " ".join(out["summary"]["highlights"]))


class TestTopRankings(FundamentalsServiceTestBase):
    def test_coverage_filter_and_ordering(self):
        self.repo.get_all_latest.return_value = [
            {"symbol": "AAA", "composite_score": 5, "coverage": 0.9,
             "fundamental_label": "good", "fetched_at": "t"},
            {"symbol": "BBB", "composite_score": 9, "coverage": 0.8,
             "fundamental_label": "great", "fetched_at": "t"},
            {"symbol": "LOWCOV", "composite_score": 12, "coverage": 0.2,
             "fundamental_label": "?", "fetched_at": "t"},
            {"symbol": "NOSCORE", "composite_score": None, "coverage": 0.9},
        ]
        out = self.service.get_top_fundamental_stocks(n=10, min_coverage=0.5)
        self.assertEqual(out["total_in_cache"], 4)
        self.assertEqual(out["eligible_count"], 2)
        self.assertEqual([r["symbol"] for r in out["rankings"]], ["BBB", "AAA"])
        self.assertEqual(out["rankings"][0]["rank"], 1)


# ---------------------------------------------------------------------------
# _compute_* internals (wave 2) — full yfinance-statement-shaped fixtures
# ---------------------------------------------------------------------------

from datetime import date, timedelta  # noqa: E402


def statement(rows: dict, dates):
    return pd.DataFrame(
        {pd.Timestamp(d): [float(rows[label][i]) for label in rows]
         for i, d in enumerate(dates)},
        index=list(rows),
    )


ANNUAL_DATES = ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]

FINANCIALS = statement(
    {"Total Revenue": [100, 120, 150, 200],
     "Operating Income": [25, 30, 37.5, 50]},   # constant 25% margin
    ANNUAL_DATES,
)
CASHFLOW = statement(
    {"Operating Cash Flow": [40, 48, 60, 80],   # 40% of revenue
     "Capital Expenditure": [10, 12, 15, 20]},  # 10% -> FCF margin 30%
    ANNUAL_DATES,
)


def momentum_history(n=300):
    return pd.DataFrame(
        {"Close": [float(100 + i * 0.1) for i in range(n)]},
        index=pd.bdate_range(end="2026-07-14", periods=n),
    )


class TestComputeFundamentalScore(FundamentalsServiceTestBase):
    def test_strong_compounder_composite(self):
        yf = self.service._yf
        yf.info.return_value = {"enterpriseToRevenue": 3.0,
                                "sector": "Technology", "marketCap": 1_000}
        yf.financials.return_value = FINANCIALS
        yf.cashflow.return_value = CASHFLOW
        yf.history.return_value = momentum_history()

        out = self.service._compute_fundamental_score("INTC")

        ms = out["metric_scores"]
        self.assertEqual(ms["RevCAGR3Y"]["score"], 2)     # 26% CAGR
        self.assertEqual(ms["RevAccel"]["score"], 1)      # +8.3pp acceleration
        self.assertEqual(ms["OpMargin3Y"]["score"], 2)    # 25% margins
        self.assertEqual(ms["OpMarginTrend"]["score"], 0) # flat
        self.assertEqual(ms["FCFMargin3Y"]["score"], 2)   # 30% FCF margin
        self.assertEqual(ms["ValMetric"]["score"], 2)     # log(3) cheap
        self.assertEqual(ms["Mom12_1"]["score"], 2)       # ~22% 12-1 momentum
        self.assertEqual(out["composite_score"], 11)
        self.assertEqual(out["fundamental_label"], "strong_compounder")
        self.assertEqual(out["coverage"], 1.0)
        self.assertEqual(out["val_type"], "EV/Sales")
        self.assertEqual(out["sector"], "Technology")

    def test_no_data_degrades_to_average_zero_coverage(self):
        yf = self.service._yf
        yf.info.side_effect = RuntimeError("yahoo down")
        yf.financials.return_value = pd.DataFrame()
        yf.cashflow.return_value = pd.DataFrame()
        yf.history.return_value = pd.DataFrame()

        out = self.service._compute_fundamental_score("ZZZ")
        self.assertEqual(out["composite_score"], 0)
        self.assertEqual(out["coverage"], 0.0)
        self.assertEqual(out["fundamental_label"], "average")
        self.assertEqual(out["val_type"], "NA")


QUARTER_DATES = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


class TestComputeRevenueGrowth(FundamentalsServiceTestBase):
    def quarters(self, revenues):
        return statement({"Total Revenue": revenues}, QUARTER_DATES)

    def test_accelerating_quarters(self):
        yf = self.service._yf
        yf.quarterly_financials.return_value = self.quarters([100, 102, 105, 110, 120])
        yf.financials.return_value = FINANCIALS
        out = self.service._compute_revenue_growth("INTC")
        self.assertEqual(out["trajectory"], "accelerating")
        self.assertEqual(out["weighted_score"], 1.0)      # all-positive growth
        self.assertEqual(len(out["quarterly_revenues"]), 5)
        self.assertAlmostEqual(out["cagr_3y"], 2.0 ** (1 / 3) - 1, places=4)
        self.assertAlmostEqual(out["rev_accel"], (200 / 150 - 1) - (150 / 120 - 1),
                               places=4)

    def test_positive_inflection(self):
        yf = self.service._yf
        # Last QoQ positive after a negative one -> inflecting_positive.
        yf.quarterly_financials.return_value = self.quarters([100, 110, 104.5, 100, 103])
        yf.financials.return_value = pd.DataFrame()
        out = self.service._compute_revenue_growth("INTC")
        self.assertEqual(out["trajectory"], "inflecting_positive")

    def test_insufficient_quarters(self):
        yf = self.service._yf
        yf.quarterly_financials.return_value = statement(
            {"Total Revenue": [100, 110]}, QUARTER_DATES[:2]
        )
        yf.financials.return_value = pd.DataFrame()
        out = self.service._compute_revenue_growth("INTC")
        self.assertEqual(out["trajectory"], "insufficient_data")
        self.assertEqual(out["quarterly_revenues"], [])


class TestComputeEarningsAccelerationService(FundamentalsServiceTestBase):
    def test_strong_acceleration(self):
        self.service._yf.quarterly_income_stmt.return_value = quarterly_income(
            [100e6, 50e6, 30e6, 20e6, 15e6]
        )
        out = self.service._compute_earnings_acceleration("INTC")
        self.assertEqual(out["acceleration_label"], "strong")
        self.assertEqual(out["acceleration_score"], 2)
        self.assertEqual(out["accel_count"], 3)
        self.assertEqual(out["net_incomes_M"][0], 15.0)   # scaled to millions

    def test_decelerating(self):
        self.service._yf.quarterly_income_stmt.return_value = quarterly_income(
            [50e6, 40e6, 30e6, 20e6, 10e6]
        )
        out = self.service._compute_earnings_acceleration("INTC")
        self.assertEqual(out["acceleration_label"], "decelerating")
        self.assertEqual(out["acceleration_score"], -1)

    def test_gateway_failure_degrades(self):
        self.service._yf.quarterly_income_stmt.side_effect = RuntimeError("nope")
        out = self.service._compute_earnings_acceleration("INTC")
        self.assertEqual(out["acceleration_label"], "insufficient_data")


class TestComputeEarningsCalendar(FundamentalsServiceTestBase):
    def arm_history(self):
        yf = self.service._yf
        yf.history.return_value = pd.DataFrame()
        yf.earnings_dates.return_value = pd.DataFrame()

    def calendar_at(self, days_out):
        earn = date.today() + timedelta(days=days_out)
        return pd.DataFrame(
            {0: [pd.Timestamp(earn)]}, index=["Earnings Date"]
        )

    def test_risk_ladder(self):
        self.arm_history()
        for days_out, expected in ((3, "CRITICAL"), (10, "HIGH"),
                                   (20, "MODERATE"), (45, "LOW")):
            self.service._yf.calendar.return_value = self.calendar_at(days_out)
            out = self.service._compute_earnings_calendar("INTC")
            self.assertEqual(out["risk_level"], expected, days_out)
            self.assertEqual(out["days_to_earnings"], days_out)
        # MODERATE also flags the pre-earnings IV setup.
        self.service._yf.calendar.return_value = self.calendar_at(20)
        self.assertTrue(self.service._compute_earnings_calendar("INTC")["pre_earnings_setup"])

    def test_dict_form_calendar(self):
        self.arm_history()
        earn = date.today() + timedelta(days=40)
        self.service._yf.calendar.return_value = {"Earnings Date": [earn]}
        out = self.service._compute_earnings_calendar("INTC")
        self.assertEqual(out["risk_level"], "LOW")
        self.assertEqual(out["earnings_date"], earn.isoformat())

    def test_calendar_failure_stays_unknown(self):
        self.arm_history()
        self.service._yf.calendar.side_effect = RuntimeError("yahoo calendar down")
        out = self.service._compute_earnings_calendar("INTC")
        self.assertEqual(out["risk_level"], "UNKNOWN")
        self.assertIsNone(out["days_to_earnings"])

    def test_historical_earnings_move_measured(self):
        yf = self.service._yf
        yf.calendar.return_value = None
        idx = pd.bdate_range(end="2026-07-14", periods=10)
        closes = [100.0] * 10
        closes[5] = 110.0                                  # +10% earnings pop
        yf.history.return_value = pd.DataFrame({"Close": closes}, index=idx)
        yf.earnings_dates.return_value = pd.DataFrame(
            {"EPS Estimate": [1.0]}, index=[idx[5]]
        )
        out = self.service._compute_earnings_calendar("INTC")
        self.assertEqual(out["historical_avg_move_pct"], 10.0)


# ---------------------------------------------------------------------------
# Cache-backed collection surfaces (85%-campaign part 5)
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402


def cal_entry(sym, days_out, fetched_ago_s=60, risk="MODERATE"):
    return {
        "symbol": sym,
        "earnings_date": (date.today() + timedelta(days=days_out)).isoformat(),
        "risk_level": risk,
        "pre_earnings_setup": risk == "MODERATE",
        "historical_avg_move_pct": 5.5,
        "fetched_at": "2026-07-24T00:00:00Z",
        "_fetched_at_ts": int(_time.time()) - fetched_ago_s,
    }


class TestUpcomingEarnings(FundamentalsServiceTestBase):
    def test_window_staleness_and_ordering(self):
        self.repo.ttl_seconds.return_value = 3600.0
        self.repo.get_all_latest.return_value = [
            cal_entry("SOON", 3),
            cal_entry("LATER", 10),
            cal_entry("FAR", 40),                      # outside the 14d window
            cal_entry("STALE", 5, fetched_ago_s=999_999),
            {"symbol": "NODATE", "_fetched_at_ts": int(_time.time())},
        ]
        out = self.service.get_upcoming_earnings(days=14)
        self.assertEqual([u["symbol"] for u in out["upcoming"]], ["SOON", "LATER"])
        self.assertEqual(out["stale_excluded"], 1)
        self.assertEqual(out["total_in_cache"], 5)

    def test_include_stale_keeps_and_flags(self):
        self.repo.ttl_seconds.return_value = 3600.0
        self.repo.get_all_latest.return_value = [
            cal_entry("STALE", 5, fetched_ago_s=999_999)
        ]
        out = self.service.get_upcoming_earnings(days=14, include_stale=True)
        self.assertEqual(out["count"], 1)
        self.assertTrue(out["upcoming"][0]["stale"])


def score_entry(sym, score, sector="Tech", coverage=0.9):
    return {"symbol": sym, "composite_score": score, "sector": sector,
            "coverage": coverage, "fundamental_label": "solid"}


class TestSectorBreakdown(FundamentalsServiceTestBase):
    def test_grouping_ranking_and_filter(self):
        self.repo.get_all_latest.return_value = [
            score_entry("AAA", 3), score_entry("BBB", 9),
            score_entry("CCC", 5, sector="Energy"),
            {"symbol": "NOSECTOR", "composite_score": 1},   # -> "Unknown"
        ]
        out = self.service.get_sector_fundamental_breakdown(top_n=5)
        self.assertEqual(out["sector_count"], 3)
        tech = out["sectors"]["Tech"]
        self.assertEqual([e["symbol"] for e in tech], ["BBB", "AAA"])
        self.assertEqual(tech[0]["rank"], 1)

        only_energy = self.service.get_sector_fundamental_breakdown(sector="energy")
        self.assertEqual(list(only_energy["sectors"]), ["Energy"])

    def test_cache_stats_passthrough(self):
        self.repo.stats.return_value = {"data_types": []}
        self.assertEqual(self.service.get_cache_stats(), {"data_types": []})


class TestScoreChanges(FundamentalsServiceTestBase):
    def arm_history(self, then, now):
        self.repo.get_all_latest.return_value = [score_entry("MOVER", now)]
        self.repo.history.return_value = [
            {"composite_score": then, "fundamental_label": "weak",
             "fetched_at": "t0"},
            {"composite_score": now, "fundamental_label": "solid",
             "fetched_at": "t1"},
        ]

    def test_improver_detected_with_delta(self):
        self.arm_history(then=2, now=8)
        out = self.service.get_fundamental_score_changes(min_delta=2)
        self.assertEqual(len(out["changes"]), 1)
        change = out["changes"][0]
        self.assertEqual(change["delta"], 6)
        self.assertEqual(change["direction"], "improving")

    def test_direction_and_min_delta_filters(self):
        self.arm_history(then=2, now=8)
        out = self.service.get_fundamental_score_changes(direction="deteriorating")
        self.assertEqual(out["changes"], [])
        out = self.service.get_fundamental_score_changes(min_delta=10)
        self.assertEqual(out["changes"], [])

    def test_insufficient_history_counted(self):
        self.repo.get_all_latest.return_value = [score_entry("NEWB", 5)]
        self.repo.history.return_value = [{"composite_score": 5}]
        out = self.service.get_fundamental_score_changes()
        self.assertEqual(out["symbols_with_insufficient_history"], 1)
        self.assertEqual(out["changes"], [])


class TestFundamentalHistory(FundamentalsServiceTestBase):
    def test_invalid_data_type_rejected(self):
        out = self.service.get_fundamental_history("intc", "nonsense")
        self.assertIn("error", out)

    def test_score_trend_labels(self):
        self.repo.history.return_value = [
            {"composite_score": 2, "fetched_at": "t0"},
            {"composite_score": 7, "fetched_at": "t1"},
        ]
        out = self.service.get_fundamental_history("INTC", "fundamental_score")
        self.assertEqual(out.get("trend"), "improving")


if __name__ == "__main__":
    unittest.main()
