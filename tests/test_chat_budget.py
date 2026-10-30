"""The daily Sidekick token budget (G2, #461) without a database.

quantcore/services/chat_budget.py's helpers and TurnBudget against an
in-memory stand-in for ChatUsageRepository, then ChatService's loop with a
budget: reserve before every model call, settle after, release only a call
the keyproxy refused, and per-mode behaviour (D4 shadow, D8 fail closed).
The SQL itself is tests/test_chat_usage_repository.py.
"""

import datetime
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quantcore.gateways.anthropic_gateway import AnthropicChatClient
from quantcore.gateways.keyproxy_gateway import KeyProxyChatClient, KeyProxyError
from quantcore.services import chat_budget
from quantcore.services.chat import ChatService, Done, ErrorEvent, TextDelta, TurnContext
from quantcore.services.chat_budget import (
    MAX_OUTPUT_TOKENS,
    BudgetExhausted,
    BudgetUnavailable,
    SidekickBudget,
    call_usage,
    estimate_tokens,
)

DAY = datetime.date(2026, 10, 30)
OWNER = "alice@example.com"


class FakeUsageRepo:
    """ChatUsageRepository's contract, in memory: [turns, input, output, reserved]."""

    def __init__(self):
        self.rows = {}
        self.fail = set()  # method names that raise
        self.calls = []

    def _row(self, owner, day):
        return self.rows.setdefault((owner, day), [0, 0, 0, 0])

    def reserve(self, *, owner, usage_day, tokens, budget, new_turn, enforce):
        self.calls.append(("reserve", tokens))
        if "reserve" in self.fail:
            raise RuntimeError("postgresql://user:secret@db/x is down")
        row = self.rows.get((owner, usage_day), [0, 0, 0, 0])
        within = row[1] + row[2] + row[3] + tokens <= budget
        if not within and enforce:
            return False, None
        row = self._row(owner, usage_day)
        row[0] += 1 if new_turn else 0
        row[3] += tokens
        return within, row[1] + row[2] + row[3]

    def reconcile(self, *, owner, usage_day, reserved, input_tokens, output_tokens):
        self.calls.append(("reconcile", reserved, input_tokens, output_tokens))
        if "reconcile" in self.fail:
            raise RuntimeError("connection reset")
        row = self._row(owner, usage_day)
        row[3] = max(row[3] - reserved, 0)
        row[1] += input_tokens
        row[2] += output_tokens

    def used(self, *, owner, usage_day):
        row = self.rows.get((owner, usage_day), [0, 0, 0, 0])
        return row[1] + row[2] + row[3]


class ModeEnv(unittest.TestCase):
    """Isolates the two env vars and the report-once memory."""

    mode = None

    def setUp(self):
        saved = {k: os.environ.get(k) for k in (chat_budget.MODE_ENV, chat_budget.CEILING_ENV)}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            chat_budget.reset_reported()

        self.addCleanup(restore)
        os.environ.pop(chat_budget.CEILING_ENV, None)
        if self.mode is None:
            os.environ.pop(chat_budget.MODE_ENV, None)
        else:
            os.environ[chat_budget.MODE_ENV] = self.mode
        chat_budget.reset_reported()


class SettingsTests(ModeEnv):
    def test_defaults(self):
        self.assertEqual(chat_budget.budget_mode(), "shadow")
        self.assertEqual(chat_budget.ceiling(), chat_budget.DEFAULT_CEILING)

    def test_modes_are_read_at_call_time_and_case_folded(self):
        os.environ[chat_budget.MODE_ENV] = " Enforce "
        self.assertEqual(chat_budget.budget_mode(), "enforce")
        os.environ[chat_budget.MODE_ENV] = "off"
        self.assertEqual(chat_budget.budget_mode(), "off")

    def test_bad_mode_falls_back_to_shadow_and_reports_once(self):
        os.environ[chat_budget.MODE_ENV] = "enforced"
        with self.assertLogs("quantcore.services.chat_budget", "ERROR") as logs:
            self.assertEqual(chat_budget.budget_mode(), "shadow")
            self.assertEqual(chat_budget.budget_mode(), "shadow")
        self.assertEqual(
            logs.output,
            [
                "ERROR:quantcore.services.chat_budget:SIDEKICK_BUDGET_MODE is not "
                "one of off/shadow/enforce; using shadow"
            ],
        )

    def test_bad_ceiling_falls_back_and_names_only_the_variable(self):
        for raw in ("lots", "0", "-5"):
            chat_budget.reset_reported()
            os.environ[chat_budget.CEILING_ENV] = raw
            with self.assertLogs("quantcore.services.chat_budget", "ERROR") as logs:
                self.assertEqual(chat_budget.ceiling(), chat_budget.DEFAULT_CEILING)
            self.assertNotIn(raw, logs.output[0].split("using")[0].split("is not")[1])

    def test_good_ceiling(self):
        os.environ[chat_budget.CEILING_ENV] = "500000"
        self.assertEqual(chat_budget.ceiling(), 500_000)

    def test_today_is_the_eastern_day(self):
        # 03:30 UTC on the 31st is still the 30th in New York.
        fixed = datetime.datetime(2026, 10, 31, 3, 30, tzinfo=datetime.timezone.utc)
        with patch.object(chat_budget.datetime, "datetime", wraps=datetime.datetime) as dt:
            dt.now.side_effect = lambda tz: fixed.astimezone(tz)
            self.assertEqual(chat_budget.today(), DAY)


