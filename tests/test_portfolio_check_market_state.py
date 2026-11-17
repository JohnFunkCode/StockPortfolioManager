"""The Portfolio Check market state: its sources, builder, validator and refresh script.

No network: the gateway is driven through a fake session, and the refresh runs on
fake gateways over synthetic data shaped like the real thing (49 industries, a few
hundred constituents, ~14 months of closes). The ported Sell edge was checked
against the prototype's full 1926–2026 run once (docs/proposals/portfolio-check-plan.md,
step 2 checkpoint); here it is pinned on a case worked by hand.
"""

import io
import json
import math
import os
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import requests

from quantcore.analytics import portfolio_check as pc
from quantcore.gateways import market_reference_gateway as mrg
from scripts import refresh_portfolio_check_market_state as script

COMMITTED_STATE = Path(__file__).resolve().parent.parent / "quantcore" / "data" / "portfolio_check_market_state.json"

FF_TEXT = """This file was created by CMPT_IND_RETS using the 202609 CRSP database.

  Average Value Weighted Returns -- Monthly
,Agric,Food ,Soda
202607,   1.50,  -0.20, -99.99
202608,   2.00,   0.30,   1.10

  Average Firm Size
,Agric,Food ,Soda
202607, 100.00, 200.00, -999
202608, 101.00, 201.00,  50.00

 Annual stuff that follows
"""

SIC_TEXT = """ 1 Agric  Agriculture
          0100-0199 Agricultural production - crops
          0200-0299 Agricultural production - livestock

 2 Food   Food Products
          2000-2009 Food and kindred products
"""

HTML = """<html><body><table class="wikitable" id="other"><tr><td>x</td></tr></table>
<table class="wikitable sortable" id="constituents">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th><th>HQ</th></tr>
<tr><td><a href="/x">MMM</a></td><td><a href="/y">3M</a></td><td>Industrials</td><td>Industrial Conglomerates</td><td>Saint Paul</td></tr>
<tr><td><a>BRK.B</a></td><td>Berkshire
 Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td><td>Omaha</td></tr>
</table></body></html>"""


class ParserTests(unittest.TestCase):
    def test_ff_section_stops_at_the_section_end_and_blanks_missing_codes(self):
        df = mrg.parse_ff_section(FF_TEXT, "Average Value Weighted Returns -- Monthly")
        self.assertEqual(list(df.columns), ["Agric", "Food", "Soda"])
        self.assertEqual(list(df.index), ["202607", "202608"])
        self.assertTrue(math.isnan(df.loc["202607", "Soda"]))
        self.assertEqual(df.loc["202608", "Food"], 0.30)
        size = mrg.parse_ff_section(FF_TEXT, "Average Firm Size")
        self.assertTrue(math.isnan(size.loc["202607", "Soda"]))

    def test_ff_section_missing_is_an_error(self):
        with self.assertRaises(ValueError):
            mrg.parse_ff_section(FF_TEXT, "Number of Firms in Portfolios")

    def test_sic_ranges(self):
        self.assertEqual(mrg.parse_sic_ranges(SIC_TEXT),
                         [(100, 199, "Agric"), (200, 299, "Agric"), (2000, 2009, "Food")])

    def test_constituents_table_only_and_yahoo_spelling(self):
        t = mrg.parse_constituents_html(HTML)
        self.assertEqual(list(t["ticker"]), ["MMM", "BRK.B"])
        self.assertEqual(list(t["yahoo"]), ["MMM", "BRK-B"])
        self.assertEqual(t.loc[1, "name"], "Berkshire Hathaway")
        self.assertEqual(t.loc[0, "sub_industry"], "Industrial Conglomerates")

    def test_constituents_table_missing_is_an_error(self):
        with self.assertRaises(ValueError):
            mrg.parse_constituents_html("<table id='other'><tr><td>x</td></tr></table>")

    def test_ff49_for_sic(self):
        ranges = mrg.parse_sic_ranges(SIC_TEXT)
        self.assertEqual(pc.ff49_for_sic(150, ranges), "Agric")
        self.assertEqual(pc.ff49_for_sic(2005, ranges), "Food")
        self.assertEqual(pc.ff49_for_sic(9999, ranges), "Other")
        self.assertIsNone(pc.ff49_for_sic(None, ranges))


class _Resp:
    def __init__(self, status=200, text="", content=b"", payload=None):
        self.status_code, self.text, self.content, self._payload = status, text, content, payload

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}")


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        return self.responses[url] if url in self.responses else self.responses["*"]


