"""Packet 2a tests: scope v1 validation + budget counters (keyproxy/scopes.py).

Hash agreement is proven against the Phase 1 cross-runtime vectors
(tests/vectors/keyproxy_envelope_v1.json) — the same bytes the TypeScript
side signs off on — so a scope hashed in the browser is the scope this
module validates.
"""

import copy
import json
import os
import unittest
from unittest.mock import patch

from keyproxy.scopes import (
    TOKEN_BUDGET_MESSAGE,
    BudgetExceededError,
    BudgetTracker,
    Scope,
    ScopeError,
    canonical_scope_json,
    compute_scope_hash,
    validate_scope,
)

VECTORS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "tests",
    "vectors",
    "keyproxy_envelope_v1.json",
)
with open(VECTORS_PATH, encoding="utf-8") as fh:
    VECTORS = json.load(fh)

# The plan's worked chat-turn example ("Scope schema (v1)").
CHAT_SCOPE = {
    "v": 1,
    "provider": "anthropic",
    "action": "chat.turn",
    "params": {},
    "budget": {
        "max_calls": 20,
        "max_mutations": 0,
        "max_tokens": 250_000,
        "ttl": 300,
    },
}


class TestVectorAgreement(unittest.TestCase):
    def test_canonicalization_vectors(self):
        for case in VECTORS["canonicalization"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    canonical_scope_json(case["input"]).decode("utf-8"),
                    case["canonical"],
                )
                self.assertEqual(
                    compute_scope_hash(case["input"]), case["scope_hash"]
                )

    def test_envelope_scope_hashes_match_aad(self):
        for case in VECTORS["envelopes"]:
            with self.subTest(case=case["name"]):
                scope = validate_scope(case["scope"], max_session_tokens=250_000)
                self.assertEqual(scope.scope_hash, case["aad"]["scope_hash"])


class TestValidateScope(unittest.TestCase):
    def validate(self, scope_obj, ceiling=250_000):
        return validate_scope(scope_obj, max_session_tokens=ceiling)

    def test_plan_chat_scope_validates(self):
        scope = self.validate(CHAT_SCOPE)
        self.assertIsInstance(scope, Scope)
        self.assertEqual(scope.provider, "anthropic")
        self.assertEqual(scope.action, "chat.turn")
        self.assertEqual(scope.max_calls, 20)
        self.assertEqual(scope.max_mutations, 0)
        self.assertEqual(scope.max_tokens, 250_000)
        self.assertEqual(scope.ttl, 300)

    def test_result_is_immutable(self):
        scope = self.validate(CHAT_SCOPE)
        with self.assertRaises(TypeError):
            scope.raw["provider"] = "evil"
        with self.assertRaises(TypeError):
            scope.params["extra"] = 1

    def test_absent_max_tokens_defaults_to_ceiling(self):
        obj = copy.deepcopy(CHAT_SCOPE)
        del obj["budget"]["max_tokens"]
        self.assertEqual(self.validate(obj, ceiling=1_000).max_tokens, 1_000)

    def test_max_tokens_above_ceiling_uses_system_threshold_copy(self):
        obj = copy.deepcopy(CHAT_SCOPE)
        obj["budget"]["max_tokens"] = 250_001
        with self.assertRaises(ScopeError) as ctx:
            self.validate(obj)
        self.assertIn("system-imposed threshold", str(ctx.exception))
        self.assertIn("development team", str(ctx.exception))

    def test_ceiling_env_default(self):
        obj = copy.deepcopy(CHAT_SCOPE)
        obj["budget"]["max_tokens"] = 200
        with patch.dict("os.environ", {"KEYPROXY_MAX_SESSION_TOKENS": "100"}):
            with self.assertRaises(ScopeError):
                validate_scope(obj)

    def test_structural_rejections(self):
        cases = {
            "not a dict": ["v", 1],
            "wrong version": {**CHAT_SCOPE, "v": 2},
            "bool version": {**CHAT_SCOPE, "v": True},
            "missing key": {k: v for k, v in CHAT_SCOPE.items() if k != "action"},
            "extra key": {**CHAT_SCOPE, "note": "hi"},
            "empty provider": {**CHAT_SCOPE, "provider": ""},
            "non-string action": {**CHAT_SCOPE, "action": 7},
            "non-dict params": {**CHAT_SCOPE, "params": []},
            "non-dict budget": {**CHAT_SCOPE, "budget": 5},
        }
        for name, obj in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ScopeError):
                    self.validate(obj)

    def test_budget_rejections(self):
        def with_budget(**overrides):
            obj = copy.deepcopy(CHAT_SCOPE)
            obj["budget"].update(overrides)
            for key, value in list(overrides.items()):
                if value is _ABSENT:
                    del obj["budget"][key]
            return obj

        _ABSENT = object()
        cases = {
            "missing max_calls": with_budget(max_calls=_ABSENT),
            "missing ttl": with_budget(ttl=_ABSENT),
            "unknown budget key": with_budget(max_retries=3),
            "zero max_calls": with_budget(max_calls=0),
            "negative max_mutations": with_budget(max_mutations=-1),
            "zero ttl": with_budget(ttl=0),
            "float max_calls": with_budget(max_calls=1.0),
            "bool max_mutations": with_budget(max_mutations=True),
            "string max_tokens": with_budget(max_tokens="1000"),
        }
        for name, obj in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ScopeError):
                    self.validate(obj)

    def test_non_canonicalizable_params_rejected(self):
        obj = copy.deepcopy(CHAT_SCOPE)
        obj["params"] = {"nan": float("nan")}
        with self.assertRaises(ScopeError) as ctx:
            self.validate(obj)
        self.assertEqual(str(ctx.exception), "scope is not canonicalizable")
        self.assertIsNone(ctx.exception.__cause__)

    def test_error_messages_never_echo_values(self):
        obj = copy.deepcopy(CHAT_SCOPE)
        obj["provider"] = "sk-ant-leaky-value"
        obj["v"] = 99
        with self.assertRaises(ScopeError) as ctx:
            self.validate(obj)
        self.assertNotIn("sk-ant-leaky-value", str(ctx.exception))
        self.assertNotIn("99", str(ctx.exception))


