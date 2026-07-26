"""Architecture-guard tests for the Anthropic provider gateway (issue #78).

Per architectural-standard-v2 §5.3, external-provider adapters live in
quantcore/gateways/. These tests pin three properties of the extraction:

  1. The gateway module imports WITHOUT pulling in the anthropic SDK
     (requirements-base images — MCP wrappers, report job, CI — don't ship it;
     the SDK must load lazily on first client construction only).
  2. The gateway exposes the ChatClient protocol surface (stream_turn).
  3. quantcore/services/chat.py no longer contains any provider adapter code —
     services hold business logic only.

No test here constructs the real client (that would require the SDK + a key);
the ChatService->gateway wiring is covered behaviorally in test_chat_service.
"""
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestAnthropicGatewayExtraction(unittest.TestCase):
    def test_gateway_module_imports_without_anthropic_sdk(self):
        code = (
            "import sys; "
            "import quantcore.gateways.anthropic_gateway; "
            "assert 'anthropic' not in sys.modules, 'anthropic imported eagerly'; "
            "print('lazy-ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("lazy-ok", result.stdout)

    def test_gateway_exposes_chat_client_protocol(self):
        from quantcore.gateways.anthropic_gateway import AnthropicChatClient

        self.assertTrue(callable(getattr(AnthropicChatClient, "stream_turn", None)))

    def test_services_chat_contains_no_provider_adapter(self):
        src = (REPO / "quantcore" / "services" / "chat.py").read_text()
        # SDK import specifically ("import anthropic" / "from anthropic import ...");
        # importing the gateway MODULE (anthropic_gateway) is the intended seam.
        sdk_import = re.search(r"^\s*(import anthropic\b(?!_)|from anthropic\b(?!_))", src, re.M)
        self.assertIsNone(sdk_import, f"SDK import found in services/chat.py: {sdk_import}")
        self.assertNotIn("class AnthropicChatClient", src)

    def test_registry_module_still_imports_lazily(self):
        # The registry is imported by MCP stdio servers at startup; the gateway
        # move must not make it (transitively) import the SDK.
        code = (
            "import sys; "
            "import quantcore.services.registry; "
            "assert 'anthropic' not in sys.modules, 'registry pulls anthropic'; "
            "print('registry-ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("registry-ok", result.stdout)


class _FakeStream:
    """Stands in for the SDK's streaming context manager — yields nothing."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        # The quantcore path uses the object directly; keyproxy serializes it.
        return types.SimpleNamespace(
            stop_reason="end_turn",
            content=[],
            model_dump=lambda mode="json": {"stop_reason": "end_turn", "content": []},
        )


class _FakeSDK:
    """Minimal fake `anthropic` module recording the kwargs of each call."""

    def __init__(self):
        self.init_kwargs = None
        self.stream_kwargs = None

        def stream(**kwargs):
            self.stream_kwargs = kwargs
            return _FakeStream()

        def Anthropic(**kwargs):  # noqa: N802 — mirrors the SDK's class name
            self.init_kwargs = kwargs
            return types.SimpleNamespace(
                beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream))
            )

        self.module = types.SimpleNamespace(Anthropic=Anthropic)

    def __enter__(self):
        self._saved = sys.modules.get("anthropic")
        sys.modules["anthropic"] = self.module
        return self

    def __exit__(self, *exc):
        if self._saved is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = self._saved
        return False


class TestTurnRequestShape(unittest.TestCase):
    """The outgoing request carries ONLY parameters every model accepts.

    Regression guard for the sidekick model selector (issue #124). The request
    shape was validated against claude-fable-5 and generalized to all three
    models, but `fallbacks` is fable-only: claude-sonnet-5 and claude-opus-4-8
    reject the entire turn with a 400 ("'<model>' does not support the
    `fallbacks` parameter") rather than ignoring the unknown parameter. Every
    non-fable sidekick turn failed. `fallbacks` was then dropped outright.

    These assert on the kwargs actually handed to the SDK, so they also catch
    a *new* unvetted parameter being added later — which is the failure mode
    that produced the original bug.
    """

    ALLOWED_KWARGS = {
        "model",
        "max_tokens",
        "system",
        "tools",
        "messages",
        "output_config",
    }

    def _quantcore_kwargs(self, model):
        from quantcore.gateways.anthropic_gateway import AnthropicChatClient

        with _FakeSDK() as sdk:
            client = AnthropicChatClient(model=model, effort="medium")
            list(client.stream_turn(system="sys", tools=[], messages=[]))
        return sdk.stream_kwargs

    def _keyproxy_kwargs(self, model):
        from keyproxy.providers import anthropic as kp

        with _FakeSDK() as sdk:
            list(
                kp.stream_turn(
                    "sk-test",
                    model=model,
                    effort="medium",
                    max_tokens=16384,
                    system="sys",
                    tools=[],
                    messages=[],
                )
            )
        return sdk.stream_kwargs

    def test_no_fallbacks_or_betas_on_any_model(self):
        from keyproxy.providers.anthropic import ALLOWED_MODELS

        for model in sorted(ALLOWED_MODELS):
            for name, kwargs in (
                ("quantcore", self._quantcore_kwargs(model)),
                ("keyproxy", self._keyproxy_kwargs(model)),
            ):
                with self.subTest(model=model, path=name):
                    self.assertNotIn("fallbacks", kwargs)
                    self.assertNotIn("betas", kwargs)

    def test_request_shape_is_the_vetted_set_on_both_paths(self):
        # A new kwarg here must be checked against all three models first —
        # an unsupported one 400s the whole turn instead of being ignored.
        from keyproxy.providers.anthropic import ALLOWED_MODELS

        for model in sorted(ALLOWED_MODELS):
            for name, kwargs in (
                ("quantcore", self._quantcore_kwargs(model)),
                ("keyproxy", self._keyproxy_kwargs(model)),
            ):
                with self.subTest(model=model, path=name):
                    self.assertEqual(set(kwargs), self.ALLOWED_KWARGS)
                    self.assertEqual(kwargs["model"], model)
                    self.assertEqual(kwargs["output_config"], {"effort": "medium"})

    def test_thinking_is_never_sent(self):
        # Omitted deliberately (on by default); sending it is not uniform.
        self.assertNotIn("thinking", self._quantcore_kwargs("claude-sonnet-5"))
        self.assertNotIn("thinking", self._keyproxy_kwargs("claude-sonnet-5"))

    def test_keyproxy_pins_egress_base_url(self):
        # The allowlist-by-construction guarantee: no env override may
        # redirect a decrypted key somewhere other than api.anthropic.com.
        from keyproxy.providers import anthropic as kp

        with _FakeSDK() as sdk:
            list(
                kp.stream_turn(
                    "sk-test",
                    model="claude-sonnet-5",
                    effort="medium",
                    max_tokens=16384,
                    system="sys",
                    tools=[],
                    messages=[],
                )
            )
        self.assertEqual(sdk.init_kwargs["base_url"], kp.BASE_URL)
        self.assertEqual(kp.BASE_URL, "https://api.anthropic.com")

    def test_output_budget_matches_across_both_paths(self):
        # Thinking tokens share this budget, so a mismatch between the
        # in-process and the BYOK client means truncation behaviour depends on
        # which one served the turn (issue #121 symptom).
        import inspect

        from quantcore.gateways.anthropic_gateway import AnthropicChatClient
        from quantcore.gateways.keyproxy_gateway import KeyProxyChatClient

        direct = inspect.signature(AnthropicChatClient.__init__).parameters
        byok = inspect.signature(KeyProxyChatClient.__init__).parameters
        self.assertEqual(direct["max_tokens"].default, byok["max_tokens"].default)
        self.assertEqual(direct["max_tokens"].default, 16384)


if __name__ == "__main__":
    unittest.main()