def _zip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("file.csv", text.encode("latin1"))
    return buf.getvalue()


class GatewayTests(unittest.TestCase):
    def test_sec_refuses_without_a_user_agent(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("SEC_USER_AGENT", None)
            gw = mrg.MarketReferenceGateway(session=_Session({}))
        self.assertFalse(gw.has_sec_user_agent)
        with self.assertRaises(mrg.SecUserAgentMissing):
            gw.sec_company_tickers()

    def test_sec_calls_carry_the_user_agent_and_are_spaced_out(self):
        session = _Session({"*": _Resp(payload={"sic": "3571", "sicDescription": "Computers", "name": "Apple"})})
        slept = []
        gw = mrg.MarketReferenceGateway(sec_user_agent="Test <ops@example.org>", session=session,
                                        sleep=slept.append, clock=lambda: 100.0)
        self.assertEqual(gw.sec_sic(320193), {"sic": 3571, "sic_desc": "Computers", "name": "Apple"})
        gw.sec_sic(320193)
        self.assertEqual(session.calls[0][0], "https://data.sec.gov/submissions/CIK0000320193.json")
        self.assertEqual(session.calls[0][1]["User-Agent"], "Test <ops@example.org>")
        self.assertEqual(slept, [mrg.SEC_MIN_INTERVAL_SECONDS])  # the clock never moved

    def test_sec_not_found_is_unclassified_but_blocked_stops(self):
        gw = mrg.MarketReferenceGateway(sec_user_agent="t", session=_Session({"*": _Resp(404)}), sleep=lambda s: None)
        self.assertEqual(gw.sec_sic(1)["sic"], None)
        for status in (403, 429):
            gw = mrg.MarketReferenceGateway(sec_user_agent="t", session=_Session({"*": _Resp(status)}),
                                            sleep=lambda s: None)
            with self.assertRaises(requests.HTTPError):
                gw.sec_sic(1)

    def test_french_files_are_unzipped_and_carry_no_contact(self):
        session = _Session({mrg.SIC49_URL: _Resp(content=_zip(SIC_TEXT))})
        gw = mrg.MarketReferenceGateway(sec_user_agent="secret contact", session=session)
        self.assertEqual(gw.ff49_sic_ranges()[0], (100, 199, "Agric"))
        self.assertEqual(session.calls[0][1]["User-Agent"], mrg.DEFAULT_USER_AGENT)

    def test_sec_company_tickers(self):
        payload = json.dumps({"0": {"cik_str": 320193, "ticker": "aapl"}, "1": {"cik_str": 1067983, "ticker": "BRK-B"}})
        gw = mrg.MarketReferenceGateway(sec_user_agent="t", session=_Session({"*": _Resp(text=payload)}))
        self.assertEqual(gw.sec_company_tickers(), {"AAPL": 320193, "BRK-B": 1067983})


class SellEdgeTests(unittest.TestCase):
    def test_worked_case(self):
        # Three industries; C is always weakest, so from its second review it is a
        # Sell. Its log share falls 0.01 a month, so 12 months on it is 0.12 lower.
        idx = pd.period_range("2000-01", periods=40, freq="M")
        p12 = pd.DataFrame({"A": 0.9, "B": 0.6, "C": 0.3}, index=idx)
        rel = pd.DataFrame({"A": 0.0, "B": 0.0, "C": [-0.01 * i for i in range(40)]}, index=idx)
        edge = pc.label_forward_edge(p12, rel, first="2000-01", since="2000-01", min_industries=3)
        self.assertAlmostEqual(edge, math.expm1(-0.12))
        self.assertAlmostEqual(pc.label_forward_edge(p12, rel, label="Buy zone", first="2000-01",
                                                     since="2000-01", min_industries=3), 0.0)

    def test_no_reviews_is_nan(self):
        idx = pd.period_range("2000-01", periods=10, freq="M")
        p12 = pd.DataFrame({"A": 0.3}, index=idx)
        self.assertTrue(math.isnan(pc.label_forward_edge(p12, p12 * 0, first="2000-01", min_industries=1)))


# ------------------------------------------------ synthetic market world ----
CODES = list(pc.NAMES49)
PER_INDUSTRY = 7


def _world(seed=7):
    rng = np.random.default_rng(seed)
    months = [p.strftime("%Y%m") for p in pd.period_range("1990-01", "2026-09", freq="M")]
    ret = pd.DataFrame(rng.normal(0.8, 5, (len(months), len(CODES))).round(2), index=months, columns=CODES)
    size = pd.DataFrame(rng.uniform(100, 5000, (len(months), len(CODES))).round(2), index=months, columns=CODES)
    nfirms = pd.DataFrame(rng.integers(5, 300, (len(months), len(CODES))).astype(float), index=months, columns=CODES)
    bm = pd.DataFrame(rng.uniform(0.1, 1.5, (37, len(CODES))).round(2), index=[str(y) for y in range(1990, 2027)],
                      columns=CODES)
    cons = pd.DataFrame([{"ticker": f"{c[:3].upper()}{k}", "name": f"{c} Co {k}", "sector": "x", "sub_industry": "y"}
                         for c in CODES for k in range(PER_INDUSTRY)])
    cons.loc[0, "ticker"] = "BRK.B"                           # a dotted ticker, Yahoo-spelt BRK-B
    cons["yahoo"] = cons["ticker"].str.replace(".", "-", regex=False)
    days = pd.bdate_range(end="2026-11-13", periods=300)
    closes = pd.DataFrame(np.exp(np.cumsum(rng.normal(0, 0.01, (len(days), len(cons))), axis=0)) * 50,
                          index=days, columns=cons["yahoo"])
    ranges = [(1000 + 10 * i, 1009 + 10 * i, c) for i, c in enumerate(CODES)]
    sic = {t: 1000 + 10 * (i // PER_INDUSTRY) for i, t in enumerate(cons["ticker"])}
    sic["CRSP"] = 1000 + 10 * CODES.index("Drugs")              # an extra, non-S&P ticker
    return {"tables": {"ret": ret, "size": size, "nfirms": nfirms, "bm": bm}, "cons": cons, "closes": closes,
            "ranges": ranges, "sic": sic}


class _FakeReference:
    def __init__(self, w):
        self.w = w
        self.ciks = {t.replace(".", "-"): 1000 + i for i, t in enumerate(sorted(w["sic"]))}
        self.by_cik = {cik: t for t, cik in self.ciks.items()}

    def ff49_tables(self):
        return {k: v.copy() for k, v in self.w["tables"].items()}

    def ff49_sic_ranges(self):
        return self.w["ranges"]

    def sp500_constituents(self):
        return self.w["cons"][["ticker", "name", "sector", "sub_industry", "yahoo"]].copy()

    def sec_company_tickers(self):
        return dict(self.ciks)

    def sec_sic(self, cik):
        t = self.by_cik[cik]
        return {"sic": self.w["sic"].get(t.replace("-", ".")) or self.w["sic"].get(t), "sic_desc": "desc",
                "name": t.title()}


class _FakeYahoo:
    def __init__(self, w, no_shares=(), no_prices=()):
        self.w, self.no_shares, self.no_prices = w, set(no_shares), set(no_prices)

    def fast_info(self, symbol):
        if symbol in self.no_shares:
            raise KeyError("shares")
        return {"shares": 1_000_000.0 + hash(symbol) % 1000}

    def download(self, tickers, period, auto_adjust):
        cols = [t for t in tickers if t not in self.no_prices]
        close = self.w["closes"][cols]
        close.columns = pd.MultiIndex.from_product([["Close"], cols])
        return close


class RefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = _world()
        cls.state = script.refresh(_FakeReference(cls.w), _FakeYahoo(cls.w, no_shares={"AGR1"}, no_prices={"AGR2"}),
                                   today=date(2026, 11, 17), extra_tickers=["crsp", " "])

    def test_a_full_state_that_validates_and_serialises(self):
        pc.validate_market_state(self.state)
        json.dumps(self.state, allow_nan=False)
        self.assertEqual(self.state["generated"], "2026-11-17")
        self.assertEqual(self.state["industries_asof"], "2026-09")
        self.assertEqual(len(self.state["industries"]), 49)
        self.assertEqual(self.state["sell_edge"]["period"], "1931–2026")

    def test_constituents_without_shares_or_prices_are_left_out(self):
        tickers = {p["ticker"] for p in self.state["peers"]}
        self.assertNotIn("AGR1", tickers)
        self.assertNotIn("AGR2", tickers)
        self.assertIn("BRK.B", tickers)
        self.assertEqual(len(self.state["peers"]), 49 * PER_INDUSTRY - 2)

    def test_window_is_fixed_on_the_trading_days_of_the_closes(self):
        days = self.w["closes"].index
        self.assertEqual(self.state["window"], {"a": days[-pc.WIN_A].date().isoformat(),
                                                "b": days[-pc.WIN_B].date().isoformat(),
                                                "prices_asof": days[-1].date().isoformat()})

    def test_extra_tickers_are_classified(self):
        self.assertEqual(self.state["classes"]["CRSP"]["ff49"], "Drugs")
        self.assertEqual(self.state["classes"]["BRK.B"]["ff49"], CODES[0])

    def test_industry_state_matches_a_direct_computation(self):
        t = self.w["tables"]
        ret, cap, bm = pc.industry_inputs(t["ret"].copy(), t["size"].copy(), t["nfirms"].copy(), t["bm"].copy())
        ind, _ = pc.industry_state(ret, cap, bm)
        self.assertEqual([i["code"] for i in self.state["industries"]], list(ind["code"]))

    def test_the_state_drives_check_portfolio(self):
        m = pc.market_state_inputs(self.state)
        a, b = m["window"]
        closes = self.w["closes"]
        held = "DRU3"
        lots = [{"ticker": held, "shares": 10, "price": float(closes[held].iloc[-1]), "cost": 400.0,
                 "acquired": "2024-01-02"},
                {"ticker": "CRSP", "shares": 5, "price": 40.0}]
        out = pc.check_portfolio(lots, m["industries"], m["peers"], m["codes"],
                                 {held: (float(closes.loc[str(a), held]), float(closes.loc[str(b), held]))},
                                 today=date(2026, 11, 17), sell_edge=m["sell_edge"],
                                 sell_edge_period=m["sell_edge_period"])
        by = {h["ticker"]: h for h in out["holdings"]}
        self.assertEqual(by[held]["industry_code"], "Drugs")
        self.assertEqual(by[held]["peer_count"], PER_INDUSTRY - 1)
        self.assertIsNotNone(by[held]["vs_peers_12m"])
        self.assertEqual(by["CRSP"]["industry_code"], "Drugs")
        self.assertIsNone(by["CRSP"]["vs_peers_12m"])          # no window prices given for it
        json.dumps(out, allow_nan=False)


class ValidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        w = _world()
        cls.good = script.refresh(_FakeReference(w), _FakeYahoo(w), today=date(2026, 11, 17))

    def broken(self, **changes):
        s = json.loads(json.dumps(self.good))
        for k, v in changes.items():
            s[k] = v
        return s

    def assert_rejected(self, state, words):
        with self.assertRaisesRegex(ValueError, words):
            pc.validate_market_state(state)

    def test_rejections(self):
        self.assert_rejected(self.broken(schema=2), "schema")
        self.assert_rejected(self.broken(peers=self.good["peers"][:10]), "10 S&P 500 peers")
        self.assert_rejected(self.broken(industries=self.good["industries"][:5]), "5 industries")
        self.assert_rejected(self.broken(sell_edge={"value": None}), "sell edge")
        w = dict(self.good["window"], a=self.good["window"]["b"])
        self.assert_rejected(self.broken(window=w), "out of order")
        inds = [dict(i) for i in self.good["industries"]]
        inds[0]["label"] = "Moon"
        self.assert_rejected(self.broken(industries=inds), "unknown industry labels")
        peers = [dict(p) for p in self.good["peers"]]
        peers[0]["capA"] = 0
        self.assert_rejected(self.broken(peers=peers), "positive share count")


class ScriptCliTests(unittest.TestCase):
    def test_check_reports_a_good_file_and_fails_a_broken_one(self):
        w = _world()
        state = script.refresh(_FakeReference(w), _FakeYahoo(w), today=date(2026, 11, 17))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            script.write_state(state, path)
            with mock.patch("sys.stdout", new=io.StringIO()) as out:
                self.assertEqual(script.main(["--check", "--output", str(path)]), 0)
            self.assertIn("343 classified tickers", out.getvalue())
            path.write_text("{}", encoding="utf-8")
            with mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(script.main(["--check", "--output", str(path)]), 1)

    def test_refuses_to_start_without_an_sec_user_agent(self):
        with mock.patch.dict(os.environ), mock.patch("sys.stderr", new=io.StringIO()) as err, \
                mock.patch.object(script, "refresh") as refresh:
            os.environ.pop("SEC_USER_AGENT", None)
            self.assertEqual(script.main(["--output", os.devnull]), 2)
        refresh.assert_not_called()
        self.assertIn("SEC_USER_AGENT", err.getvalue())


@unittest.skipUnless(COMMITTED_STATE.exists(), "no committed market-state file yet")
class CommittedStateTests(unittest.TestCase):
    def test_the_committed_file_validates(self):
        pc.validate_market_state(json.loads(COMMITTED_STATE.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
