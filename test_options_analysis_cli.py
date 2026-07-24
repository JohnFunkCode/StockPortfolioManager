"""Tests for fastMCPTest/options_analysis.py — the hybrid MCP-wrapper + CLI
reporter (85%-coverage campaign; previously 0%).

The console reporters are pure formatting over SecurityAnalysis fixtures and
the real (pure) screening service methods; output is captured and asserted on
the load-bearing strings/branches. The MCP tool bodies are one rest_client
call deep — pinned with a mocked client (URL + params).
"""
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

# Swap in the test DSN BEFORE quantcore.db is imported transitively (DB_DSN
# freezes at import time; this module sorts ahead of DB-backed suites).
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip().startswith("QUANTCORE_TEST_DB_DSN="):
            os.environ["QUANTCORE_DB_DSN"] = _line.split("=", 1)[1].strip()
            break

from quantcore.db_safety import assert_not_production  # noqa: E402

assert_not_production()

from fastMCPTest import options_analysis as oa  # noqa: E402
from quantcore.services.options_screening import (  # noqa: E402
    BollingerBands,
    IVAnalysis,
    OptionsSummary,
    PutCallAnalysis,
    SecurityAnalysis,
)


def out_of(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


def security(symbol="TST", price=100.0, **kw):
    sec = SecurityAnalysis(
        symbol=symbol, name=f"{symbol} Co", tags=[], price=price,
        bands=BollingerBands(upper=110.0, middle=100.0, lower=90.0),
        options=kw.pop("options", None),
    )
    for key, value in kw.items():
        setattr(sec, key, value)
    return sec


def options_summary(**kw):
    base = dict(expiration="2026-08-21", put_call_ratio=1.0,
                total_call_oi=1_000, total_put_oi=1_000,
                total_call_volume=1_000, total_put_volume=1_000,
                avg_call_iv=40.0, avg_put_iv=40.0,
                atm_calls=[], atm_puts=[])
    base.update(kw)
    return OptionsSummary(**base)


def pc_analysis(**kw):
    base = dict(near_expiry="2026-08-21", near_oi_pc=1.4, near_vol_pc=0.9,
                near_atm_pc=1.1, mid_expiry="2026-09-18", mid_oi_pc=1.0,
                term_skew=0.4, vol_oi_ratio=0.64,
                put_unwinding=True, fresh_put_buying=False,
                near_term_fear=True)
    base.update(kw)
    return PutCallAnalysis(**base)


IV = IVAnalysis(current_iv=55.0, hv_30=40.0, iv_vs_hv=1.4, hv_52w_low=20.0,
                hv_52w_high=80.0, iv_rank=58.0, iv_percentile=61.0,
                label="elevated fear")

ATM_CALL = {"strike": 100.0, "ask": 3.0, "iv": 40.0}
ATM_PUT = {"strike": 100.0, "ask": 3.0, "iv": 40.0}


class TestFormattingHelpers(unittest.TestCase):
    def test_bb_bar_marker_placement_and_clamping(self):
        self.assertEqual(oa._bb_bar(0.0)[1], "█")          # lower band -> far left
        self.assertEqual(oa._bb_bar(1.2)[-2], "█")         # breakout clamps to far right
        bar = oa._bb_bar(0.5, width=20)
        self.assertEqual(len(bar), 22)                     # [ + 20 + ]
        self.assertEqual(bar.index("█"), 11)               # middle

    def test_pc_label_bands(self):
        self.assertEqual(oa._pc_label(None), "n/a")
        self.assertIn("very bullish", oa._pc_label(0.4))
        self.assertIn("bullish", oa._pc_label(0.7))
        self.assertIn("neutral", oa._pc_label(1.0))
        self.assertIn("bearish", oa._pc_label(1.7))
        self.assertIn("very bearish", oa._pc_label(2.5))

    def test_print_section_frames_the_title(self):
        text = out_of(oa.print_section, "HELLO")
        self.assertIn("HELLO", text)
        self.assertIn(oa.SEPARATOR, text)


class TestTradePrinters(unittest.TestCase):
    TRADE = {
        "strike": 100.0, "expiration": "2026-08-21", "ask": 3.0, "iv": 70.0,
        "target_price": 110.0, "profit_at_target_per_contract": 700.0,
        "roi_at_target_pct": 233.0, "contracts": 1, "total_cost": 300.0,
        "suggest_spread": True,
    }

    def test_call_trade_lines_include_spread_note_when_iv_high(self):
        text = out_of(oa._print_call_trade, self.TRADE)
        self.assertIn("CALL strike=100.0", text)
        self.assertIn("bull call spread", text)
        self.assertIn("ROI=233%", text)

    def test_put_trade_without_spread_note(self):
        trade = dict(self.TRADE, suggest_spread=False, iv=40.0)
        text = out_of(oa._print_put_trade, trade)
        self.assertIn("PUT  strike=100.0", text)
        self.assertNotIn("debit spread", text)


class TestCandidateSections(unittest.TestCase):
    def bullish_sec(self):
        sec = security(
            symbol="UPP", price=91.0,
            options=options_summary(put_call_ratio=0.4, total_call_volume=60_000,
                                    atm_calls=[dict(ATM_CALL)],
                                    atm_puts=[dict(ATM_PUT)]),
            iv=IV, pc=pc_analysis(),
            days_to_earnings=45, news_signal="BULLISH",
            news_top_headline="Upgrade to overweight",
        )
        sec.long_score = 9.0
        sec.put_score = 0.0
        sec.long_reason = "oversold; very bullish P/C"
        return sec

    def bearish_sec(self):
        sec = security(
            symbol="DWN", price=111.0,
            options=options_summary(put_call_ratio=2.4, total_put_oi=60_000,
                                    atm_calls=[dict(ATM_CALL)],
                                    atm_puts=[dict(ATM_PUT)]),
            iv=IV,
            pc=pc_analysis(put_unwinding=False, fresh_put_buying=True,
                           near_term_fear=False),
            days_to_earnings=3,   # trips the earnings guardrail on both sides
        )
        sec.put_score = 8.0
        sec.long_score = 0.0
        sec.put_reason = "overbought; heavy puts"
        return sec

    def test_long_candidates_full_render(self):
        text = out_of(oa.print_long_candidates, [self.bullish_sec()], budget=1000.0)
        self.assertIn("LONG / BOUNCE CANDIDATES", text)
        self.assertIn("#1  UPP", text)
        self.assertIn("[UNWINDING]", text)
        self.assertIn("[NEAR-TERM-FEAR]", text)
        self.assertIn("45d to earnings", text)
        self.assertIn('NEWS BULLISH  "Upgrade to overweight"', text)
        self.assertIn("CALL strike=", text)                # trade built + printed
        self.assertIn("CALL PORTFOLIO SUMMARY", text)
        self.assertIn("Total deployed", text)

    def test_long_candidates_empty(self):
        text = out_of(oa.print_long_candidates, [security()], budget=1000.0)
        self.assertIn("No candidates met the scoring threshold.", text)

    def test_put_candidates_guardrails_announced(self):
        text = out_of(oa.print_put_candidates, [self.bearish_sec()], budget=1000.0)
        self.assertIn("PUT / BEARISH CANDIDATES", text)
        self.assertIn("[FRESH-PUTS]", text)
        self.assertIn("GUARDRAIL (PUT)", text)             # earnings blackout
        self.assertIn("earnings in 3d", text)
        self.assertNotIn("PUT  strike=", text)             # trade vetoed
        self.assertNotIn("PUT PORTFOLIO SUMMARY", text)    # nothing to summarize

    def test_put_candidates_empty(self):
        text = out_of(oa.print_put_candidates, [security()], budget=500.0)
        self.assertIn("No candidates met the scoring threshold.", text)


class TestPortfolioSummaries(unittest.TestCase):
    TRADE = {
        "symbol": "UPP", "strike": 100.0, "expiration": "2026-08-21",
        "ask": 3.0, "iv": 40.0, "target_price": 110.0,
        "profit_at_target_per_contract": 700.0, "roi_at_target_pct": 233.0,
        "contracts": 1, "total_cost": 300.0, "suggest_spread": False,
        "put_score": 8.0, "long_score": 8.0,
    }

    def test_put_summary_deploys_and_reports_leftover(self):
        text = out_of(oa.print_put_portfolio_summary, [dict(self.TRADE)], 1000.0)
        self.assertIn("PUT PORTFOLIO SUMMARY", text)
        self.assertIn("Total deployed", text)
        self.assertIn("Remaining:", text)

    def test_call_summary(self):
        text = out_of(oa.print_call_portfolio_summary, [dict(self.TRADE)], 1000.0)
        self.assertIn("CALL PORTFOLIO SUMMARY", text)
        self.assertIn("$100.0 call", text)

    def test_empty_trades_print_nothing(self):
        self.assertEqual(out_of(oa.print_put_portfolio_summary, [], 1000.0), "")
        self.assertEqual(out_of(oa.print_call_portfolio_summary, [], 1000.0), "")

    def test_unaffordable_trades_reported(self):
        rich = dict(self.TRADE, ask=50.0)                  # $5,000/contract
        text = out_of(oa.print_put_portfolio_summary, [rich], 1000.0)
        self.assertIn("No trades fit within budget.", text)


class TestSkipList(unittest.TestCase):
    def test_lists_only_signal_free_securities(self):
        quiet = security(symbol="ZZZ")
        loud = security(symbol="AAA")
        loud.long_score = 5.0
        text = out_of(oa.print_skip_list, [quiet, loud])
        self.assertIn("ZZZ", text)
        self.assertNotIn("AAA", text)
        self.assertEqual(out_of(oa.print_skip_list, [loud]), "")


class TestMcpToolBodies(unittest.TestCase):
    """The @mcp.tool bodies are exactly one rest_client call deep (Rule 6)."""

    def test_health_check_shape(self):
        out = oa.mcp_health_check()
        self.assertEqual(out["server"], "options-analysis-server")
        self.assertIn("fastmcp_version", out)
        self.assertIn("watchlist_default", out)

    def test_watchlist_and_symbol_tools_hit_their_routes(self):
        with patch.object(oa, "rest_client") as rc:
            rc.get.return_value = {"ok": True}
            oa.analyze_options_watchlist(puts_budget=500.0, top_n=5)
            path = rc.get.call_args[0][0]
            self.assertIn("options/screen-watchlist", path)

            oa.analyze_options_symbol("intc", puts_budget=250.0)
            path = rc.get.call_args[0][0]
            self.assertIn("/securities/intc/options/screen", path)

    def test_contracts_and_spread_tools(self):
        with patch.object(oa, "rest_client") as rc:
            rc.get.return_value = {"ok": True}
            rc.post.return_value = {"ok": True}
            oa.get_option_contracts("WMT", ["2026-08-21"], [120.0], kind="put")
            self.assertIn("options/contracts", rc.get.call_args[0][0])
            self.assertEqual(rc.get.call_args[1]["kind"], "put")

            oa.price_vertical_spread("WMT", "2026-08-21", 120.0, 125.0)
            self.assertIn("options/vertical-spread", rc.post.call_args[0][0])
            body = rc.post.call_args[1]["json"]
            self.assertEqual(body["long_strike"], 120.0)
            self.assertEqual(body["short_strike"], 125.0)


if __name__ == "__main__":
    unittest.main()


class TestMainSingleSymbol(unittest.TestCase):
    """End-to-end CLI run for --symbol: real screening scoring/printing over a
    patched fetch, no DB writes (--no-persist), no news (--no-news)."""

    def test_main_renders_the_full_report(self):
        import sys
        from types import SimpleNamespace
        from quantcore.services.options_screening import OptionsScreeningService

        svc = OptionsScreeningService(
            ohlcv_repository=Mock(), yfinance_gateway=Mock(), prices=Mock()
        )
        sec = security(
            symbol="UPP", price=91.0,
            options=options_summary(put_call_ratio=0.4,
                                    atm_calls=[dict(ATM_CALL)],
                                    atm_puts=[dict(ATM_PUT)]),
            iv=IV,
        )
        bag = SimpleNamespace(options_screening=svc)

        argv = ["options_analysis.py", "--symbol", "upp",
                "--no-persist", "--no-news", "--puts-budget", "500"]
        with patch.object(oa, "get_services", return_value=bag), \
             patch("quantcore.db.init_schema"), \
             patch.object(svc, "fetch_security", return_value=sec), \
             patch.object(sys, "argv", argv):
            text = out_of(oa.main)

        self.assertIn("Options Analysis Engine", text)
        self.assertIn("Symbols   : 1", text)
        self.assertIn("News      : disabled (--no-news)", text)
        self.assertIn("LONG / BOUNCE CANDIDATES", text)
        self.assertIn("UPP", text)
