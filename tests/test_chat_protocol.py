"""Unit tests for the chat SSE protocol and directive validation.

Pure unit layer — no DB, no network, no Anthropic. Covers:
  * api.sse.sse_encode        — exact SSE frame format, single-line JSON, NaN rejection
  * api.sse.event_stream      — ChatEvent -> frame mapping, mid-stream error handling
  * quantcore.services.chat_tools.validate_directive — accept/reject table
  * quantcore.services.chat_tools.TOOL_SCHEMAS       — shape sanity for the Anthropic tool list
"""
import json
import math
import unittest

from api.sse import event_stream, sse_encode
from quantcore.services.chat import (
    Directive,
    Done,
    ErrorEvent,
    TextDelta,
    ToolStatus,
)
from quantcore.services.chat_tools import (
    BACKEND_COMPONENT_REGISTRY,
    BACKEND_INTERACTION_REGISTRY,
    TOOL_SCHEMAS,
    validate_directive,
    validate_interaction,
)


class TestSseEncode(unittest.TestCase):
    def test_exact_frame_format(self):
        frame = sse_encode("text", {"delta": "hi"})
        self.assertEqual(frame, 'event: text\ndata: {"delta": "hi"}\n\n')

    def test_payload_is_single_line_json(self):
        frame = sse_encode("directive", {"component": "signals", "props": {"ticker": "INTC"}})
        body = frame.split("data: ", 1)[1].rstrip("\n")
        self.assertNotIn("\n", body)
        self.assertEqual(
            json.loads(body), {"component": "signals", "props": {"ticker": "INTC"}}
        )

    def test_newlines_in_payload_strings_stay_escaped(self):
        frame = sse_encode("text", {"delta": "line1\nline2"})
        # The frame must contain exactly the protocol newlines, with the payload
        # newline escaped inside the JSON string.
        self.assertEqual(frame.count("\n"), 3)
        body = frame.split("data: ", 1)[1].rstrip("\n")
        self.assertEqual(json.loads(body)["delta"], "line1\nline2")

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            sse_encode("text", {"delta": math.nan})


class TestEventStream(unittest.TestCase):
    def _frames(self, events):
        return list(event_stream(iter(events)))

    def test_maps_each_event_type(self):
        frames = self._frames(
            [
                TextDelta(delta="hello"),
                ToolStatus(tool="get_rsi", args={"symbol": "AMD"}, state="running"),
                ToolStatus(tool="get_rsi", args={"symbol": "AMD"}, state="done"),
                Directive(component="signals", props={"ticker": "INTC"}),
                Done(stop_reason="end_turn"),
            ]
        )
        self.assertEqual(len(frames), 5)
        self.assertTrue(frames[0].startswith("event: text\n"))
        self.assertTrue(frames[1].startswith("event: tool_status\n"))
        self.assertIn('"state": "running"', frames[1])
        self.assertIn('"state": "done"', frames[2])
        self.assertTrue(frames[3].startswith("event: directive\n"))
        self.assertTrue(frames[4].startswith("event: done\n"))
        self.assertIn('"stop_reason": "end_turn"', frames[4])

    def test_error_event_maps_to_error_frame(self):
        frames = self._frames([ErrorEvent(message="boom")])
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].startswith("event: error\n"))
        self.assertIn('"message": "boom"', frames[0])

    def test_generator_exception_yields_error_frame_and_stops(self):
        def exploding():
            yield TextDelta(delta="partial")
            raise RuntimeError("upstream died")

        frames = list(event_stream(exploding()))
        self.assertEqual(len(frames), 2)
        self.assertTrue(frames[0].startswith("event: text\n"))
        self.assertTrue(frames[1].startswith("event: error\n"))
        self.assertIn("upstream died", frames[1])
        # No done frame after an error.
        self.assertFalse(any(f.startswith("event: done") for f in frames))

    def test_done_is_final_frame_on_clean_exit(self):
        frames = self._frames([TextDelta(delta="x"), Done(stop_reason="end_turn")])
        self.assertTrue(frames[-1].startswith("event: done\n"))

    def test_directive_frame_carries_component_id(self):
        frames = self._frames(
            [Directive(component="signals", props={"ticker": "INTC"}, component_id="abc123")]
        )
        body = json.loads(frames[0].split("data: ", 1)[1])
        self.assertEqual(body["component_id"], "abc123")


