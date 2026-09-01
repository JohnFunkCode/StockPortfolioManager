"""ArbitrageService tests — scoring behaviour, fully mocked.

No database, no network: a fake repository supplies the curated universe and a
fake PricesService supplies constructed price history, so every assertion is
about the service's own logic.

The tests that matter most are in ``ConvergenceRankingTest``: given identical
price data, a pair with a real convergence mechanism must outrank one with
none. That is the inversion the whole scanner is built around — spread width
qualifies a candidate, the mechanism ranks it.
"""
import unittest

import numpy as np
import pandas as pd
import pytz

from quantcore.services.arbitrage import ArbitrageService


def price_frame(values, start="2025-01-01") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(values), freq="D")
    return pd.DataFrame({"Close": np.asarray(values, dtype=float)}, index=idx)


def cointegrated_pair(n=400, seed=5, gap_sigma=2.0, wobble_sigma=0.006):
    """x is a random walk; y tracks it with a stationary wobble ending `gap_sigma` out.

    ``wobble_sigma`` is kept below the walk's own volatility so the pair also
    clears a realistic return-correlation floor — a wobble that dominates the
    shared trend produces a cointegrated pair that nonetheless correlates at
    ~0.4, which is not what a real miner/commodity relationship looks like.
    """
    rng = np.random.default_rng(seed)
    x = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    wobble = np.zeros(n)
    for t in range(1, n):
        wobble[t] = 0.85 * wobble[t - 1] + rng.normal(0, wobble_sigma)
    wobble[-1] = wobble.std() * gap_sigma  # force a known terminal stretch
    y = np.exp(np.log(x) + wobble)
    return y, x


class FakePrices:
    """Serves canned Close history; raises for symbols marked as broken."""

    def __init__(self, frames: dict, broken=()):
        self.frames = frames
        self.broken = set(broken)
        self.calls = []

    def get_history(self, symbol, interval="1d", days=365):
        self.calls.append((symbol, interval, days))
        if symbol in self.broken:
            raise RuntimeError(f"yahoo exploded for {symbol}")
        return self.frames.get(symbol, pd.DataFrame())


class FakeRepo:
    def __init__(self, entries, snapshot=None):
        self.entries = entries
        self.snapshot = snapshot

    def load_universe(self):
        return [dict(e) for e in self.entries]

    def get_entry(self, security):
        target = (security or "").upper()
        return next((dict(e) for e in self.entries if e["security"] == target), None)

    def latest_nav_snapshot(self, security):
        return self.snapshot


class FakeGateway:
    def __init__(self, info=None):
        self._info = info or {}

    def ticker_info(self, symbol, timeout=15.0):
        return self._info.get(symbol, {})


def entry(security="AAA", kind="producer", underlying="ZZZ",
          hedge="HHH", mechanism="none", **extra):
    base = {
        "security": security, "name": security, "kind": kind,
        "underlying": underlying, "hedge_instrument": hedge,
        "convergence_mechanism": mechanism, "holdings_units": None,
        "holdings_as_of": None, "senior_claims_usd": 0.0,
        "annual_senior_cost_usd": 0.0, "other_assets_usd": 0.0,
        "diluted_shares": None, "source": None, "notes": None,
    }
    base.update(extra)
    return base


def build(entries, frames, snapshot=None, broken=(), info=None):
    return ArbitrageService(
        arbitrage_repository=FakeRepo(entries, snapshot),
        prices=FakePrices(frames, broken),
        yfinance_gateway=FakeGateway(info),
    )


class ConvergenceRankingTest(unittest.TestCase):
    """Identical data, different mechanism — the mechanism must decide."""

    def setUp(self):
        y, x = cointegrated_pair()
        self.frames = {"AAA": price_frame(y), "BBB": price_frame(y),
                       "ZZZ": price_frame(x)}

    def score_for(self, mechanism):
        svc = build([entry(security="AAA", mechanism=mechanism)], self.frames)
        return svc.analyze_pair("AAA")["score"]

    def test_redemption_outranks_no_mechanism(self):
        self.assertGreater(self.score_for("redemption"), self.score_for("none"))

    def test_buyback_sits_between_redemption_and_none(self):
        none_score = self.score_for("none")
        buyback = self.score_for("buyback")
        redemption = self.score_for("redemption")
        self.assertLess(none_score, buyback)
        self.assertLess(buyback, redemption)

    def test_no_mechanism_is_called_out_in_breaks_on(self):
        svc = build([entry(mechanism="none")], self.frames)
        result = svc.analyze_pair("AAA")
        self.assertIn("No forcing mechanism — the gap can widen indefinitely",
                      result["breaks_on"])

    def test_scan_orders_by_score_descending(self):
        entries = [entry(security="AAA", mechanism="none"),
                   entry(security="BBB", mechanism="redemption")]
        svc = build(entries, self.frames)
        scan = svc.scan()
        self.assertEqual([c["security"] for c in scan["candidates"]],
                         ["BBB", "AAA"])


