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


class CaptureRateLimitTest(unittest.TestCase):
    """#373: the gateway's 429 counter marks which failures were rate limits."""

    def test_failures_that_moved_the_429_counter_are_rate_limited(self):
        counter = {"n": 0}

        def get_chain(sym, max_expirations=6):
            if sym == "LIMITED":
                counter["n"] += 3            # the gateway retried twice, then gave up
                raise RuntimeError("all 6 option-chain fetches failed for LIMITED")
            if sym == "BROKEN":
                raise ValueError("Could not retrieve price for symbol: BROKEN")
            return {"persisted": True, "expiration_count": 6}

        o = MagicMock()
        o.get_full_options_chain.side_effect = get_chain
        s = main.capture_options_chains(
            ["A", "LIMITED", "BROKEN"], o, budget_seconds=1000,
            rate_limit_count=lambda: counter["n"])
        self.assertEqual(s["failures"], ["LIMITED", "BROKEN"])
        self.assertEqual(s["rate_limited_failures"], ["LIMITED"])
        self.assertEqual(s["rate_limited_calls"], 3)

    def test_no_counter_means_no_rate_limit_attribution(self):
        s = main.capture_options_chains(["A"], _options(fail={"A"}), budget_seconds=1000)
        self.assertEqual((s["rate_limited_failures"], s["rate_limited_calls"]), ([], 0))


class RecordGammaTest(unittest.TestCase):
    """#373: gamma/GEX come from the snapshot the capture just persisted."""

    def test_uses_the_snapshot_and_never_the_live_fetchers(self):
        o = MagicMock()
        o.record_gex_from_snapshot.side_effect = [
            {"gamma_wall_saved": True, "gex_saved": True}, None, RuntimeError("x"),
        ]
        s = main.record_gamma_and_gex(["A", "B", "C"], o, budget_seconds=1000,
                                      today=datetime.date.fromordinal(3 * 1000))
        self.assertEqual((s["recorded"], s["no_snapshot"], s["failed"]), (1, 1, 1))
        self.assertEqual(o.record_gex_from_snapshot.call_count, 3)
        o.get_delta_adjusted_oi.assert_not_called()
        o.get_gex_profile.assert_not_called()

    def test_skips_symbols_without_listed_options(self):
        o = MagicMock()
        s = main.record_gamma_and_gex(["A", "NOPT", "C"], o, budget_seconds=1000,
                                      today=datetime.date(2026, 1, 1), skip=["NOPT"])
        seen = [c.args[0] for c in o.record_gex_from_snapshot.call_args_list]
        self.assertEqual(sorted(seen), ["A", "C"])
        self.assertEqual(s["requested"], 2)

    def test_start_rotates_with_the_date(self):
        o = MagicMock()
        main.record_gamma_and_gex(["A", "B", "C"], o, budget_seconds=1000,
                                  today=datetime.date.fromordinal(3 * 1000 + 1))
        seen = [c.args[0] for c in o.record_gex_from_snapshot.call_args_list]
        self.assertEqual(seen, ["B", "C", "A"])

    def test_budget_stops_the_walk(self):
        s = main.record_gamma_and_gex(list("ABCDEF"), MagicMock(), budget_seconds=3,
                                      clock=_Clock(1.0), today=datetime.date(2026, 1, 1))
        self.assertTrue(s["budget_exhausted"])
        self.assertEqual(s["attempted"] + s["skipped"], 6)


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

    def test_rate_limited_failures_are_named_as_such(self):
        n = MagicMock()
        problems = main.check_capture_health(
            10, self._capture(failed=6, failures=list("ABCDEF"),
                              rate_limited_failures=list("ABCDE"), rate_limited_calls=15),
            None, {"chains": 10, "gamma_wall": 0, "gex": 0}, n)
        self.assertEqual(len(problems), 1)
        self.assertIn("Yahoo rate-limited 5 symbols", problems[0])
        self.assertIn("15 429 responses", problems[0])

    def test_mostly_other_failures_keep_the_generic_wording(self):
        n = MagicMock()
        problems = main.check_capture_health(
            10, self._capture(failed=6, failures=list("ABCDEF"),
                              rate_limited_failures=["A"], rate_limited_calls=3),
            None, {"chains": 10, "gamma_wall": 0, "gex": 0}, n)
        self.assertEqual(len(problems), 1)
        self.assertNotIn("rate-limited", problems[0])

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