class HelperTests(unittest.TestCase):
    def test_estimate_rounds_up(self):
        self.assertEqual(estimate_tokens(0), 0)
        self.assertEqual(estimate_tokens(1), 1)
        self.assertEqual(estimate_tokens(4), 2)
        self.assertEqual(estimate_tokens(5), 3)

    def test_call_usage_reads_a_keyproxy_message(self):
        final = SimpleNamespace(raw={"usage": {"input_tokens": 12, "output_tokens": 3}})
        self.assertEqual(call_usage(final), (12, 3))

    def test_call_usage_reads_an_sdk_message(self):
        final = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=9))
        self.assertEqual(call_usage(final), (7, 9))

    def test_call_usage_is_none_when_missing_or_malformed(self):
        self.assertIsNone(call_usage(SimpleNamespace()))
        self.assertIsNone(call_usage(SimpleNamespace(raw={})))
        self.assertIsNone(call_usage(SimpleNamespace(raw={"usage": {"input_tokens": 1}})))
        self.assertIsNone(
            call_usage(SimpleNamespace(raw={"usage": {"input_tokens": True, "output_tokens": 1}}))
        )

    def test_both_clients_default_to_the_reserved_output_cap(self):
        # A call's output can never exceed what was reserved for it.
        import inspect

        for cls in (KeyProxyChatClient, AnthropicChatClient):
            default = inspect.signature(cls.__init__).parameters["max_tokens"].default
            self.assertEqual(default, MAX_OUTPUT_TOKENS, cls.__name__)

    def test_exhausted_message_names_the_contact_route(self):
        msg = str(BudgetExhausted(used=1_234_567, budget=2_000_000))
        self.assertIn("1,234,567 of 2,000,000", msg)
        self.assertIn("contact the development team", msg)


def _budget(repo, budget=100):
    return SidekickBudget(repo, budget_for=lambda owner: budget, day=lambda: DAY)


class EnforceTurnBudgetTests(ModeEnv):
    mode = "enforce"

    def test_reserve_then_settle_records_actual_usage(self):
        repo = FakeUsageRepo()
        turn = _budget(repo).start_turn(OWNER)
        r = turn.reserve(60)
        turn.settle(r, (20, 5))
        self.assertEqual(repo.rows[(OWNER, DAY)], [1, 20, 5, 0])

    def test_only_the_first_call_counts_a_turn(self):
        repo = FakeUsageRepo()
        turn = _budget(repo).start_turn(OWNER)
        turn.settle(turn.reserve(10), (1, 1))
        turn.settle(turn.reserve(10), (1, 1))
        self.assertEqual(repo.rows[(OWNER, DAY)][0], 1)

    def test_settle_without_usage_charges_the_reservation(self):
        repo = FakeUsageRepo()
        turn = _budget(repo).start_turn(OWNER)
        turn.settle(turn.reserve(60), None)
        self.assertEqual(repo.used(owner=OWNER, usage_day=DAY), 60)

    def test_release_hands_the_reservation_back(self):
        repo = FakeUsageRepo()
        turn = _budget(repo).start_turn(OWNER)
        turn.release(turn.reserve(60))
        self.assertEqual(repo.used(owner=OWNER, usage_day=DAY), 0)

    def test_exhausted_raises_and_logs_once_per_turn(self):
        repo = FakeUsageRepo()
        turn = _budget(repo).start_turn(OWNER)
        turn.settle(turn.reserve(90), (90, 0))
        with self.assertLogs("quantcore.security", "WARNING") as logs:
            with self.assertRaises(BudgetExhausted) as ctx:
                turn.reserve(20)
            with self.assertRaises(BudgetExhausted):
                turn.reserve(20)
        self.assertEqual(
            logs.output,
            [
                "WARNING:quantcore.security:SECURITY chat_budget_exhausted "
                f"mode=enforce identity={OWNER}"
            ],
        )
        self.assertIn("90 of 100", str(ctx.exception))

    def test_a_reserve_failure_fails_closed_and_logs_the_class_only(self):
        repo = FakeUsageRepo()
        repo.fail.add("reserve")
        turn = _budget(repo).start_turn(OWNER)
        with self.assertLogs("quantcore.security", "WARNING") as logs:
            with self.assertRaises(BudgetUnavailable):
                turn.reserve(10)
        self.assertEqual(
            logs.output,
            [
                "WARNING:quantcore.security:SECURITY chat_budget_unavailable "
                "mode=enforce stage=reserve error=RuntimeError"
            ],
        )
        self.assertNotIn("secret", "".join(logs.output))

    def test_a_reconcile_failure_fails_closed(self):
        repo = FakeUsageRepo()
        turn = _budget(repo).start_turn(OWNER)
        r = turn.reserve(10)
        repo.fail.add("reconcile")
        with self.assertLogs("quantcore.security", "WARNING"):
            with self.assertRaises(BudgetUnavailable):
                turn.settle(r, (1, 1))

    def test_a_budget_lookup_failure_fails_closed(self):
        def boom(owner):
            raise RuntimeError("settings down")

        turn = SidekickBudget(FakeUsageRepo(), budget_for=boom, day=lambda: DAY).start_turn(OWNER)
        with self.assertLogs("quantcore.security", "WARNING"):
            with self.assertRaises(BudgetUnavailable):
                turn.reserve(10)


