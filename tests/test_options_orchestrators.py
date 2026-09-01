"""Tests for OptionsService's Polygon backfill + bulk snapshot refresh
orchestrators (85%-campaign part 6). Polygon/prices/gateway are Mocks; the
backfill's per-date state machine (stored/duplicate/no_data/error/402/400)
is walked branch by branch with literal Polygon payloads.
"""
import os
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import pytz

from quantcore.gateways.polygon_gateway import PolygonPlanError  # noqa: E402
from quantcore.services.options import OptionsService  # noqa: E402


class _FrozenToday(date):
    """Fixes date.today() to a Wednesday so the backfill's weekday math is
    deterministic. A trailing N-day window's weekday count depends on which
    day of the week "today" falls on (a weekend "today" drops only one
    weekend day from the window instead of two), so leaving it unfrozen made
    test_per_date_state_machine flaky depending on which day CI ran."""

    @classmethod
    def today(cls):
        return date(2026, 1, 14)


def polygon_contract(kind="call", exp="2026-08-21", oi=100, vol=10, iv=0.30,
                     price=100.0):
    return {
        "details": {"contract_type": kind, "expiration_date": exp},
        "open_interest": oi,
        "implied_volatility": iv,
        "day": {"volume": vol},
        "underlying_asset": {"price": price},
    }


class OrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self.yf = Mock()
        self.options = Mock()
        self.polygon = Mock()
        self.prices = Mock()
        self.service = OptionsService(
            ohlcv_repository=Mock(),
            yfinance_gateway=self.yf,
            options_repository=self.options,
            polygon_gateway=self.polygon,
            prices=self.prices,
        )


class TestBackfill(OrchestratorTestBase):
    def test_late_evening_starts_from_eastern_calendar_date(self):
        self.polygon.has_key = True
        self.polygon.option_snapshots.return_value = []
        evening = pytz.timezone("America/New_York").localize(
            datetime(2026, 8, 14, 23, 35)
        )
        payload, status = self.service.backfill_options_history(
            "INTC", days=3, skip_existing=False, now=evening
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [call.args[1] for call in self.polygon.option_snapshots.call_args_list],
            ["2026-08-11", "2026-08-12", "2026-08-13"],
        )

    def test_missing_api_key_is_a_400(self):
        self.polygon.has_key = False
        payload, status = self.service.backfill_options_history("intc")
        self.assertEqual(status, 400)
        self.assertIn("POLYGON_API_KEY", payload["error"])

    def test_fully_backfilled_range_short_circuits(self):
        self.polygon.has_key = True
        all_days = {
            (date.today() - timedelta(days=o)).isoformat() for o in range(0, 20)
        }
        self.options.get_snapshot_dates.return_value = all_days
        payload, status = self.service.backfill_options_history("INTC", days=5)
        self.assertEqual(status, 200)
        self.assertEqual(payload["stored"], 0)
        self.assertIn("already have snapshots", payload["note"])

    def test_plan_error_is_a_402(self):
        self.polygon.has_key = True
        self.options.get_snapshot_dates.return_value = set()
        self.polygon.option_snapshots.side_effect = PolygonPlanError(
            403, "upgrade required"
        )
        payload, status = self.service.backfill_options_history("INTC", days=5)
        self.assertEqual(status, 402)
        self.assertEqual(payload["polygon_status"], 403)

    def test_per_date_state_machine(self):
        self.polygon.has_key = True
        self.options.get_snapshot_dates.return_value = set()
        # Four trading days -> error, no_data, stored, duplicate (in order).
        self.polygon.option_snapshots.side_effect = [
            requests.RequestException("polygon hiccup"),
            [],
            [polygon_contract(), polygon_contract(kind="put", oi=200, vol=40)],
            [polygon_contract()],
        ]
        self.options.save_full_chain.side_effect = [11, None]  # stored, duplicate
        with patch("quantcore.services.options.datetime.date", _FrozenToday):
            payload, status = self.service.backfill_options_history(
                "INTC", days=6, skip_existing=False
            )
        self.assertEqual(status, 200)
        statuses = [r["status"] for r in payload["results"]]
        self.assertEqual(statuses.count("error"), 1)
        self.assertGreaterEqual(statuses.count("no_data"), 1)
        self.assertEqual(payload["stored"], 1)
        self.assertEqual(payload["skipped"], 1)          # the duplicate
        # The stored snapshot aggregated both sides at the 16:00 ET close.
        _, kwargs = self.options.save_full_chain.call_args_list[0]
        self.assertTrue(kwargs["captured_at"].endswith("T21:00:00Z"))
        exp_data = kwargs["expirations_data"][0]
        self.assertEqual(exp_data["put_call_ratio"], 2.0)   # 200 put / 100 call OI
        self.assertEqual(exp_data["calls"]["avg_iv_pct"], 30.0)


class TestRefreshSnapshots(OrchestratorTestBase):
    PORTFOLIO = [{"symbol": "AAA"}, {"symbol": "BBB"}]
    WATCHLIST = [{"symbol": "BBB"}, {"symbol": "CCC"}]

    def run_refresh(self, **kw):
        with patch("quantcore.services.options._time.sleep"):
            return self.service.refresh_options_snapshots(
                self.PORTFOLIO, self.WATCHLIST, **kw
            )

    def test_all_source_dedupes_and_reports(self):
        self.prices.get_stock_price.return_value = {"ok": True}
        out = self.run_refresh(source="all", chain_type="atm")
        symbols = [r["symbol"] for r in out["results"]]
        self.assertEqual(symbols, ["AAA", "BBB", "CCC"])   # deduped + sorted
        self.assertEqual(out["succeeded"], 3)
        self.assertEqual(out["failed"], 0)
        self.yf.close_thread_caches.assert_called_once()   # issue-#75 cleanup

    def test_full_chain_type_uses_the_chain_fetcher(self):
        with patch.object(self.service, "get_full_options_chain",
                          return_value={"ok": True}) as full:
            out = self.run_refresh(source="portfolio", chain_type="full")
        self.assertEqual(full.call_count, 2)
        self.assertEqual(out["succeeded"], 2)

    def test_failures_are_retried_then_reported(self):
        # AAA fails twice (retry exhausted); BBB/CCC succeed.
        def flaky(sym):
            if sym == "AAA":
                raise RuntimeError("yahoo down")
            return {"ok": True}

        self.prices.get_stock_price.side_effect = flaky
        out = self.run_refresh(source="all")
        self.assertEqual(out["failed"], 1)
        failed = next(r for r in out["results"] if r["status"] == "error")
        self.assertEqual(failed["symbol"], "AAA")
        self.assertIn("yahoo down", failed["error"])


if __name__ == "__main__":
    unittest.main()
