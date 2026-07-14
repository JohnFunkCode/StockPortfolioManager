"""Unit tests for the ChatService agent loop (quantcore/services/chat.py).

Pure unit layer — the Anthropic SDK is never touched. The service depends on a
minimal ChatClient protocol (``stream_turn(system=, tools=, messages=)`` yielding
("delta", str) tuples then one ("final", message)), which lets these tests drive
the loop with a scripted client and lets CHAT_FAKE=1 swap in FakeChatClient.

No registry / DB imports here (that wiring is covered in test_chat_api.py with
the db_safety preamble); services are plain Mocks.
"""
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from quantcore.services.chat import (
    ChatService,
    Directive,
    Done,
    ErrorEvent,
    TextDelta,
    ToolStatus,
)
from quantcore.services.chat_fake import FakeChatClient


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def final(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class ScriptedClient:
    """Plays back scripted turns; records every stream_turn call."""

    def __init__(self, turns, cycle=False):
        self.turns = list(turns)
        self.cycle = cycle
        self.calls = []

    def stream_turn(self, *, system, tools, messages):
        # Snapshot messages — the service mutates its conversation list.
        self.calls.append(
            {"system": system, "tools": tools, "messages": [dict(m) for m in messages]}
        )
        if self.cycle:
            turn = self.turns[(len(self.calls) - 1) % len(self.turns)]
        else:
            turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        for delta in turn.get("deltas", []):
            yield ("delta", delta)
        yield ("final", turn["final"])


class ChatServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.prices = Mock()
        self.fundamentals = Mock()
        self.sentiment = Mock()
        self.options = Mock()

    def make_service(self, client, max_iterations=8):
        return ChatService(
            prices=self.prices,
            fundamentals=self.fundamentals,
            sentiment=self.sentiment,
            options=self.options,
            max_iterations=max_iterations,
            client_factory=lambda: client,
        )

    def run_chat(self, client, **kwargs):
        service = self.make_service(client, **kwargs)
        return list(service.stream_chat([{"role": "user", "content": "hi"}]))


class TestTextOnlyTurn(ChatServiceTestBase):
    def test_deltas_then_done_no_services_touched(self):
        client = ScriptedClient(
            [{"deltas": ["Hello ", "world"], "final": final("end_turn", text_block("Hello world"))}]
        )
        events = self.run_chat(client)
        self.assertEqual(
            [type(e) for e in events], [TextDelta, TextDelta, Done]
        )
        self.assertEqual(events[0].delta, "Hello ")
        self.assertEqual(events[1].delta, "world")
        self.assertEqual(events[2].stop_reason, "end_turn")
        self.assertEqual(self.prices.mock_calls, [])
        self.assertEqual(self.fundamentals.mock_calls, [])
        self.assertEqual(self.sentiment.mock_calls, [])


class TestToolTurn(ChatServiceTestBase):
    def test_dispatch_status_and_result_round_trip(self):
        self.prices.get_rsi.return_value = {"symbol": "AMD", "rsi": 55.1}
        client = ScriptedClient(
            [
                {
                    "deltas": ["Checking AMD."],
                    "final": final(
                        "tool_use",
                        text_block("Checking AMD."),
                        tool_use("tu_1", "get_rsi", {"symbol": "AMD", "period": 7}),
                    ),
                },
                {"deltas": ["RSI is 55."], "final": final("end_turn", text_block("RSI is 55."))},
            ]
        )
        events = self.run_chat(client)

        statuses = [e for e in events if isinstance(e, ToolStatus)]
        self.assertEqual([s.state for s in statuses], ["running", "done"])
        self.assertEqual(statuses[0].tool, "get_rsi")
        self.assertEqual(statuses[0].args, {"symbol": "AMD", "period": 7})
        self.assertIsInstance(events[-1], Done)

        self.prices.get_rsi.assert_called_once_with("AMD", 7, "1d")

        # Second model turn must carry the assistant content + tool_result.
        second_call_msgs = client.calls[1]["messages"]
        self.assertEqual(second_call_msgs[-2]["role"], "assistant")
        result_msg = second_call_msgs[-1]
        self.assertEqual(result_msg["role"], "user")
        result_block = result_msg["content"][0]
        self.assertEqual(result_block["type"], "tool_result")
        self.assertEqual(result_block["tool_use_id"], "tu_1")
        self.assertIn("55.1", result_block["content"])

    def test_unknown_tool_is_error_result_and_loop_continues(self):
        client = ScriptedClient(
            [
                {"final": final("tool_use", tool_use("tu_1", "get_whatever", {"x": 1}))},
                {"final": final("end_turn", text_block("ok"))},
            ]
        )
        events = self.run_chat(client)
        statuses = [e for e in events if isinstance(e, ToolStatus)]
        self.assertEqual([s.state for s in statuses], ["running", "error"])
        self.assertIsInstance(events[-1], Done)
        result_block = client.calls[1]["messages"][-1]["content"][0]
        self.assertTrue(result_block.get("is_error"))
        self.assertIn("get_whatever", result_block["content"])

    def test_dispatch_exception_surfaces_and_loop_continues(self):
        self.prices.get_rsi.side_effect = RuntimeError("yahoo down")
        client = ScriptedClient(
            [
                {"final": final("tool_use", tool_use("tu_1", "get_rsi", {"symbol": "AMD"}))},
                {"final": final("end_turn", text_block("sorry"))},
            ]
        )
        events = self.run_chat(client)
        statuses = [e for e in events if isinstance(e, ToolStatus)]
        self.assertEqual([s.state for s in statuses], ["running", "error"])
        self.assertIsInstance(events[-1], Done)
        result_block = client.calls[1]["messages"][-1]["content"][0]
        self.assertTrue(result_block.get("is_error"))
        self.assertIn("yahoo down", result_block["content"])

    def test_price_vertical_spread_dispatch(self):
        self.options.price_vertical_spread.return_value = {"debit": 4.94, "max_profit": 15.06}
        client = ScriptedClient(
            [
                {
                    "final": final(
                        "tool_use",
                        tool_use(
                            "tu_1",
                            "price_vertical_spread",
                            {
                                "symbol": "INTC",
                                "expiration": "2026-08-21",
                                "long_strike": 140,
                                "short_strike": 160,
                                "kind": "call",
                            },
                        ),
                    )
                },
                {"final": final("end_turn", text_block("Priced."))},
            ]
        )
        events = self.run_chat(client)
        statuses = [e for e in events if isinstance(e, ToolStatus)]
        self.assertEqual([s.state for s in statuses], ["running", "done"])
        self.options.price_vertical_spread.assert_called_once_with(
            "INTC",
            expiration="2026-08-21",
            long_strike=140,
            short_strike=160,
            kind="call",
        )
        result_block = client.calls[1]["messages"][-1]["content"][0]
        self.assertIn("4.94", result_block["content"])

    def test_spread_payoff_directive_passes_numeric_props(self):
        props = {
            "ticker": "INTC",
            "expiration": "2026-08-21",
            "long_strike": 140,
            "short_strike": 160.0,
            "kind": "call",
        }
        client = ScriptedClient(
            [
                {
                    "final": final(
                        "tool_use",
                        tool_use("tu_1", "show_component",
                                 {"component": "spread_payoff", "props": dict(props)}),
                    )
                },
                {"final": final("end_turn", text_block("Rendered."))},
            ]
        )
        events = self.run_chat(client)
        directives = [e for e in events if isinstance(e, Directive)]
        self.assertEqual(len(directives), 1)
        self.assertEqual(directives[0].component, "spread_payoff")
        self.assertEqual(directives[0].props, props)

    def test_nan_in_tool_result_sanitized_to_null(self):
        self.prices.get_rsi.return_value = {
            "rsi": math.nan,
            "series": [1.0, math.inf, -math.inf],
        }
        client = ScriptedClient(
            [
                {"final": final("tool_use", tool_use("tu_1", "get_rsi", {"symbol": "AMD"}))},
                {"final": final("end_turn", text_block("ok"))},
            ]
        )
        self.run_chat(client)
        content = client.calls[1]["messages"][-1]["content"][0]["content"]
        self.assertNotIn("NaN", content)
        self.assertNotIn("Infinity", content)
        parsed = json.loads(content)
        self.assertIsNone(parsed["rsi"])
        self.assertEqual(parsed["series"], [1.0, None, None])


class TestShowComponent(ChatServiceTestBase):
    def test_valid_directive_emitted_no_data_service_called(self):
        client = ScriptedClient(
            [
                {
                    "final": final(
                        "tool_use",
                        tool_use(
                            "tu_1",
                            "show_component",
                            {"component": "signals", "props": {"ticker": "INTC"}},
                        ),
                    )
                },
                {"final": final("end_turn", text_block("There it is."))},
            ]
        )
        events = self.run_chat(client)
        directives = [e for e in events if isinstance(e, Directive)]
        self.assertEqual(len(directives), 1)
        self.assertEqual(directives[0].component, "signals")
        self.assertEqual(directives[0].props, {"ticker": "INTC"})
        # No ToolStatus for show_component — it renders instantly client-side.
        self.assertEqual([e for e in events if isinstance(e, ToolStatus)], [])
        self.assertEqual(self.prices.mock_calls, [])
        result_block = client.calls[1]["messages"][-1]["content"][0]
        self.assertIn("rendered", result_block["content"])
        self.assertFalse(result_block.get("is_error"))

    def test_invalid_directive_no_event_error_result_model_can_retry(self):
        client = ScriptedClient(
            [
                {
                    "final": final(
                        "tool_use",
                        tool_use(
                            "tu_1",
                            "show_component",
                            {"component": "nuclear_launch", "props": {"ticker": "X"}},
                        ),
                    )
                },
                {"final": final("end_turn", text_block("My mistake."))},
            ]
        )
        events = self.run_chat(client)
        self.assertEqual([e for e in events if isinstance(e, Directive)], [])
        self.assertIsInstance(events[-1], Done)
        result_block = client.calls[1]["messages"][-1]["content"][0]
        self.assertTrue(result_block.get("is_error"))
        self.assertIn("nuclear_launch", result_block["content"])


def ui_interaction(**overrides):
    base = {
        "component_id": "d1",
        "component": "spread_payoff",
        "action": "select_strike",
        "payload": {"strike": 120.0},
    }
    base.update(overrides)
    return base


class TestInteractions(ChatServiceTestBase):
    """The UI->model backchannel: validated interactions are folded into the
    last user turn as [UI_INTERACTION] envelope lines before the model runs."""

    END = {"final": final("end_turn", text_block("ok"))}

    def test_interaction_folded_into_last_user_message(self):
        client = ScriptedClient([dict(self.END)])
        service = self.make_service(client)
        events = list(
            service.stream_chat(
                [{"role": "user", "content": "what about this strike?"}],
                interactions=[ui_interaction()],
            )
        )
        self.assertIsInstance(events[-1], Done)
        last = client.calls[0]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("what about this strike?", last["content"])
        self.assertIn("[UI_INTERACTION]", last["content"])
        self.assertIn('"select_strike"', last["content"])
        self.assertIn("120", last["content"])

    def test_multiple_interactions_fold_in_order(self):
        client = ScriptedClient([dict(self.END)])
        service = self.make_service(client)
        list(
            service.stream_chat(
                [{"role": "user", "content": "hi"}],
                interactions=[
                    ui_interaction(payload={"strike": 110.0}),
                    ui_interaction(payload={"strike": 130.0}, component_id="d2"),
                ],
            )
        )
        content = client.calls[0]["messages"][-1]["content"]
        self.assertEqual(content.count("[UI_INTERACTION]"), 2)
        self.assertLess(content.index("110"), content.index("130"))

    def test_props_snapshot_included_in_envelope(self):
        client = ScriptedClient([dict(self.END)])
        service = self.make_service(client)
        list(
            service.stream_chat(
                [{"role": "user", "content": "hi"}],
                interactions=[
                    ui_interaction(
                        props={
                            "ticker": "WMT",
                            "expiration": "2026-12-18",
                            "long_strike": 120,
                            "short_strike": 125,
                            "kind": "call",
                        }
                    )
                ],
            )
        )
        content = client.calls[0]["messages"][-1]["content"]
        self.assertIn("WMT", content)
        self.assertIn("2026-12-18", content)

    def test_no_interactions_leaves_conversation_untouched(self):
        client = ScriptedClient([dict(self.END)])
        service = self.make_service(client)
        list(service.stream_chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(client.calls[0]["messages"][-1]["content"], "hi")

    def test_invalid_interaction_errors_before_model_call(self):
        client = ScriptedClient([dict(self.END)])
        service = self.make_service(client)
        events = list(
            service.stream_chat(
                [{"role": "user", "content": "hi"}],
                interactions=[ui_interaction(action="explode")],
            )
        )
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ErrorEvent)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.options.mock_calls, [])

    def test_assistant_final_message_gets_own_user_turn(self):
        client = ScriptedClient([dict(self.END)])
        service = self.make_service(client)
        list(
            service.stream_chat(
                [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
                interactions=[ui_interaction()],
            )
        )
        msgs = client.calls[0]["messages"]
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("[UI_INTERACTION]", msgs[-1]["content"])
        self.assertEqual(msgs[-2]["content"], "hello")

    def test_system_prompt_documents_the_envelope(self):
        from quantcore.services.chat import SYSTEM_PROMPT

        self.assertIn("[UI_INTERACTION]", SYSTEM_PROMPT)


class TestDirectiveComponentIds(ChatServiceTestBase):
    def test_each_directive_gets_a_unique_component_id(self):
        show = lambda tu_id, ticker: {  # noqa: E731
            "final": final(
                "tool_use",
                tool_use(
                    tu_id,
                    "show_component",
                    {"component": "signals", "props": {"ticker": ticker}},
                ),
            )
        }
        client = ScriptedClient(
            [
                show("tu_1", "INTC"),
                show("tu_2", "AMD"),
                {"final": final("end_turn", text_block("done"))},
            ]
        )
        events = self.run_chat(client)
        directives = [e for e in events if isinstance(e, Directive)]
        self.assertEqual(len(directives), 2)
        self.assertTrue(all(d.component_id for d in directives))
        self.assertNotEqual(directives[0].component_id, directives[1].component_id)


class TestFailureModes(ChatServiceTestBase):
    def test_client_exception_yields_single_error_no_done(self):
        client = ScriptedClient([RuntimeError("api down")])
        events = self.run_chat(client)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ErrorEvent)
        self.assertIn("api down", events[0].message)

    def test_refusal_stop_reason_yields_error_no_done(self):
        client = ScriptedClient([{"final": final("refusal")}])
        events = self.run_chat(client)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertFalse(any(isinstance(e, Done) for e in events))

    def test_default_factory_builds_gateway_client_with_model_and_effort(self):
        """Without an injected factory, ChatService must construct the provider
        client from quantcore.gateways.anthropic_gateway (issue #78 wiring)."""
        from unittest.mock import patch

        with patch("quantcore.gateways.anthropic_gateway.AnthropicChatClient") as gateway_cls:
            gateway_cls.return_value.stream_turn.return_value = iter(
                [("final", final("end_turn", text_block("hi")))]
            )
            service = ChatService(
                prices=Mock(),
                fundamentals=Mock(),
                sentiment=Mock(),
                options=Mock(),
                model="model-x",
                effort="low",
            )
            events = list(service.stream_chat([{"role": "user", "content": "hey"}]))

        gateway_cls.assert_called_once_with("model-x", "low")
        self.assertIsInstance(events[-1], Done)

    def test_iteration_cap_terminates_with_error(self):
        looping_turn = {
            "final": final("tool_use", tool_use("tu_x", "get_rsi", {"symbol": "AMD"}))
        }
        self.prices.get_rsi.return_value = {"rsi": 50}
        client = ScriptedClient([looping_turn], cycle=True)
        events = self.run_chat(client, max_iterations=3)
        self.assertEqual(len(client.calls), 3)
        self.assertIsInstance(events[-1], ErrorEvent)
        self.assertFalse(any(isinstance(e, Done) for e in events))


