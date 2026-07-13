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
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent


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


if __name__ == "__main__":
    unittest.main()