class HedgeabilityTest(unittest.TestCase):
    def setUp(self):
        y, x = cointegrated_pair()
        self.frames = {"AAA": price_frame(y), "ZZZ": price_frame(x),
                       "CL=F": price_frame(x)}

    def test_missing_hedge_halves_the_score_and_is_flagged(self):
        hedged = build([entry(hedge="HHH")], self.frames).analyze_pair("AAA")
        naked = build([entry(hedge=None)], self.frames).analyze_pair("AAA")
        self.assertEqual(hedged["factors"]["hedge"], 1.0)
        self.assertEqual(naked["factors"]["hedge"], 0.5)
        self.assertAlmostEqual(naked["score"], hedged["score"] / 2, delta=0.2)
        self.assertIn("Short leg not executable without futures",
                      naked["breaks_on"])

    def test_futures_underlying_without_hedge_is_marked_futures_only(self):
        svc = build([entry(underlying="CL=F", hedge=None)], self.frames)
        hedge = svc.analyze_pair("AAA")["hedge"]
        self.assertTrue(hedge["futures_only"])
        self.assertFalse(hedge["available"])

    def test_mapped_hedge_reports_the_instrument(self):
        svc = build([entry(hedge="GLD")], self.frames)
        self.assertEqual(svc.analyze_pair("AAA")["hedge"]["instrument"], "GLD")


class NavVehicleTest(unittest.TestCase):
    """The MSTR shape, end to end through the service."""

    def setUp(self):
        n = 400
        rng = np.random.default_rng(9)
        btc = 64_709 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
        btc[-1] = 64_709
        mstr = 96.99 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        mstr[-1] = 96.99
        self.frames = {"MSTR": price_frame(mstr), "BTC-USD": price_frame(btc)}
        self.entry = entry(
            security="MSTR", kind="nav_vehicle", underlying="BTC-USD",
            hedge="IBIT", mechanism="buyback",
            holdings_units=843_775, holdings_as_of="2026-07-14",
            senior_claims_usd=16_500_000_000,
            annual_senior_cost_usd=854_000_000,
            diluted_shares=350_000_000,
        )

    def test_reports_the_net_discount_not_the_gross_one(self):
        result = build([self.entry], self.frames).analyze_pair("MSTR")
        nav = result["nav"]
        self.assertTrue(nav["available"])
        self.assertAlmostEqual(nav["premium_discount_pct"], -10.9, delta=0.5)
        self.assertAlmostEqual(nav["gross_premium_discount_pct"], -37.8, delta=0.5)
        self.assertEqual(result["basis"], "nav_discount")

    def test_explains_that_the_headline_gap_is_overstated(self):
        result = build([self.entry], self.frames).analyze_pair("MSTR")
        self.assertTrue(
            any("accounting illusion" in r for r in result["reasons"]),
            result["reasons"],
        )

    def test_carry_drag_erodes_the_score_and_is_flagged(self):
        result = build([self.entry], self.frames).analyze_pair("MSTR")
        self.assertLess(result["factors"]["carry"], 1.0)
        self.assertIn("Negative carry (senior claims serviced out of NAV)",
                      result["breaks_on"])

    def test_db_snapshot_supersedes_the_yaml_bootstrap(self):
        snapshot = {
            "units": 900_000, "as_of": "2026-07-20", "senior_claims": 1.0,
            "annual_senior_cost": 0.0, "other_assets": 0.0,
            "diluted_shares": 350_000_000, "source": "db",
        }
        svc = build([self.entry], self.frames, snapshot=snapshot)
        nav = svc.analyze_pair("MSTR")["nav"]
        self.assertEqual(nav["units"], 900_000)
        self.assertEqual(nav["source"], "db")

    def test_stale_holdings_reduce_the_score(self):
        # The fixture's 2026-07-14 snapshot is 31 days old at this injected
        # 2026-08-14 clock, intentionally below the 45-day stale threshold.
        evening = pytz.timezone("America/New_York").localize(
            pd.Timestamp("2026-08-14 23:35").to_pydatetime()
        )
        fresh = build([self.entry], self.frames).analyze_pair("MSTR", now=evening)
        stale_entry = {**self.entry, "holdings_as_of": "2020-01-01"}
        stale = build([stale_entry], self.frames).analyze_pair("MSTR", now=evening)
        self.assertEqual(fresh["factors"]["freshness"], 1.0)
        self.assertEqual(stale["factors"]["freshness"], 0.85)
        self.assertIn("Stale holdings data", stale["breaks_on"])

    def test_missing_holdings_skips_nav_math_without_failing(self):
        bare = {**self.entry, "holdings_units": None, "diluted_shares": None}
        result = build([bare], self.frames).analyze_pair("MSTR")
        self.assertFalse(result["nav"]["available"])
        self.assertIn("No curated holdings", result["nav"]["reason"])
        self.assertEqual(result["basis"], "spread_zscore")

    def test_share_count_falls_back_to_the_gateway(self):
        bare = {**self.entry, "diluted_shares": None}
        svc = build([bare], self.frames,
                    info={"MSTR": {"sharesOutstanding": 350_000_000}})
        self.assertTrue(svc.analyze_pair("MSTR")["nav"]["available"])