class TestFakeChatClient(unittest.TestCase):
    """Pins the deterministic contract that route tests and Playwright E2E rely on."""

    def test_canned_script_yields_text_directive_text_done(self):
        service = ChatService(
            prices=Mock(),
            fundamentals=Mock(),
            sentiment=Mock(),
            options=Mock(),
            client_factory=FakeChatClient,
        )
        events = list(
            service.stream_chat([{"role": "user", "content": "How's INTC looking?"}])
        )
        kinds = [type(e) for e in events]
        self.assertIn(TextDelta, kinds)
        directives = [e for e in events if isinstance(e, Directive)]
        self.assertEqual(len(directives), 1)
        self.assertEqual(directives[0].component, "signals")
        self.assertEqual(directives[0].props, {"ticker": "INTC"})
        self.assertIsInstance(events[-1], Done)
        # Directive comes after the first text and before the closing text.
        first_text = kinds.index(TextDelta)
        directive_idx = kinds.index(Directive)
        self.assertGreater(directive_idx, first_text)
        self.assertLess(directive_idx, len(events) - 1)
        joined = "".join(e.delta for e in events if isinstance(e, TextDelta))
        self.assertIn("INTC", joined)

    def test_spread_prompt_renders_spread_payoff(self):
        """Prompts mentioning a spread play the WMT spread_payoff script —
        the interactive card the Playwright backchannel spec clicks on."""
        service = ChatService(
            prices=Mock(),
            fundamentals=Mock(),
            sentiment=Mock(),
            options=Mock(),
            client_factory=FakeChatClient,
        )
        events = list(
            service.stream_chat(
                [{"role": "user", "content": "Price a WMT 120/125 call spread"}]
            )
        )
        directives = [e for e in events if isinstance(e, Directive)]
        self.assertEqual(len(directives), 1)
        self.assertEqual(directives[0].component, "spread_payoff")
        self.assertEqual(directives[0].props["ticker"], "WMT")
        self.assertEqual(directives[0].props["long_strike"], 120)
        self.assertTrue(directives[0].component_id)
        self.assertIsInstance(events[-1], Done)
        joined = "".join(e.delta for e in events if isinstance(e, TextDelta))
        self.assertIn("risk graph", joined)

    def test_interaction_envelope_gets_acknowledgement_turn(self):
        """When the folded user turn carries a [UI_INTERACTION] envelope, the
        fake acknowledges it instead of replaying the INTC script — pins the
        contract the Playwright interaction spec relies on."""
        service = ChatService(
            prices=Mock(),
            fundamentals=Mock(),
            sentiment=Mock(),
            options=Mock(),
            client_factory=FakeChatClient,
        )
        events = list(
            service.stream_chat(
                [{"role": "user", "content": "what about this one?"}],
                interactions=[
                    {
                        "component_id": "d1",
                        "component": "spread_payoff",
                        "action": "select_strike",
                        "payload": {"strike": 120.0},
                    }
                ],
            )
        )
        self.assertIsInstance(events[-1], Done)
        self.assertEqual([e for e in events if isinstance(e, Directive)], [])
        joined = "".join(e.delta for e in events if isinstance(e, TextDelta))
        self.assertIn("120", joined)
        self.assertIn("selection", joined.lower())


if __name__ == "__main__":
    unittest.main()