class ShadowTurnBudgetTests(ModeEnv):
    mode = "shadow"

    def test_over_budget_is_logged_recorded_and_allowed(self):
        repo = FakeUsageRepo()
        turn = _budget(repo).start_turn(OWNER)
        turn.settle(turn.reserve(90), (90, 0))
        with self.assertLogs("quantcore.security", "WARNING") as logs:
            r = turn.reserve(20)
        self.assertTrue(r.tracked)
        self.assertEqual(repo.used(owner=OWNER, usage_day=DAY), 110)
        self.assertEqual(
            logs.output,
            [
                "WARNING:quantcore.security:SECURITY chat_budget_exhausted "
                f"mode=shadow identity={OWNER}"
            ],
        )

    def test_db_failures_are_logged_and_the_turn_goes_on(self):
        repo = FakeUsageRepo()
        repo.fail.update({"reserve", "reconcile"})
        turn = _budget(repo).start_turn(OWNER)
        with self.assertLogs("quantcore.security", "WARNING") as logs:
            r = turn.reserve(10)
            turn.settle(r, (1, 1))  # untracked: nothing to settle
        self.assertFalse(r.tracked)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("mode=shadow stage=reserve error=RuntimeError", logs.output[0])


class OffTurnBudgetTests(ModeEnv):
    mode = "off"

    def test_off_touches_nothing(self):
        repo = FakeUsageRepo()
        turn = _budget(repo, budget=1).start_turn(OWNER)
        turn.settle(turn.reserve(1000), (1000, 1000))
        self.assertEqual(repo.calls, [])


# -- ChatService with a budget -------------------------------------------


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def final(stop_reason, *blocks, usage=(10, 2)):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=list(blocks),
        raw={"usage": {"input_tokens": usage[0], "output_tokens": usage[1]}},
    )


class ScriptedClient:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    def stream_turn(self, *, system, tools, messages):
        self.calls += 1
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        yield ("delta", "x")
        yield ("final", turn)


TOOL_THEN_TEXT = [
    final("tool_use", tool_use("tu_1", "get_stock_price", {"symbol": "AAPL"}), usage=(100, 20)),
    final("end_turn", text_block("done"), usage=(150, 30)),
]


class ChatServiceBudgetBase(ModeEnv):
    def make(self, turns, repo=None, budget=10**9):
        self.repo = repo or FakeUsageRepo()
        self.client = ScriptedClient(turns)
        prices = Mock()
        prices.get_stock_price.return_value = {"price": 1.0}
        service = ChatService(
            prices=prices,
            fundamentals=Mock(),
            sentiment=Mock(),
            options=Mock(),
            client_factory=lambda context: self.client,
            budget=_budget(self.repo, budget),
        )
        return service

    def run_turn(self, service, context=None):
        return list(
            service.stream_chat(
                [{"role": "user", "content": "hi"}],
                context=context or TurnContext(subject=OWNER),
            )
        )