class TrendPenaltyTest(unittest.TestCase):
    def test_widening_spread_is_penalised(self):
        n = 300
        rng = np.random.default_rng(21)
        # x's own linear trend is removed so the hedge-ratio regression cannot
        # absorb the drift below into beta and flatten the residual.
        log_x = np.cumsum(rng.normal(0, 0.01, n))
        log_x -= np.linspace(0, log_x[-1], n)
        x = 100 * np.exp(log_x)
        # y carries x's path plus a steady, one-directional drift away from it.
        widening = np.exp(np.log(x) + np.linspace(0, -0.5, n))
        frames = {"AAA": price_frame(widening), "ZZZ": price_frame(x)}
        result = build([entry()], frames).analyze_pair("AAA")
        self.assertEqual(result["factors"]["trend"], 0.75)
        self.assertIn("Spread trending wider", result["breaks_on"])


class ErrorHandlingTest(unittest.TestCase):
    def setUp(self):
        y, x = cointegrated_pair()
        self.frames = {"AAA": price_frame(y), "ZZZ": price_frame(x)}

    def test_unknown_security_without_underlying_explains_itself(self):
        result = build([], self.frames).analyze_pair("NOPE")
        self.assertIn("not in the curated universe", result["error"])
        self.assertIn("hint", result)

    def test_ad_hoc_pair_works_when_an_underlying_is_supplied(self):
        result = build([], self.frames).analyze_pair("AAA", underlying="ZZZ")
        self.assertIsNone(result.get("error"))
        self.assertEqual(result["kind"], "producer")
        self.assertEqual(result["convergence"], "none")

    def test_missing_price_history_returns_a_clear_error(self):
        result = build([entry()], {"ZZZ": price_frame([1.0] * 40)}).analyze_pair("AAA")
        self.assertIn("No price history", result["error"])

    def test_too_little_history_is_treated_as_missing(self):
        frames = {"AAA": price_frame([1.0] * 10), "ZZZ": price_frame([1.0] * 10)}
        self.assertIn("No price history", build([entry()], frames).analyze_pair("AAA")["error"])

    def test_scan_isolates_a_failing_symbol(self):
        entries = [entry(security="AAA"), entry(security="BOOM")]
        svc = build(entries, self.frames, broken={"BOOM"})
        scan = svc.scan()
        self.assertEqual([c["security"] for c in scan["candidates"]], ["AAA"])
        self.assertEqual(len(scan["errors"]), 1)
        self.assertEqual(scan["errors"][0]["security"], "BOOM")
        self.assertEqual(scan["scanned"], 2)

    def test_flat_series_yield_insufficient_data_rather_than_a_fake_score(self):
        frames = {"AAA": price_frame([100.0] * 60), "ZZZ": price_frame([50.0] * 60)}
        result = build([entry()], frames).analyze_pair("AAA")
        self.assertEqual(result["verdict"], "insufficient_data")
        self.assertEqual(result["score"], 0.0)


