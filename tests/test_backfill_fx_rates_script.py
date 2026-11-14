"""scripts/backfill_fx_rates.py: a thin adapter over
PortfolioService.backfill_fx_rates (#235 step 2.3). The service is faked; its
own behaviour is covered in tests/test_portfolio_service.py."""
import contextlib
import importlib.util
import io
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quantcore.db_safety import assert_not_production

assert_not_production()

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backfill_fx_rates.py"
_spec = importlib.util.spec_from_file_location("backfill_fx_rates", _SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)


class FakePortfolio:
    def __init__(self, by_owner):
        self.by_owner = by_owner
        self.calls = []

    def list_owners(self):
        return list(self.by_owner)

    def backfill_fx_rates(self, owner, *, apply=False):
        self.calls.append((owner, apply))
        rows = self.by_owner.get(owner, {})
        return {
            "owner": owner, "applied": apply,
            "lots": [dict(r, filled=apply and r["rate"] is not None) for r in rows.get("lots", [])],
            "sales": [dict(r, filled=apply and r["rate"] is not None) for r in rows.get("sales", [])],
        }


LOT = {"lot_id": 7, "symbol": "SAP.DE", "currency": "EUR", "trade_date": "2026-01-05",
       "rate": Decimal("1.10000000")}
SALE = {"sale_id": 9, "lot_id": 7, "symbol": "SAP.DE", "currency": "EUR",
        "sale_trade_date": "2026-03-02", "rate": Decimal("1.05000000")}
UNRATED = {"lot_id": 8, "symbol": "ZZZ.L", "currency": "GBP", "trade_date": "2026-01-05",
           "rate": None}


class BackfillScriptTest(unittest.TestCase):
    def _run(self, portfolio, *argv):
        out, err = io.StringIO(), io.StringIO()
        services = SimpleNamespace(portfolio=portfolio)
        with patch("sys.argv", ["backfill_fx_rates.py", *argv]), \
                patch("quantcore.services.registry.get_services", return_value=services), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = backfill.main()
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_is_the_default_and_says_what_it_would_write(self):
        portfolio = FakePortfolio({"john": {"lots": [LOT], "sales": [SALE]}, "sager": {}})
        code, out, _ = self._run(portfolio)

        self.assertEqual(code, 0)
        self.assertEqual(portfolio.calls, [("john", False), ("sager", False)])
        self.assertIn("would fill lot 7 SAP.DE EUR 2026-01-05: 1.10000000", out)
        self.assertIn("would fill sale 9 SAP.DE EUR 2026-03-02: 1.05000000", out)
        self.assertNotIn("sager:", out)
        self.assertIn("2 owner(s) checked, 2 rate(s) to fill, 0 unresolved.", out)
        self.assertIn("re-run with --apply", out)

    def test_apply_writes_for_one_named_owner(self):
        portfolio = FakePortfolio({"john": {"lots": [LOT]}, "sager": {"lots": [LOT]}})
        code, out, _ = self._run(portfolio, "--apply", "--owner", "sager")

        self.assertEqual(code, 0)
        self.assertEqual(portfolio.calls, [("sager", True)])
        self.assertIn("filled lot 7", out)
        self.assertIn("1 owner(s) checked, 1 rate(s) filled, 0 unresolved.", out)
        self.assertNotIn("re-run with --apply", out)

    def test_an_unresolved_row_is_reported_and_fails_the_run(self):
        portfolio = FakePortfolio({"john": {"lots": [UNRATED, LOT]}})
        code, out, err = self._run(portfolio, "--apply")

        self.assertEqual(code, 1)
        self.assertIn("no rate for lot 8 ZZZ.L GBP 2026-01-05 (left empty)", err)
        self.assertIn("1 rate(s) filled, 1 unresolved.", out)

    def test_a_row_another_run_filled_first_is_reported_as_left(self):
        rows = [dict(LOT, filled=False)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            counts = backfill._report("lot", "lot_id", "trade_date", rows, apply=True)
        self.assertEqual(counts, (1, 0))
        self.assertIn("already had a rate, left lot 7", out.getvalue())

    def test_backfill_totals_every_owner_and_prints_only_those_with_rows(self):
        portfolio = FakePortfolio({
            "john": {"lots": [LOT, UNRATED], "sales": [SALE]}, "sager": {}, "thomas": {"lots": [LOT]},
        })
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            counts = backfill._backfill(portfolio, ["john", "sager", "thomas"], False)

        self.assertEqual(counts, (3, 1))
        self.assertEqual(portfolio.calls, [("john", False), ("sager", False), ("thomas", False)])
        headers = [line for line in out.getvalue().splitlines() if line.endswith(":")]
        self.assertEqual(headers, ["john:", "thomas:"])

    def test_parse_args_defaults_to_a_dry_run_of_every_owner(self):
        with patch("sys.argv", ["backfill_fx_rates.py"]):
            args = backfill._parse_args()
        self.assertEqual((args.apply, args.owner, args.allow_prod), (False, "", False))

    def test_refuses_production_unless_allowed(self):
        portfolio = FakePortfolio({})
        with patch("quantcore.db_safety.assert_not_production", side_effect=SystemExit(2)):
            with self.assertRaises(SystemExit):
                self._run(portfolio)
            self.assertEqual(portfolio.calls, [])
            self._run(portfolio, "--allow-prod")


if __name__ == "__main__":
    unittest.main()
