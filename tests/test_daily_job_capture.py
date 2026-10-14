"""The daily job's budgeted capture tail and its health check (issue #68).

The capture steps swallow their own failures so notifications are never held
hostage; these tests pin the other half -- that a degraded night is *visible*
(a summary, then an alarm) and that no step can raise out of the tail.
"""
import datetime
import os
import unittest
from unittest.mock import MagicMock, patch

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

    def test_symbol_without_listed_options_is_not_a_duplicate(self):
        # #374: get_full_options_chain marks a symbol with no listed options
        # ``no_listed_options`` and does not persist anything.
        o = MagicMock()
        o.get_full_options_chain.side_effect = [
            {"persisted": True, "expiration_count": 6},
            {"expiration_count": 0, "total_contracts": 0,
             "no_listed_options": True},
            {"persisted": False, "expiration_count": 6},
        ]
        s = main.capture_options_chains(["A", "NOPT", "C"], o, budget_seconds=1000)
        self.assertEqual((s["captured"], s["no_options"], s["duplicate"]), (1, 1, 1))
        self.assertEqual(s["no_options_symbols"], ["NOPT"])

    def test_zero_expirations_without_the_flag_is_not_no_options(self):
        # #374 review: an empty result is only excluded from coverage when the
        # service says the symbol has no listed options; anything else must
        # not shrink the denominator.
        o = MagicMock()
        o.get_full_options_chain.side_effect = [
            {"persisted": False, "expiration_count": 0},
        ]
        s = main.capture_options_chains(["A"], o, budget_seconds=1000)
        self.assertEqual((s["no_options"], s["duplicate"]), (0, 1))

    def test_partial_expiration_failures_are_counted(self):
        o = MagicMock()
        o.get_full_options_chain.side_effect = [
            {"persisted": True, "expiration_count": 4, "failed_expirations": 2},
            {"persisted": True, "expiration_count": 6, "failed_expirations": 0},
        ]
        s = main.capture_options_chains(["A", "B"], o, budget_seconds=1000)
        self.assertEqual(s["captured"], 2)
        self.assertEqual(s["partial_expirations_failed"], 2)
        self.assertEqual(s["partial_symbols"], ["A"])

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

    def test_skips_symbols_without_listed_options(self):
        seen = []
        o = MagicMock()
        o.get_delta_adjusted_oi.side_effect = lambda sym: seen.append(sym)
        s = main.record_gamma_and_gex(["A", "NOPT", "C"], o, budget_seconds=1000,
                                      today=datetime.date(2026, 1, 1), skip=["NOPT"])
        self.assertNotIn("NOPT", seen)
        self.assertEqual(sorted(seen), ["A", "C"])
        self.assertEqual(s["requested"], 2)
        o.get_gex_profile.assert_called()
        self.assertNotIn("NOPT", [c.args[0] for c in o.get_gex_profile.call_args_list])

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

    def test_no_options_symbols_leave_the_coverage_denominator(self):
        # The #374 night: 120 symbols, 29 with no listed options, every
        # optionable chain landed. 91/120 is 76% (alarm); 91/91 is 100%.
        n = MagicMock()
        problems = main.check_capture_health(
            120, self._capture(attempted=120, no_options=29,
                               no_options_symbols=[f"N{i}" for i in range(29)]),
            None, {"chains": 91, "gamma_wall": 0, "gex": 0}, n)
        self.assertEqual(problems, [])
        n.send_capture_gap_alert.assert_not_called()

    def test_coverage_still_alarms_among_optionable_symbols(self):
        n = MagicMock()
        problems = main.check_capture_health(
            120, self._capture(attempted=120, no_options=20,
                               no_options_symbols=["X"] * 20),
            None, {"chains": 50, "gamma_wall": 0, "gex": 0}, n)
        self.assertEqual(len(problems), 1)
        self.assertIn("50 of 100 optionable", problems[0])

    def test_too_many_no_options_symbols_alarm(self):
        n = MagicMock()
        problems = main.check_capture_health(
            10, self._capture(no_options=3, no_options_symbols=["P", "Q", "R"]),
            None, {"chains": 7, "gamma_wall": 0, "gex": 0}, n)
        self.assertEqual(len(problems), 1)
        self.assertIn("3 of 10 symbols returned no options chain", problems[0])
        self.assertIn("P, Q, R", problems[0])

    def test_invalid_no_options_ceiling_falls_back_to_default(self):
        capture = self._capture(no_options=2, no_options_symbols=["P", "Q"])
        counts = {"chains": 8, "gamma_wall": 0, "gex": 0}
        with patch.dict(os.environ, {main.CAPTURE_NO_OPTIONS_CEILING_ENV: "lots"}):
            # 20% is under the 25% default; a typo must not arm (or disarm) it.
            self.assertEqual(
                main.check_capture_health(10, capture, None, counts, MagicMock()), [])
            capture = self._capture(no_options=3, no_options_symbols=["P", "Q", "R"])
            counts = {"chains": 7, "gamma_wall": 0, "gex": 0}
            self.assertEqual(
                len(main.check_capture_health(10, capture, None, counts, MagicMock())), 1)

    def test_universe_with_no_options_anywhere_does_not_divide_by_zero(self):
        n = MagicMock()
        problems = main.check_capture_health(
            4, self._capture(attempted=4, no_options=4, no_options_symbols=list("ABCD")),
            None, {"chains": 0, "gamma_wall": 0, "gex": 0}, n)
        self.assertEqual(len(problems), 1)
        self.assertIn("returned no options chain", problems[0])
        n.send_capture_gap_alert.assert_called_once_with(problems)

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

    def test_gex_step_skips_what_the_capture_found_without_options(self):
        services = MagicMock()
        services.options.get_full_options_chain.side_effect = (
            lambda sym, max_expirations=6: {"expiration_count": 0,
                                             "no_listed_options": True}
            if sym == "NOPT" else {"persisted": True, "expiration_count": 6})
        services.options.capture_counts.return_value = {
            "chains": 4, "gamma_wall": 4, "gex": 4}
        notifier = MagicMock()
        main.run_capture_tail(["A", "B", "C", "D", "NOPT"], services, notifier,
                              job_started=0.0, clock=lambda: 0.0)
        called = [c.args[0] for c in services.options.get_delta_adjusted_oi.call_args_list]
        self.assertEqual(sorted(called), ["A", "B", "C", "D"])
        notifier.send_capture_gap_alert.assert_not_called()

    def test_remaining_budget_is_clamped_to_the_deadline(self):
        got = main._remaining_budget(900, job_started=0.0, clock=lambda: 1500.0)
        self.assertEqual(got, 240.0)   # 1800 - 60 margin - 1500 elapsed
        self.assertEqual(main._remaining_budget(900, 0.0, clock=lambda: 5000.0), 1.0)


if __name__ == "__main__":
    unittest.main()