class ScanSummaryTest(unittest.TestCase):
    """The compact projection the chat sidekick consumes."""

    def setUp(self):
        n = 400
        rng = np.random.default_rng(9)
        btc = 64_709 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
        btc[-1] = 64_709
        mstr = 96.99 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        mstr[-1] = 96.99
        y, x = cointegrated_pair()
        self.frames = {
            "MSTR": price_frame(mstr), "BTC-USD": price_frame(btc),
            "AAA": price_frame(y), "ZZZ": price_frame(x),
        }
        self.entries = [
            entry(security="MSTR", kind="nav_vehicle", underlying="BTC-USD",
                  hedge="IBIT", mechanism="buyback", holdings_units=843_775,
                  holdings_as_of="2026-07-14", senior_claims_usd=16_500_000_000,
                  annual_senior_cost_usd=854_000_000, diluted_shares=350_000_000),
            entry(security="AAA"),
        ]

    def test_rows_carry_the_judgement_fields(self):
        summary = build(self.entries, self.frames).scan_summary()
        row = next(r for r in summary["candidates"] if r["security"] == "MSTR")
        for field in ("score", "verdict", "discount_pct", "convergence",
                      "hedge_available", "breaks_on", "cointegrated"):
            self.assertIn(field, row)
        self.assertEqual(summary["returned"], len(summary["candidates"]))

    def test_discount_is_the_net_figure_and_gross_is_absent(self):
        """A summary row is exactly where a gross/net misread would happen, so
        the gross number is dropped rather than trimmed alongside it."""
        summary = build(self.entries, self.frames).scan_summary()
        row = next(r for r in summary["candidates"] if r["security"] == "MSTR")
        self.assertAlmostEqual(row["discount_pct"], -10.9, delta=0.5)
        self.assertNotIn("gross_premium_discount_pct", row)
        self.assertNotIn("nav", row)

    def test_heavy_blocks_are_dropped(self):
        summary = build(self.entries, self.frames).scan_summary()
        row = summary["candidates"][0]
        for heavy in ("statistics", "factors", "reasons", "hedge"):
            self.assertNotIn(heavy, row)

    def test_non_nav_rows_report_no_discount(self):
        summary = build(self.entries, self.frames).scan_summary()
        row = next(r for r in summary["candidates"] if r["security"] == "AAA")
        self.assertIsNone(row["discount_pct"])
        self.assertIsNotNone(row["spread_zscore"])

    def test_errors_and_ordering_survive_the_projection(self):
        entries = self.entries + [entry(security="BOOM")]
        summary = build(entries, self.frames, broken={"BOOM"}).scan_summary()
        scores = [r["score"] or 0 for r in summary["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([e["security"] for e in summary["errors"]], ["BOOM"])

    def test_note_tells_the_reader_the_discount_is_net(self):
        summary = build(self.entries, self.frames).scan_summary()
        self.assertIn("NET", summary["note"])


class SpreadHistoryTest(unittest.TestCase):
    """The series analyze_pair discards, plus the levels needed to draw it."""

    def setUp(self):
        y, x = cointegrated_pair()
        self.frames = {"AAA": price_frame(y), "ZZZ": price_frame(x)}
        self.entries = [entry(security="AAA")]

    def test_returns_dated_points_and_band_levels(self):
        result = build(self.entries, self.frames).get_spread_history("AAA")
        self.assertGreater(len(result["points"]), 100)
        self.assertIn("date", result["points"][0])
        self.assertIn("spread", result["points"][0])
        # Bands are precomputed so the chart never does arithmetic.
        mean, std = result["mean"], result["std"]
        self.assertAlmostEqual(result["bands"]["plus_one"], mean + std, places=5)
        self.assertAlmostEqual(result["bands"]["minus_one"], mean - std, places=5)
        self.assertAlmostEqual(result["bands"]["plus_two"], mean + 2 * std, places=5)
        self.assertAlmostEqual(result["bands"]["minus_two"], mean - 2 * std, places=5)

    def test_latest_matches_the_final_point(self):
        result = build(self.entries, self.frames).get_spread_history("AAA")
        self.assertEqual(result["latest"]["spread"], result["points"][-1]["spread"])
        self.assertEqual(result["latest"]["date"], result["points"][-1]["date"])
        self.assertIsNotNone(result["latest"]["z"])

    def test_trend_carries_an_intercept_so_the_line_can_be_drawn(self):
        result = build(self.entries, self.frames).get_spread_history("AAA")
        self.assertIn("intercept", result["trend"])
        self.assertIsNotNone(result["trend"]["intercept"])

    def test_cointegration_block_never_leaks_the_series(self):
        """engle_granger returns the spread; it must not ride along twice."""
        result = build(self.entries, self.frames).get_spread_history("AAA")
        self.assertNotIn("spread", result["cointegration"])

    def test_ad_hoc_pair_needs_an_underlying(self):
        svc = build([], self.frames)
        self.assertIn("not in the curated universe",
                      svc.get_spread_history("AAA")["error"])
        self.assertIsNone(
            svc.get_spread_history("AAA", underlying="ZZZ").get("error")
        )

    def test_missing_history_is_reported_cleanly(self):
        result = build(self.entries, {"ZZZ": price_frame([1.0] * 40)}).get_spread_history("AAA")
        self.assertIn("No price history", result["error"])


class PremiumHistoryTest(unittest.TestCase):
    def setUp(self):
        n = 120
        self.frames = {
            "MSTR": price_frame([100.0] * n),
            "BTC-USD": price_frame([50_000.0] * n),
        }
        self.entry = entry(
            security="MSTR", kind="nav_vehicle", underlying="BTC-USD", hedge="IBIT",
            mechanism="buyback", holdings_units=1_000, holdings_as_of="2026-07-14",
            senior_claims_usd=10_000_000, diluted_shares=1_000_000,
        )

    def test_series_values_are_exact(self):
        # 1,000 units x $50,000 = $50M gross, less $10M senior = $40M over 1M
        # shares = $40/share; price 100 => +150% premium.
        result = build([self.entry], self.frames).get_premium_history("MSTR")
        self.assertEqual(len(result["points"]), 120)
        point = result["points"][-1]
        self.assertAlmostEqual(point["nav_per_share"], 40.0, places=4)
        self.assertAlmostEqual(point["premium_discount_pct"], 150.0, places=4)
        self.assertEqual(result["latest"], point)

    def test_late_evening_does_not_add_a_nav_age_day(self):
        evening = pytz.timezone("America/New_York").localize(
            pd.Timestamp("2026-08-14 23:35").to_pydatetime()
        )
        result = build([self.entry], self.frames).get_premium_history(
            "MSTR", now=evening
        )
        self.assertEqual(result["holdings_age_days"], 31)

    def test_approximation_is_declared_not_implied(self):
        """The series applies today's capital structure to past prices; a
        reader who misses that would misread how the gap behaved."""
        result = build([self.entry], self.frames).get_premium_history("MSTR")
        self.assertEqual(result["method"], "current_capital_structure")
        self.assertIn("Approximation", result["note"])
        self.assertIn("not a record", result["note"])

    def test_db_snapshot_supersedes_the_yaml_bootstrap(self):
        snapshot = {"units": 2_000, "as_of": "2026-07-20", "senior_claims": 0.0,
                    "annual_senior_cost": 0.0, "other_assets": 0.0,
                    "diluted_shares": 1_000_000, "source": "db"}
        result = build([self.entry], self.frames, snapshot=snapshot).get_premium_history("MSTR")
        self.assertEqual(result["units"], 2_000)
        # 2,000 x 50,000 / 1M shares = $100/share, price 100 => flat.
        self.assertAlmostEqual(result["points"][-1]["premium_discount_pct"], 0.0, places=4)

    def test_non_nav_vehicle_is_refused_with_a_reason(self):
        producer = entry(security="GDX", kind="producer", underlying="GC=F")
        frames = {"GDX": price_frame([30.0] * 60), "GC=F": price_frame([2000.0] * 60)}
        result = build([producer], frames).get_premium_history("GDX")
        self.assertIn("not a NAV vehicle", result["error"])
        self.assertEqual(result["kind"], "producer")

    def test_uncurated_security_is_refused(self):
        result = build([], self.frames).get_premium_history("NOPE")
        self.assertIn("not in the curated universe", result["error"])

    def test_missing_holdings_explains_what_to_add(self):
        bare = {**self.entry, "holdings_units": None, "diluted_shares": None}
        result = build([bare], self.frames).get_premium_history("MSTR")
        self.assertIn("arb_universe.yaml", result["error"])


class DiscoveryIncludeAllTest(unittest.TestCase):
    """Near-misses are the point of the scatter — they must survive the sweep."""

    def setUp(self):
        y, x = cointegrated_pair(seed=12)
        self.frames = {"MINER": price_frame(y), "GC=F": price_frame(x)}
        self.info = {"MINER": {"sector": "Basic Materials", "industry": "Gold Mining"}}

    def test_default_omits_the_tested_list(self):
        svc = build([], self.frames, info=self.info)
        result = svc.discover_pairs(["MINER"], references=["GC=F"])
        self.assertNotIn("tested", result)

    def test_include_all_reports_passes_and_failures_alike(self):
        svc = build([], self.frames, info=self.info)
        result = svc.discover_pairs(["MINER"], references=["GC=F"], include_all=True)
        self.assertEqual(len(result["tested"]), 1)
        row = result["tested"][0]
        for field in ("correlation", "statistic", "half_life_days", "passed",
                      "failed_because", "economic_link"):
            self.assertIn(field, row)
        self.assertIn("critical_values", result)

    def test_a_rejected_pair_keeps_its_measurements(self):
        """A correlation floor of 1.0 rejects everything — the row must still
        carry the numbers so the scatter can plot the near-miss."""
        svc = build([], self.frames, info=self.info)
        result = svc.discover_pairs(["MINER"], references=["GC=F"],
                                    min_abs_correlation=1.0, include_all=True)
        self.assertEqual(result["count"], 0)
        row = result["tested"][0]
        self.assertFalse(row["passed"])
        self.assertIn("correlation below", row["failed_because"])
        self.assertIsNotNone(row["correlation"])
        self.assertIsNotNone(row["statistic"])

    def test_gate_rejections_are_recorded_too(self):
        svc = build([], self.frames, info={"MINER": {"sector": "Consumer Staples"}})
        result = svc.discover_pairs(["MINER"], references=["GC=F"], include_all=True)
        row = result["tested"][0]
        self.assertFalse(row["passed"])
        self.assertEqual(row["failed_because"], "no economic link")
        self.assertFalse(row["economic_link"])


class UniverseTest(unittest.TestCase):
    def test_late_evening_does_not_add_a_holdings_age_day(self):
        entries = [entry(security="OLD", holdings_as_of="2026-08-14")]
        evening = pytz.timezone("America/New_York").localize(
            pd.Timestamp("2026-08-14 23:35").to_pydatetime()
        )
        universe = build(entries, {}).get_universe(now=evening)
        row = universe["entries"][0]
        self.assertEqual(row["holdings_age_days"], 0)

    def test_flags_stale_holdings_and_hedge_availability(self):
        entries = [
            entry(security="OLD", holdings_as_of="2020-01-01", hedge=None),
            entry(security="NEW", holdings_as_of=None, hedge="IBIT"),
        ]
        universe = build(entries, {}).get_universe()
        by_symbol = {e["security"]: e for e in universe["entries"]}
        self.assertEqual(universe["count"], 2)
        self.assertTrue(by_symbol["OLD"]["holdings_stale"])
        self.assertFalse(by_symbol["OLD"]["hedge_available"])
        self.assertFalse(by_symbol["NEW"]["holdings_stale"])
        self.assertTrue(by_symbol["NEW"]["hedge_available"])

    def test_scan_never_reports_a_negative_or_inconsistent_count(self):
        """Defence in depth: the REST edge rejects top_n<1, but an in-process
        caller passing -1 must not get `candidates[:-1]` with returned=-1."""
        y, x = cointegrated_pair()
        frames = {"AAA": price_frame(y), "BBB": price_frame(y),
                  "ZZZ": price_frame(x)}
        entries = [entry(security="AAA"), entry(security="BBB")]
        svc = build(entries, frames)
        for top_n in (-1, 0, 1, 50):
            scan = svc.scan(top_n=top_n)
            self.assertEqual(scan["returned"], len(scan["candidates"]),
                             f"top_n={top_n}")
            self.assertGreaterEqual(scan["returned"], 0, f"top_n={top_n}")
        # A negative limit must not silently truncate the ranking either.
        self.assertEqual(svc.scan(top_n=-1)["returned"], 0)
        self.assertEqual(svc.scan(top_n=50)["returned"], 2)

    def test_scan_filters_by_kind(self):
        y, x = cointegrated_pair()
        frames = {"AAA": price_frame(y), "BBB": price_frame(y),
                  "ZZZ": price_frame(x)}
        entries = [entry(security="AAA", kind="producer"),
                   entry(security="BBB", kind="commodity_etf")]
        scan = build(entries, frames).scan(kinds=["commodity_etf"])
        self.assertEqual([c["security"] for c in scan["candidates"]], ["BBB"])


class DiscoverPairsTest(unittest.TestCase):
    def setUp(self):
        y, x = cointegrated_pair(seed=12)
        self.frames = {"MINER": price_frame(y), "GC=F": price_frame(x)}

    def test_economic_link_gate_drops_unrelated_sectors(self):
        svc = build([], self.frames,
                    info={"MINER": {"sector": "Consumer Staples",
                                    "industry": "Packaged Foods"}})
        found = svc.discover_pairs(["MINER"], references=["GC=F"])
        self.assertEqual(found["count"], 0)

    def test_linked_sector_passes_the_gate(self):
        svc = build([], self.frames,
                    info={"MINER": {"sector": "Basic Materials",
                                    "industry": "Gold Mining"}})
        found = svc.discover_pairs(["MINER"], references=["GC=F"])
        self.assertEqual(found["count"], 1)
        pair = found["pairs"][0]
        self.assertEqual(pair["underlying"], "GC=F")
        self.assertTrue(pair["economic_link"])
        self.assertIn("no convergence claim", pair["note"])

    def test_gate_can_be_disabled_explicitly(self):
        svc = build([], self.frames,
                    info={"MINER": {"sector": "Consumer Staples"}})
        found = svc.discover_pairs(["MINER"], references=["GC=F"],
                                   require_economic_link=False)
        self.assertEqual(found["count"], 1)
        self.assertFalse(found["pairs"][0]["economic_link"])

    def test_symbols_without_history_are_skipped_not_fatal(self):
        svc = build([], self.frames)
        found = svc.discover_pairs(["GHOST"], references=["GC=F"],
                                   require_economic_link=False)
        self.assertEqual(found["count"], 0)
        self.assertEqual(found["skipped"][0]["symbol"], "GHOST")


class EconomicLinkGateTest(unittest.TestCase):
    """The gate must name the commodity, not the industry around it.

    It used to be a bare substring test against sector + industry + summary,
    with generic keywords ("mining", "energy", "technology") in the panel. Two
    distinct false links came out of that, and both are guarded here: a
    *bitcoin* miner reading as "mining" and getting cointegration-tested
    against gold and copper, and "Goldman" reading as "gold".
    """

    link = staticmethod(ArbitrageService._economic_link)

    BITCOIN_MINER = (
        "financial services capital markets marathon digital is a digital "
        "asset technology company that mines cryptocurrencies, operating "
        "bitcoin mining facilities and sourcing low-cost energy."
    ).lower()
    GOLD_MINER = (
        "basic materials gold newmont engages in the exploration and mining "
        "of gold, silver, copper and other precious metals."
    ).lower()
    INVESTMENT_BANK = (
        "financial services capital markets the goldman sachs group is a "
        "leading global investment banking and asset management firm."
    ).lower()
    SOFTWARE = (
        "technology software - infrastructure builds enterprise software and "
        "cloud platforms for large organizations."
    ).lower()

    def test_a_bitcoin_miner_links_only_to_bitcoin(self):
        self.assertTrue(self.link("BTC-USD", self.BITCOIN_MINER))
        for ref in ("GC=F", "HG=F", "CL=F", "NG=F"):
            self.assertFalse(self.link(ref, self.BITCOIN_MINER), ref)

    def test_a_gold_miner_still_links_to_gold(self):
        self.assertTrue(self.link("GC=F", self.GOLD_MINER))
        self.assertTrue(self.link("HG=F", self.GOLD_MINER))  # says "copper"
        self.assertFalse(self.link("BTC-USD", self.GOLD_MINER))

    def test_goldman_is_not_gold(self):
        """Word boundaries: the substring match put an investment bank
        against gold futures."""
        self.assertFalse(self.link("GC=F", self.INVESTMENT_BANK))
        self.assertFalse(self.link("BTC-USD", self.INVESTMENT_BANK))

    def test_a_software_company_links_to_nothing(self):
        for ref in ("GC=F", "HG=F", "CL=F", "NG=F", "BTC-USD"):
            self.assertFalse(self.link(ref, self.SOFTWARE), ref)

    def test_stems_reach_their_inflections(self):
        """A trailing ``*`` is the escape hatch for the cases where the word
        boundary is the thing in the way."""
        self.assertTrue(self.link("BTC-USD", "holds digital assets on its balance sheet"))
        self.assertTrue(self.link("BTC-USD", "a cryptocurrency exchange"))
        self.assertTrue(self.link("GC=F", "a precious metals royalty company"))

    def test_an_unknown_reference_never_links(self):
        self.assertFalse(self.link("ZZZ=F", self.GOLD_MINER))


class DiscoveryCoverageTest(unittest.TestCase):
    """``symbols_tested`` and ``skipped`` must partition ``symbols_requested``.

    The return used to name only the symbols that found something, so an agent
    splitting a long list across several calls could drop a ticker and still
    report full coverage (issue #208). The partition makes the claim checkable.
    """

    def setUp(self):
        y, x = cointegrated_pair(seed=12)
        self.frames = {"MINER": price_frame(y), "FOODCO": price_frame(y),
                       "GC=F": price_frame(x)}
        self.info = {
            "MINER": {"sector": "Basic Materials", "industry": "Gold Mining"},
            "FOODCO": {"sector": "Consumer Staples", "industry": "Packaged Foods"},
        }

    def discover(self, symbols, **kwargs):
        svc = build([], self.frames, info=self.info)
        return svc.discover_pairs(symbols, references=["GC=F"], **kwargs)

    def assert_partitions(self, result):
        accounted = list(result["symbols_tested"]) + [s["symbol"] for s in result["skipped"]]
        self.assertCountEqual(accounted, result["symbols_requested"])
        self.assertEqual(len(set(accounted)), len(accounted), "a symbol in two buckets")

    def test_every_requested_symbol_lands_in_exactly_one_bucket(self):
        result = self.discover(["MINER", "FOODCO", "GHOST"])
        self.assertEqual(result["symbols_requested"], ["MINER", "FOODCO", "GHOST"])
        self.assertEqual(result["symbols_tested"], ["MINER"])
        self.assert_partitions(result)

    def test_a_skip_carries_the_reason_it_was_skipped(self):
        reasons = {s["symbol"]: s["reason"]
                   for s in self.discover(["FOODCO", "GHOST"])["skipped"]}
        self.assertEqual(reasons["GHOST"], "no price history")
        # Never "not cointegrated": it was never run through the test.
        self.assertEqual(reasons["FOODCO"], "no economic link to any reference")

    def test_a_gate_blocked_symbol_is_not_counted_as_tested(self):
        result = self.discover(["FOODCO"])
        self.assertEqual(result["symbols_tested"], [])
        self.assertEqual(result["count"], 0)

    def test_disabling_the_gate_moves_that_symbol_into_tested(self):
        result = self.discover(["FOODCO"], require_economic_link=False)
        self.assertEqual(result["symbols_tested"], ["FOODCO"])
        self.assertEqual(result["skipped"], [])
        self.assert_partitions(result)

    def test_duplicates_and_casing_collapse_to_one_entry(self):
        result = self.discover(["MINER", "miner", " MINER ", ""])
        self.assertEqual(result["symbols_requested"], ["MINER"])
        self.assert_partitions(result)

    def test_count_is_out_of_tested_not_out_of_requested(self):
        result = self.discover(["MINER", "FOODCO", "GHOST"])
        self.assertEqual(result["count"], len(result["pairs"]))
        self.assertLessEqual(result["count"], len(result["symbols_tested"]))

    def test_an_unusable_reference_panel_says_so_rather_than_blaming_the_symbol(self):
        svc = build([], self.frames, info=self.info)
        result = svc.discover_pairs(["MINER"], references=["NOSUCH=F"])
        self.assertEqual(result["references"], [])
        self.assertEqual(result["skipped"][0]["reason"], "no reference price history")


if __name__ == "__main__":
    unittest.main()