class TestBudgetTracker(unittest.TestCase):
    def tracker(self, max_calls=2, max_mutations=1, max_tokens=100, ttl=300):
        scope = validate_scope(
            {
                "v": 1,
                "provider": "anthropic",
                "action": "chat.turn",
                "params": {},
                "budget": {
                    "max_calls": max_calls,
                    "max_mutations": max_mutations,
                    "max_tokens": max_tokens,
                    "ttl": ttl,
                },
            },
            max_session_tokens=250_000,
        )
        return BudgetTracker(scope)

    def test_call_budget_exhausts(self):
        tracker = self.tracker(max_calls=2)
        tracker.charge_call()
        tracker.charge_call()
        with self.assertRaises(BudgetExceededError):
            tracker.charge_call()
        self.assertEqual(tracker.calls_used, 2)

    def test_zero_mutation_budget_blocks_first_mutation(self):
        scope = validate_scope(CHAT_SCOPE, max_session_tokens=250_000)
        tracker = BudgetTracker(scope)  # max_mutations = 0
        with self.assertRaises(BudgetExceededError):
            tracker.charge_mutation()

    def test_token_budget_is_posthoc_and_cumulative(self):
        tracker = self.tracker(max_tokens=100)
        tracker.charge_tokens(60)
        tracker.charge_tokens(40)  # exactly at the line is allowed
        self.assertEqual(tracker.tokens_used, 100)
        with self.assertRaises(BudgetExceededError) as ctx:
            tracker.charge_tokens(1)
        self.assertEqual(str(ctx.exception), TOKEN_BUDGET_MESSAGE)

    def test_exhaustion_latches_across_budget_lines(self):
        tracker = self.tracker(max_calls=1)
        tracker.charge_call()
        with self.assertRaises(BudgetExceededError):
            tracker.charge_call()
        # The session is dead: every other charge now fails too.
        with self.assertRaises(BudgetExceededError):
            tracker.charge_tokens(1)
        with self.assertRaises(BudgetExceededError):
            tracker.charge_mutation()

    def test_invalid_token_counts_rejected(self):
        tracker = self.tracker()
        for bad in (-1, 1.5, True, "10"):
            with self.subTest(count=bad):
                with self.assertRaises(ValueError):
                    tracker.charge_tokens(bad)
        self.assertEqual(tracker.tokens_used, 0)

    def test_exhaustion_messages_never_carry_values(self):
        tracker = self.tracker(max_calls=1)
        tracker.charge_call()
        with self.assertRaises(BudgetExceededError) as ctx:
            tracker.charge_call()
        self.assertNotIn("1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
