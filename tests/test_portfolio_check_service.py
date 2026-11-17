"""PortfolioCheckService gathers the owner's lots, window closes and the market state.

No database and no network: the portfolio service, the OHLCV repository and the
prices service are stand-ins, and the market state comes from the synthetic world
in test_portfolio_check_market_state (refreshed on fake gateways), so the expected
labels don't move when the committed file is refreshed. Owner isolation itself is
PortfolioService.list_lots's (its SQL and wrong-owner tests); here the service is
pinned to ask for exactly the owner it was given, open lots only.
"""

import datetime
import json
import math
import unittest
from decimal import Decimal

import pandas as pd

from quantcore.analytics import portfolio_check as pc
from quantcore.services.portfolio_check import PortfolioCheckService, _trading_dates
from scripts import refresh_portfolio_check_market_state as script
from tests.test_portfolio_check_market_state import PER_INDUSTRY, _FakeReference, _FakeYahoo, _world

TODAY = datetime.date(2026, 11, 17)


def _bars(closes: pd.Series) -> pd.DataFrame:
    """A get_bars-shaped frame: UTC timestamps at 05:00 (midnight ET), like stored daily bars."""
    idx = pd.DatetimeIndex([pd.Timestamp(d).tz_localize("America/New_York").tz_convert("UTC") for d in closes.index])
    return pd.DataFrame({"Open": closes.values, "High": closes.values, "Low": closes.values,
                         "Close": closes.values, "Volume": 1000}, index=idx)


class FakePortfolio:
    def __init__(self, lots_by_owner):
        self.lots_by_owner, self.calls = lots_by_owner, []

    def list_lots(self, owner, status="OPEN", force=False):
        self.calls.append((owner, status))
        return [dict(r) for r in self.lots_by_owner.get(owner, [])]


class FakeOhlcv:
    def __init__(self, frames):
        self.frames, self.calls = frames, []

    def daily_history_for_symbols(self, symbols, days):
        self.calls.append((sorted(symbols), days))
        return {s: self.frames[s] for s in symbols if s in self.frames}


class FakePrices:
    def __init__(self, frames=None, fail=()):
        self.frames, self.fail, self.calls = frames or {}, set(fail), []

    def get_history(self, symbol, interval="1d", days=365):
        self.calls.append((symbol, interval, days))
        if symbol in self.fail:
            raise RuntimeError("yahoo down")
        return self.frames.get(symbol, pd.DataFrame())