class EnforceChatServiceTests(ChatServiceBudgetBase):
    mode = "enforce"

    def test_every_call_is_reserved_then_settled_with_its_usage(self):
        service = self.make(list(TOOL_THEN_TEXT))
        events = self.run_turn(service)
        self.assertIsInstance(events[-1], Done)
        kinds = [c[0] for c in self.repo.calls]
        self.assertEqual(kinds, ["reserve", "reconcile", "reserve", "reconcile"])
        self.assertEqual(self.repo.calls[1][2:], (100, 20))
        self.assertEqual(self.repo.calls[3][2:], (150, 30))
        self.assertEqual(self.repo.rows[(OWNER, DAY)], [1, 250, 50, 0])
        # Each reservation covers at least the output cap.
        self.assertGreater(self.repo.calls[0][1], MAX_OUTPUT_TOKENS)
        # The second builds on the first call's reported usage.
        self.assertGreaterEqual(self.repo.calls[2][1], 120 + MAX_OUTPUT_TOKENS)

    def test_exhausted_before_the_first_call_never_reaches_the_model(self):
        repo = FakeUsageRepo()
        repo.rows[(OWNER, DAY)] = [3, 100, 0, 0]
        service = self.make(list(TOOL_THEN_TEXT), repo=repo, budget=100)
        with self.assertLogs("quantcore.security", "WARNING"):
            events = self.run_turn(service)
        self.assertEqual(self.client.calls, 0)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ErrorEvent)
        self.assertIn("contact the development team", events[0].message)

    def test_exhausted_mid_turn_keeps_the_partial_answer_then_errors(self):
        # The first call fits exactly and reports using all of it.
        first = ChatService._first_call_estimate([{"role": "user", "content": "hi"}])
        turns = [
            final("tool_use", tool_use("tu_1", "get_stock_price", {"symbol": "AAPL"}), usage=(first, 0)),
            final("end_turn", text_block("done")),
        ]
        service = self.make(turns, budget=first)
        with self.assertLogs("quantcore.security", "WARNING"):
            events = self.run_turn(service)
        self.assertEqual(self.client.calls, 1)
        self.assertIsInstance(events[0], TextDelta)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertIn("Sidekick budget", events[-1].message)

    def test_a_settle_failure_mid_turn_stops_before_the_next_call(self):
        service = self.make(list(TOOL_THEN_TEXT))
        self.repo.fail.add("reconcile")
        with self.assertLogs("quantcore.security", "WARNING"):
            events = self.run_turn(service)
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(events[-1].message, chat_budget.UNAVAILABLE_MESSAGE)

    def test_a_settle_failure_on_the_last_call_keeps_the_answer(self):
        service = self.make([final("end_turn", text_block("done"))])
        self.repo.fail.add("reconcile")
        with self.assertLogs("quantcore.security", "WARNING"):
            events = self.run_turn(service)
        self.assertIsInstance(events[-1], Done)

    def test_a_reserve_failure_stops_the_turn_with_the_unavailable_message(self):
        service = self.make(list(TOOL_THEN_TEXT))
        self.repo.fail.add("reserve")
        with self.assertLogs("quantcore.security", "WARNING"):
            events = self.run_turn(service)
        self.assertEqual(self.client.calls, 0)
        self.assertEqual([e.message for e in events], [chat_budget.UNAVAILABLE_MESSAGE])

    def test_a_call_the_keyproxy_refused_is_released(self):
        refused = KeyProxyError("keyproxy said no", reached_provider=False)
        service = self.make([refused])
        with self.assertLogs("quantcore.services.chat", "ERROR"):
            events = self.run_turn(service)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertEqual(self.repo.used(owner=OWNER, usage_day=DAY), 0)

    def test_a_call_that_may_have_reached_the_provider_stays_charged(self):
        service = self.make([KeyProxyError("stream broke")])
        with self.assertLogs("quantcore.services.chat", "ERROR"):
            self.run_turn(service)
        self.assertGreater(self.repo.used(owner=OWNER, usage_day=DAY), MAX_OUTPUT_TOKENS)

    def test_the_local_principal_is_never_budgeted(self):
        service = self.make(list(TOOL_THEN_TEXT), budget=1)
        events = self.run_turn(service, TurnContext(subject="local", is_local=True))
        self.assertIsInstance(events[-1], Done)
        self.assertEqual(self.repo.calls, [])


class ShadowChatServiceTests(ChatServiceBudgetBase):
    mode = "shadow"

    def test_over_budget_turns_still_run(self):
        service = self.make(list(TOOL_THEN_TEXT), budget=1)
        with self.assertLogs("quantcore.security", "WARNING") as logs:
            events = self.run_turn(service)
        self.assertIsInstance(events[-1], Done)
        self.assertEqual(self.client.calls, 2)
        self.assertEqual(len(logs.output), 1)  # once per turn, not per call

    def test_db_failures_never_stop_a_turn(self):
        service = self.make(list(TOOL_THEN_TEXT))
        self.repo.fail.update({"reserve", "reconcile"})
        with self.assertLogs("quantcore.security", "WARNING"):
            events = self.run_turn(service)
        self.assertIsInstance(events[-1], Done)


if __name__ == "__main__":
    unittest.main()
