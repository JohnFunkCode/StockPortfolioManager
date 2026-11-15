"""scripts/backfill_cash_entries.py: a thin adapter over CashService.backfill
(#431 step 3.1). The service is faked; its own behaviour is covered in
tests/test_cash_api.py."""
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quantcore.db_safety import assert_not_production

assert_not_production()

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backfill_cash_entries.py"
_spec = importlib.util.spec_from_file_location("backfill_cash_entries", _SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)


def _book(book_id, name, kind, buys=0, sells=0, openings=0):
    return {"book_id": book_id, "name": name, "kind": kind,
            "buys": buys, "sells": sells, "openings": openings}


class FakeCash:
    def __init__(self, by_owner):
        self.by_owner = by_owner
        self.calls = []

    def list_owners(self):
        return list(self.by_owner)

    def backfill(self, *, owner, apply=False):
        self.calls.append((owner, apply))
        return self.by_owner.get(owner, [])


BOOKS = {
    "john": [_book(1, "Real", "real", buys=12, sells=3), _book(4, "Momentum", "paper", openings=1)],
    "sager": [_book(2, "Real", "real")],
}


class BackfillScriptTest(unittest.TestCase):
    def _run(self, cash, *argv):
        out, err = io.StringIO(), io.StringIO()
        services = SimpleNamespace(cash=cash)
        with patch("sys.argv", ["backfill_cash_entries.py", *argv]), \
                patch("quantcore.services.registry.get_services", return_value=services), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = backfill.main()
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_is_the_default_and_says_what_it_would_write(self):
        cash = FakeCash(BOOKS)
        code, out, _ = self._run(cash)

        self.assertEqual(code, 0)
        self.assertEqual(cash.calls, [("john", False), ("sager", False)])
        self.assertIn(
            "john / Real (real book 1): would write 12 buy, 3 sell, 0 opening balance", out
        )
        self.assertIn(
            "john / Momentum (paper book 4): would write 0 buy, 0 sell, 1 opening balance", out
        )
        self.assertIn("2 owner(s) checked, 16 cash row(s) to write.", out)
        self.assertIn("re-run with --apply", out)
        # A book with nothing missing prints nothing.
        self.assertNotIn("sager", out)

    def test_apply_writes_and_says_so(self):
        cash = FakeCash(BOOKS)
        code, out, _ = self._run(cash, "--apply")

        self.assertEqual(code, 0)
        self.assertEqual(cash.calls, [("john", True), ("sager", True)])
        self.assertIn("john / Real (real book 1): wrote 12 buy, 3 sell, 0 opening balance", out)
        self.assertIn("2 owner(s) checked, 16 cash row(s) written.", out)
        self.assertNotIn("re-run", out)

    def test_one_owner_can_be_named(self):
        cash = FakeCash(BOOKS)
        _, out, _ = self._run(cash, "--owner", "sager", "--apply")
        self.assertEqual(cash.calls, [("sager", True)])
        self.assertIn("1 owner(s) checked, 0 cash row(s) written.", out)

    def test_nothing_missing_is_not_an_error_and_asks_for_no_rerun(self):
        code, out, _ = self._run(FakeCash({"sager": BOOKS["sager"]}))
        self.assertEqual(code, 0)
        self.assertIn("0 cash row(s) to write.", out)
        self.assertNotIn("re-run", out)

    def test_it_refuses_production_unless_told(self):
        cash = FakeCash(BOOKS)
        with patch("quantcore.db_safety.assert_not_production",
                   side_effect=SystemExit("refusing")) as guard:
            with self.assertRaises(SystemExit):
                self._run(cash)
            self.assertEqual(cash.calls, [])
            self._run(cash, "--allow-prod")
        self.assertEqual(guard.call_count, 1)

    def test_backfill_helper_returns_the_row_count(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(backfill._backfill(FakeCash(BOOKS), ["john", "sager"], False), 16)
            self.assertEqual(backfill._backfill(FakeCash(BOOKS), [], True), 0)


if __name__ == "__main__":
    unittest.main()