def _lot(symbol, qty, buy, price, trade_date="2024-01-02", purchase_date=None):
    return {"symbol": symbol, "quantity": Decimal(str(qty)), "purchase_price": Decimal(str(buy)),
            "current_price": None if price is None else Decimal(str(price)),
            "trade_date": trade_date, "purchase_date": purchase_date}


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = _world()
        cls.state = script.refresh(_FakeReference(cls.w), _FakeYahoo(cls.w), today=TODAY)
        cls.closes = cls.w["closes"]
        cls.days = [d.date() for d in cls.closes.index]
        by_code = {i["code"]: i for i in cls.state["industries"]}
        cls.buy_code = next(c for c, i in by_code.items() if i["label"] == "Buy zone")
        cls.buy_ticker = f"{cls.buy_code[:3].upper()}3"
        other = next(c for c, i in by_code.items() if i["label"] != "Buy zone")
        cls.other_ticker = f"{other[:3].upper()}3"

    def series(self, ticker, start=0):
        return pd.Series(self.closes[ticker].to_numpy()[start:], index=self.days[start:])

    def service(self, lots_by_owner, stored=None, prices=None, loader=None):
        portfolio = FakePortfolio(lots_by_owner)
        ohlcv = FakeOhlcv(stored or {})
        prices = prices or FakePrices()
        loads = []

        def load():
            loads.append(1)
            return json.loads(json.dumps(self.state))
        svc = PortfolioCheckService(portfolio=portfolio, ohlcv_repository=ohlcv, prices=prices,
                                    market_state_loader=loader or load, clock=lambda: TODAY)
        return svc, portfolio, ohlcv, prices, loads

    def test_asks_only_for_the_given_owners_open_lots(self):
        svc, portfolio, *_ = self.service({"alice": [_lot("DRU3", 10, 40, 50)], "bob": [_lot("OIL1", 5, 1, 2)]})
        out = svc.check("alice")
        self.assertEqual(portfolio.calls, [("alice", "OPEN")])
        self.assertEqual([h["ticker"] for h in out["holdings"]], ["DRU3"])
        self.assertNotIn("OIL1", json.dumps(out))

    def test_window_closes_come_from_stored_history_in_one_read(self):
        t = "DRU3"
        svc, _, ohlcv, prices, _ = self.service({"o": [_lot(t, 10, 40, 50)]}, stored={t: _bars(self.series(t))})
        out = svc.check("o")
        self.assertEqual(len(ohlcv.calls), 1)
        self.assertEqual(prices.calls, [])
        a = datetime.date.fromisoformat(self.state["window"]["a"])
        self.assertEqual(ohlcv.calls[0][1], (TODAY - a).days + 15)
        h = out["holdings"][0]
        self.assertEqual(h["peer_count"], PER_INDUSTRY - 1)
        window = pc.market_state_inputs(self.state)["window"]
        want, _ = pc.relative_wind(t, "Drugs", float(self.closes.loc[str(window[0]), t]),
                                   float(self.closes.loc[str(window[1]), t]), pd.DataFrame(self.state["peers"]))
        self.assertAlmostEqual(h["vs_peers_12m"], math.expm1(want))

    def test_falls_back_to_prices_when_stored_history_is_short_or_missing(self):
        short, missing = "DRU3", "DRU4"
        stored = {short: _bars(self.series(short, start=len(self.days) - 30))}   # starts after window a
        prices = FakePrices({short: _bars(self.series(short)), missing: _bars(self.series(missing))})
        svc, *_ = self.service({"o": [_lot(short, 1, 1, 50), _lot(missing, 1, 1, 50)]}, stored=stored, prices=prices)
        out = svc.check("o")
        self.assertEqual(sorted(c[0] for c in prices.calls), sorted([missing, short]))
        self.assertTrue(all(h["vs_peers_12m"] is not None for h in out["holdings"]))

    def test_one_failed_history_costs_only_that_stocks_peer_measure(self):
        svc, *_ = self.service({"o": [_lot("DRU3", 1, 1, 50), _lot("DRU4", 1, 1, 50)]},
                               stored={"DRU4": _bars(self.series("DRU4"))}, prices=FakePrices(fail={"DRU3"}))
        with self.assertLogs("quantcore.services.portfolio_check", level="WARNING") as logs:
            out = svc.check("o")
        by = {h["ticker"]: h for h in out["holdings"]}
        self.assertIsNone(by["DRU3"]["vs_peers_12m"])
        self.assertIsNotNone(by["DRU4"]["vs_peers_12m"])
        self.assertIn("history fetch failed for DRU3", logs.output[0])

    def test_lots_costs_dates_and_unpriced(self):
        lots = [_lot("DRU3", 10, 40, 50, trade_date=None, purchase_date="2020-05-01"),
                _lot("DRU3", 5, 60, 50, trade_date="2026-10-01"),
                _lot("DRU5", 3, 10, None)]
        svc, *_ = self.service({"o": lots})
        out = svc.check("o")
        by = {h["ticker"]: h for h in out["holdings"]}
        self.assertEqual(by["DRU3"]["cost"], 10 * 40 + 5 * 60)
        self.assertEqual(by["DRU3"]["shares"], 15.0)
        self.assertEqual(out["unpriced"], ["DRU5"])
        self.assertEqual(out["total_value"], 15 * 50.0)
        self.assertEqual(by["DRU3"]["company"], "Dru3")          # the SEC name, title-cased

    def test_holdings_outside_the_market_state_are_review(self):
        svc, *_ = self.service({"o": [_lot("ZZZZ", 1, 1, 10), _lot("DRU3", 1, 1, 10)]})
        by = {h["ticker"]: h for h in svc.check("o")["holdings"]}
        self.assertEqual(by["ZZZZ"]["action"], "Review")
        self.assertIsNone(by["ZZZZ"]["industry_code"])

    def test_params_reach_the_decision(self):
        t = self.buy_ticker
        # 8% of the portfolio, alone in a Buy-zone industry: under both default caps.
        svc, *_ = self.service({"o": [_lot(t, 8, 1, 10), _lot(self.other_ticker, 92, 1, 10)]})
        default = {h["ticker"]: h["action"] for h in svc.check("o")["holdings"]}
        self.assertEqual(default[t], "Buy / add")
        tight = svc.check("o", params=pc.CheckParams(position_cap=0.05))
        self.assertEqual({h["ticker"]: h["action"] for h in tight["holdings"]}[t], "Hold (at size limit)")
        self.assertEqual(tight["params"]["position_cap"], 0.05)

    def test_empty_portfolio(self):
        svc, *_ = self.service({})
        out = svc.check("nobody")
        self.assertEqual((out["holdings"], out["total_value"], out["unpriced"], out["action_counts"]),
                         ([], 0.0, [], []))
        self.assertEqual(out["ideas"], [])
        self.assertEqual(len(out["industries"]), 49)

    def test_response_metadata_and_json(self):
        svc, *_ = self.service({"o": [_lot("DRU3", 1, 1, 10)]})
        out = svc.check("o")
        self.assertEqual(out["as_of"], "2026-11-17")
        self.assertEqual(out["market_state"]["industries_asof"], "2026-09")
        self.assertEqual(out["market_state"]["window"], self.state["window"])
        json.dumps(out, allow_nan=False)

    def test_market_state_is_loaded_and_validated_once(self):
        svc, _, _, _, loads = self.service({"o": [_lot("DRU3", 1, 1, 10)]})
        svc.check("o")
        svc.check("o")
        self.assertEqual(len(loads), 1)
        bad, *_ = self.service({"o": []}, loader=lambda: {"schema": 1})
        with self.assertRaisesRegex(ValueError, "industries"):
            bad.check("o")

    def test_trading_dates_from_utc_bars(self):
        idx = pd.DatetimeIndex([pd.Timestamp("2026-11-13 05:00", tz="UTC"), pd.Timestamp("2026-07-01 04:00", tz="UTC")])
        self.assertEqual(_trading_dates(idx), [datetime.date(2026, 11, 13), datetime.date(2026, 7, 1)])
        self.assertEqual(_trading_dates(pd.DatetimeIndex(["2026-11-13"])), [datetime.date(2026, 11, 13)])


class CloseOnOrBeforeTests(unittest.TestCase):
    def test_last_close_within_the_gap(self):
        s = pd.Series([10.0, 11.0, float("nan")], index=[datetime.date(2026, 1, 2), datetime.date(2026, 1, 5),
                                                         datetime.date(2026, 1, 6)])
        self.assertEqual(pc.close_on_or_before(s, datetime.date(2026, 1, 6)), 11.0)
        self.assertEqual(pc.close_on_or_before(s, datetime.date(2026, 1, 4)), 10.0)
        self.assertIsNone(pc.close_on_or_before(s, datetime.date(2026, 1, 1)))
        self.assertIsNone(pc.close_on_or_before(s, datetime.date(2026, 2, 1)))     # 27 days stale


if __name__ == "__main__":
    unittest.main()