def _tail_services():
    services = MagicMock()
    services.yfinance_gateway.rate_limited = 0
    return services


class RunCaptureTailTest(unittest.TestCase):
    def test_never_raises(self):
        services = _tail_services()
        services.options.get_full_options_chain.side_effect = RuntimeError("boom")
        services.options.capture_counts.side_effect = RuntimeError("db down")
        main.run_capture_tail(["A"], services, MagicMock(), job_started=0.0)

    def test_gex_step_skips_what_the_capture_found_without_options(self):
        services = _tail_services()
        services.options.get_full_options_chain.side_effect = (
            lambda sym, max_expirations=6: {"expiration_count": 0,
                                             "no_listed_options": True}
            if sym == "NOPT" else {"persisted": True, "expiration_count": 6})
        services.options.capture_counts.return_value = {
            "chains": 4, "gamma_wall": 4, "gex": 4}
        notifier = MagicMock()
        main.run_capture_tail(["A", "B", "C", "D", "NOPT"], services, notifier,
                              job_started=0.0, clock=lambda: 0.0)
        called = [c.args[0] for c in services.options.record_gex_from_snapshot.call_args_list]
        self.assertEqual(sorted(called), ["A", "B", "C", "D"])
        notifier.send_capture_gap_alert.assert_not_called()

    def test_steps_run_in_order_and_gex_reads_the_snapshot(self):
        # #373 kept the order on purpose: capture, then gamma/GEX from what the
        # capture persisted, then the database read the health check alarms on.
        services = _tail_services()
        calls = []
        services.options.get_full_options_chain.side_effect = (
            lambda sym, max_expirations=6: calls.append(("capture", sym))
            or {"persisted": True, "expiration_count": 6})
        services.options.record_gex_from_snapshot.side_effect = (
            lambda sym: calls.append(("gex", sym)) or {"gex_saved": True})
        services.options.capture_counts.side_effect = (
            lambda day: calls.append(("counts", None))
            or {"chains": 2, "gamma_wall": 2, "gex": 2})
        main.run_capture_tail(["A", "B"], services, MagicMock(),
                              job_started=0.0, clock=lambda: 0.0)
        steps = [step for step, _ in calls]
        self.assertEqual(steps[:2], ["capture", "capture"])
        self.assertEqual(sorted(steps[2:4]), ["gex", "gex"])
        self.assertEqual(steps[4:], ["counts"])
        services.options.get_delta_adjusted_oi.assert_not_called()
        services.options.get_gex_profile.assert_not_called()

    def test_tail_reports_rate_limited_capture_failures(self):
        services = _tail_services()

        def get_chain(sym, max_expirations=6):
            services.yfinance_gateway.rate_limited += 3
            raise RuntimeError(f"all 6 option-chain fetches failed for {sym}")

        services.options.get_full_options_chain.side_effect = get_chain
        services.options.capture_counts.return_value = {
            "chains": 0, "gamma_wall": 0, "gex": 0}
        notifier = MagicMock()
        main.run_capture_tail(["A", "B"], services, notifier,
                              job_started=0.0, clock=lambda: 0.0)
        problems = notifier.send_capture_gap_alert.call_args.args[0]
        self.assertTrue(any("Yahoo rate-limited 2 symbols" in p for p in problems))

    def test_remaining_budget_is_clamped_to_the_deadline(self):
        got = main._remaining_budget(900, job_started=0.0, clock=lambda: 1500.0)
        self.assertEqual(got, 240.0)   # 1800 - 60 margin - 1500 elapsed
        self.assertEqual(main._remaining_budget(900, 0.0, clock=lambda: 5000.0), 1.0)


if __name__ == "__main__":
    unittest.main()