class TestValidateDirective(unittest.TestCase):
    def test_valid_components_accept_ticker(self):
        for component in ("signals", "live_price", "price_chart"):
            ok, reason = validate_directive(component, {"ticker": "INTC"})
            self.assertTrue(ok, f"{component} should accept ticker prop: {reason}")

    def test_unknown_component_rejected_with_reason(self):
        ok, reason = validate_directive("nuclear_launch", {"ticker": "INTC"})
        self.assertFalse(ok)
        self.assertIn("nuclear_launch", reason)

    def test_missing_ticker_rejected(self):
        ok, reason = validate_directive("signals", {})
        self.assertFalse(ok)
        self.assertIn("ticker", reason)

    def test_empty_ticker_rejected(self):
        ok, _ = validate_directive("signals", {"ticker": ""})
        self.assertFalse(ok)
        ok, _ = validate_directive("signals", {"ticker": "   "})
        self.assertFalse(ok)

    def test_non_string_ticker_rejected(self):
        ok, _ = validate_directive("signals", {"ticker": 42})
        self.assertFalse(ok)

    def test_extra_props_strictly_rejected(self):
        ok, reason = validate_directive("signals", {"ticker": "INTC", "explode": True})
        self.assertFalse(ok)
        self.assertIn("explode", reason)

    def test_non_dict_props_rejected(self):
        ok, _ = validate_directive("signals", "INTC")
        self.assertFalse(ok)


class TestSpreadPayoffDirective(unittest.TestCase):
    VALID = {
        "ticker": "INTC",
        "expiration": "2026-08-21",
        "long_strike": 140,
        "short_strike": 160.0,
        "kind": "call",
    }

    def test_valid_full_props_accepted(self):
        ok, reason = validate_directive("spread_payoff", dict(self.VALID))
        self.assertTrue(ok, reason)

    def test_int_and_float_strikes_both_accepted(self):
        props = dict(self.VALID, long_strike=140.5, short_strike=160)
        ok, reason = validate_directive("spread_payoff", props)
        self.assertTrue(ok, reason)

    def test_missing_strike_rejected(self):
        props = dict(self.VALID)
        del props["short_strike"]
        ok, reason = validate_directive("spread_payoff", props)
        self.assertFalse(ok)
        self.assertIn("short_strike", reason)

    def test_string_strike_rejected(self):
        props = dict(self.VALID, long_strike="140")
        ok, _ = validate_directive("spread_payoff", props)
        self.assertFalse(ok)

    def test_bool_strike_rejected(self):
        # bool is an int subclass — must not slip through as a number.
        props = dict(self.VALID, long_strike=True)
        ok, _ = validate_directive("spread_payoff", props)
        self.assertFalse(ok)

    def test_empty_expiration_rejected(self):
        props = dict(self.VALID, expiration="  ")
        ok, _ = validate_directive("spread_payoff", props)
        self.assertFalse(ok)

    def test_extra_prop_rejected(self):
        props = dict(self.VALID, leverage=10)
        ok, reason = validate_directive("spread_payoff", props)
        self.assertFalse(ok)
        self.assertIn("leverage", reason)


def interaction(**overrides):
    """A valid select_strike interaction; override fields per test."""
    base = {
        "component_id": "d1",
        "component": "spread_payoff",
        "action": "select_strike",
        "payload": {"strike": 120.0},
    }
    base.update(overrides)
    return base


