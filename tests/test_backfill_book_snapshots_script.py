"""scripts/backfill_book_snapshots.py: a thin adapter over
PerformanceService.backfill_book (#431 step 5.2). The service is faked; its
own behaviour is covered in tests/test_book_restatement.py."""
import contextlib
import datetime
import importlib.util
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quantcore.db_safety import assert_not_production

assert_not_production()

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backfill_book_snapshots.py"
_spec = importlib.util.spec_from_file_location("backfill_book_snapshots", _SCRIPT)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

FRI = datetime.date(2026, 11, 6)
MON = datetime.date(2026, 11, 9)
TUE = datetime.date(2026, 11, 10)


def _result(**extra):
    result = {"start": FRI, "sessions": 3, "inserted": 2, "restated": 1, "unchanged": 0,
              "deleted": 0, "unvalued": [MON], "stale": [TUE], "undated_lots": 0,
              "cleared": False}
    result.update(extra)
    return result


class FakePerformance:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def backfill_book(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.get(kwargs["owner"], _result())


class BackfillBookSnapshotsScriptTest(unittest.TestCase):
    def _run(self, performance, *argv, owners=("john", "sager")):
        out = io.StringIO()
        services = SimpleNamespace(
            performance=performance,
            cash=SimpleNamespace(list_owners=lambda: list(owners)))
        with patch("quantcore.services.registry.get_services", return_value=services), \
                contextlib.redirect_stdout(out):
            code = backfill.main(list(argv))
        return code, out.getvalue()

    def test_a_dry_run_walks_every_owners_real_book_and_names_the_holes(self):
        perf = FakePerformance({"sager": {"start": None, "sessions": 0}})
        code, out = self._run(perf, "--through", "2026-11-10")
        self.assertEqual(code, 0)
        self.assertEqual(
            [(c["owner"], c["book_id"], c["apply"], c["through"], c["start"]) for c in perf.calls],
            [("john", None, False, TUE, None), ("sager", None, False, TUE, None)])
        self.assertIn("john / real book: 3 session(s) from 2026-11-06: 2 would be added", out)
        self.assertIn("no value: 2026-11-09", out)
        self.assertIn("priced from an earlier close: 2026-11-10", out)
        self.assertIn("sager / real book: no opening balance, skipped", out)
        self.assertIn("3 row(s) to write", out)
        self.assertIn("Dry run", out)

    def test_one_book_from_a_date_applied(self):
        perf = FakePerformance({"john": _result(cleared=True, undated_lots=2, unvalued=[])})
        code, out = self._run(perf, "--owner", "john", "--book-id", "7", "--from",
                              "2026-11-06", "--through", "2026-11-10", "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(perf.calls, [{"owner": "john", "book_id": 7, "start": FRI,
                                       "through": TUE, "apply": True}])
        self.assertIn("john / book 7", out)
        self.assertIn("no value: none", out)
        self.assertIn("2 lot(s) with no trade date", out)
        self.assertIn("restatement mark is cleared", out)
        self.assertNotIn("Dry run", out)

    def test_a_book_id_needs_an_owner_and_a_date_must_be_a_date(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                backfill._parse_args(["--book-id", "7"])
            with self.assertRaises(SystemExit):
                backfill._parse_args(["--from", "last week"])

    def test_it_refuses_production_without_the_flag(self):
        with patch("quantcore.db_safety.assert_not_production",
                   side_effect=RuntimeError("prod")) as guard:
            with self.assertRaises(RuntimeError):
                backfill.main([])
        guard.assert_called_once()


if __name__ == "__main__":
    unittest.main()
