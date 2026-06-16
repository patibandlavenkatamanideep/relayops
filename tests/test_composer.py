"""Composer selection + LLM-composer tests (mocked provider, no network)."""

from __future__ import annotations

import unittest
from unittest import mock

from src.composer.llm_composer import LLMComposer, _build_context, get_composer
from src.core.models import Classification, Disposition, Intent, ToolResult, TurnState
from src.graph.pipeline import TemplateComposer, handle_turn


def _state(text: str, intent: Intent) -> TurnState:
    s = TurnState(raw_text=text)
    s.text = text
    s.classification = Classification(intent=intent, confidence=0.9)
    return s


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


def _fake_anthropic(reply: str):
    """A stand-in anthropic module whose Anthropic().messages.create returns reply."""
    client = mock.MagicMock()
    client.messages.create.return_value = _FakeMessage(reply)
    module = mock.MagicMock()
    module.Anthropic.return_value = client
    return module, client


class ComposerSelectionTests(unittest.TestCase):
    def test_defaults_to_template_even_with_key_and_allow(self):
        env = {
            "ANTHROPIC_API_KEY": "sk-test",
            "RELAYOPS_COMPOSER": "template",
            "RELAYOPS_ALLOW_LLM": "true",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertIsInstance(get_composer(), TemplateComposer)

    def test_llm_mode_without_allow_flag_falls_back(self):
        # The public-deploy safety case: composer=llm + a leaked key, but the
        # allow-flag is not set -> still template.
        env = {
            "RELAYOPS_COMPOSER": "llm",
            "ANTHROPIC_API_KEY": "sk-test",
            "RELAYOPS_ALLOW_LLM": "false",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertIsInstance(get_composer(), TemplateComposer)

    def test_llm_mode_allowed_without_key_falls_back(self):
        env = {"RELAYOPS_COMPOSER": "llm", "RELAYOPS_ALLOW_LLM": "true", "ANTHROPIC_API_KEY": ""}
        with mock.patch.dict("os.environ", env, clear=False):
            self.assertIsInstance(get_composer(), TemplateComposer)

    def test_llm_returned_only_when_all_three_set(self):
        module, _ = _fake_anthropic("hi")
        env = {
            "RELAYOPS_COMPOSER": "llm",
            "RELAYOPS_ALLOW_LLM": "true",
            "ANTHROPIC_API_KEY": "sk-test",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            with mock.patch.dict("sys.modules", {"anthropic": module}):
                self.assertIsInstance(get_composer(), LLMComposer)


class LLMComposerTests(unittest.TestCase):
    def test_context_is_grounded_in_tools_and_retrieval(self):
        state = _state("is my router up?", Intent.DEVICE_STATUS)
        state.tool_results = [ToolResult(ok=True, data={"devices": [{"name": "Router"}]})]
        ctx = _build_context(state)
        self.assertIn("device_status", ctx)
        self.assertIn("is my router up?", ctx)
        self.assertIn("Router", ctx)

    def test_compose_returns_model_text(self):
        module, client = _fake_anthropic("Your router is online — all good!")
        with mock.patch.dict("sys.modules", {"anthropic": module}):
            composer = LLMComposer(model="claude-haiku-4-5")
            out = composer.compose(_state("status?", Intent.DEVICE_STATUS))
        self.assertEqual(out, "Your router is online — all good!")
        self.assertEqual(client.messages.create.call_args.kwargs["model"], "claude-haiku-4-5")

    def test_empty_draft_falls_back_to_handoff_line(self):
        module, _ = _fake_anthropic("   ")
        with mock.patch.dict("sys.modules", {"anthropic": module}):
            out = LLMComposer().compose(_state("hello", Intent.GREETING))
        self.assertIn("connect you", out)


class GuardrailVetsModelOutputTests(unittest.TestCase):
    """The headline property: a money claim the model invents is blocked live."""

    def test_invented_discount_from_llm_is_blocked_end_to_end(self):
        class InventsDiscount:
            def compose(self, _state: TurnState) -> str:
                return "Great news — I can give you 50% off your next bill!"

        # Use a reset request so the turn reaches the composer (a billing intent
        # would escalate before any drafting happens).
        resp = handle_turn(
            "my router isn't working, can you reset it?",
            auth_token="tok_alice",
            composer=InventsDiscount(),
        )

        self.assertEqual(resp.disposition, Disposition.ESCALATE)
        self.assertEqual(resp.guardrail_action, "block")
        self.assertIn("unapproved_discount", resp.guardrail_violations)


if __name__ == "__main__":
    unittest.main(verbosity=2)