class TestValidateInteraction(unittest.TestCase):
    def test_valid_select_strike_accepted(self):
        ok, reason = validate_interaction(interaction())
        self.assertTrue(ok, reason)

    def test_valid_with_props_snapshot_accepted(self):
        ok, reason = validate_interaction(
            interaction(
                props={
                    "ticker": "WMT",
                    "expiration": "2026-12-18",
                    "long_strike": 120,
                    "short_strike": 125,
                    "kind": "call",
                }
            )
        )
        self.assertTrue(ok, reason)

    def test_valid_reprice_leg_accepted(self):
        ok, reason = validate_interaction(
            interaction(action="reprice_leg", payload={"leg": "short", "strike": 122.5})
        )
        self.assertTrue(ok, reason)

    def test_valid_price_chart_select_point_accepted(self):
        ok, reason = validate_interaction(
            interaction(
                component="price_chart",
                action="select_point",
                payload={"date": "2026-07-10", "price": 96.99},
            )
        )
        self.assertTrue(ok, reason)

    def test_unknown_component_rejected(self):
        ok, reason = validate_interaction(interaction(component="nope"))
        self.assertFalse(ok)
        self.assertIn("nope", reason)

    def test_component_without_interactions_rejected(self):
        # 'signals' is registered but declares no interactions.
        ok, reason = validate_interaction(
            interaction(component="signals", action="select_strike")
        )
        self.assertFalse(ok)

    def test_unknown_action_rejected_with_valid_actions_listed(self):
        ok, reason = validate_interaction(interaction(action="explode"))
        self.assertFalse(ok)
        self.assertIn("select_strike", reason)

    def test_payload_missing_key_rejected(self):
        ok, _ = validate_interaction(interaction(payload={}))
        self.assertFalse(ok)

    def test_payload_extra_key_rejected(self):
        ok, _ = validate_interaction(
            interaction(payload={"strike": 120, "evil": "x"})
        )
        self.assertFalse(ok)

    def test_payload_wrong_type_rejected(self):
        ok, _ = validate_interaction(interaction(payload={"strike": "120"}))
        self.assertFalse(ok)

    def test_payload_bool_not_a_number(self):
        ok, _ = validate_interaction(interaction(payload={"strike": True}))
        self.assertFalse(ok)

    def test_non_dict_payload_rejected(self):
        ok, _ = validate_interaction(interaction(payload=[120]))
        self.assertFalse(ok)

    def test_invalid_props_snapshot_rejected(self):
        ok, _ = validate_interaction(interaction(props={"ticker": ""}))
        self.assertFalse(ok)

    def test_missing_component_id_rejected(self):
        bad = interaction()
        del bad["component_id"]
        ok, _ = validate_interaction(bad)
        self.assertFalse(ok)

    def test_non_dict_interaction_rejected(self):
        ok, _ = validate_interaction("click")
        self.assertFalse(ok)

    def test_interaction_registry_components_exist(self):
        # Every component declaring interactions must be a renderable component.
        for component in BACKEND_INTERACTION_REGISTRY:
            self.assertIn(component, BACKEND_COMPONENT_REGISTRY)


class TestToolSchemas(unittest.TestCase):
    EXPECTED_TOOLS = {
        "get_stock_price",
        "get_technical_signals",
        "get_rsi",
        "get_macd",
        "get_fundamental_score",
        "get_news_sentiment",
        "price_vertical_spread",
        "show_component",
    }

    def test_expected_tool_names_present(self):
        names = {t["name"] for t in TOOL_SCHEMAS}
        self.assertEqual(names, self.EXPECTED_TOOLS)

    def test_every_schema_has_description_and_input_schema(self):
        for tool in TOOL_SCHEMAS:
            self.assertIn("description", tool, tool["name"])
            self.assertEqual(tool["input_schema"]["type"], "object", tool["name"])

    def test_show_component_enum_matches_registry(self):
        show = next(t for t in TOOL_SCHEMAS if t["name"] == "show_component")
        enum = show["input_schema"]["properties"]["component"]["enum"]
        self.assertEqual(set(enum), set(BACKEND_COMPONENT_REGISTRY))


if __name__ == "__main__":
    unittest.main()
