"""The daily job's budgeted capture tail and its health check (issue #68).

The capture steps swallow their own failures so notifications are never held
hostage; these tests pin the other half -- that a degraded night is *visible*
(a summary, then an alarm) and that no step can raise out of the tail.
"""
import datetime
import unittest
from unittest.mock import MagicMock

import main


class _Clock:
    """Advances by ``step`` on every read, so a budget is spent deterministically."""

    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def _options(chain=None, fail=()):
    o = MagicMock()

    def get_chain(sym, max_expirations=6):
        if sym in fail:
            raise RuntimeError("yahoo down")
        return chain or {"persisted": True, "expiration_count": 1, "total_contracts": 2}

    o.get_full_options_chain.side_effect = get_chain
    return o


class CaptureOptionsChainsTest(unittest.TestCase):
    def test_counts_captured_duplicate_and_failed(self):
        o = _options(fail={"BAD"})
        o.get_full_options_chain.side_effect = [
            {"persisted": True}, {"persisted": False}, RuntimeError("x"),
        ]
        s = main.capture_options_chains(["A", "B", "C"], o, budget_seconds=1000)
        self.assertEqual((s["captured"], s["duplicate"], s["failed"]), (1, 1, 1))
        self.assertEqual(s["failures"], ["C"])
        self.assertFalse(s["budget_exhausted"])

    def test_budget_stops_the_walk(self):
        s = main.capture_options_chains(
            list("ABCDEF"), _options(), budget_seconds=3, clock=_Clock(1.0))
        self.assertTrue(s["budget_exhausted"])
        self.assertGreater(s["skipped"], 0)
        self.assertEqual(s["attempted"] + s["skipped"], 6)


class RecordGammaTest(unittest.TestCase):
    def test_calls_both_writers_per_symbol_and_survives_failures(self):
        o = MagicMock()
        o.get_gex_profile.side_effect = [None, RuntimeError("x")]
        s = main.record_gamma_and_gex(["A", "B"], o, budget_seconds=1000,
                                      today=datetime.date(2026, 1, 1))
        self.assertEqual((s["recorded"], s["failed"]), (1, 1))
        self.assertEqual(o.get_delta_adjusted_oi.call_count, 2)

    def test_start_rotates_with_the_date(self):
        seen = []
        o = MagicMock()
        o.get_delta_adjusted_oi.side_effect = lambda sym: seen.append(sym)
        main.record_gamma_and_gex(["A", "B", "C"], o, budget_seconds=1000,
                                  today=datetime.date.fromordinal(3 * 1000 + 1))
        self.assertEqual(seen, ["B", "C", "A"])


class CheckCaptureHealthTest(unittest.TestCase):
    def _capture(self, **kw):
        base = {"attempted": 10, "failed": 0, "failures": [], "skipped": 0,
                "budget_exhausted": False, "budget_seconds": 600.0}
        return {**base, **kw}

    def test_healthy_run_sends_nothing(self):
        n = MagicMock()
        problems = main.check_capture_health(
            10, self._capture(), {"attempted": 10, "failed": 0},
            {"chains": 10, "gamma_wall": 10, "gex": 10}, n)
        self.assertEqual(problems, [])
        n.send_capture_gap_alert.assert_not_called()

    def test_low_coverage_alarms_even_when_loop_reported_success(self):
        n = MagicMock()
        problems = main.check_capture_health(
            10, self._capture(), None, {"chains": 3, "gamma_wall": 0, "gex": 0}, n)
        self.assertEqual(len(problems), 1)
        n.send_capture_gap_alert.assert_called_once_with(problems)

    def test_failure_rate_and_exhausted_budget_alarm(self):
        n = MagicMock()
        problems = main.check_capture_health(
            10, self._capture(failed=8, failures=["A"], skipped=2, budget_exhausted=True),
            {"attempted": 4, "failed": 3}, {"chains": 10, "gamma_wall": 1, "gex": 1}, n)
        self.assertEqual(len(problems), 3)

    def test_dead_webhook_does_not_raise(self):
        n = MagicMock()
        n.send_capture_gap_alert.side_effect = RuntimeError("webhook")
        main.check_capture_health(
            10, self._capture(), None, {"chains": 0, "gamma_wall": 0, "gex": 0}, n)


class RunCaptureTailTest(unittest.TestCase):
    def test_never_raises(self):
        services = MagicMock()
        services.options.get_full_options_chain.side_effect = RuntimeError("boom")
        services.options.capture_counts.side_effect = RuntimeError("db down")
        main.run_capture_tail(["A"], services, MagicMock(), job_started=0.0)

    def test_remaining_budget_is_clamped_to_the_deadline(self):
        got = main._remaining_budget(900, job_started=0.0, clock=lambda: 1500.0)
        self.assertEqual(got, 240.0)   # 1800 - 60 margin - 1500 elapsed
        self.assertEqual(main._remaining_budget(900, 0.0, clock=lambda: 5000.0), 1.0)


if __name__ == "__main__":
    unittest.main()
