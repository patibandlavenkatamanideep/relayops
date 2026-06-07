"""Step 2 tests — the independent guardrail layer.

Runnable with pytest or `python3 -m unittest`. Covers the guardrail in isolation
(block on hallucinated offers/prices, redact PII, pass clean text) and wired into
the pipeline (a hallucinating composer is blocked and escalated).
"""

from __future__ import annotations

import unittest

from src.core.models import Disposition, TurnState
from src.graph.pipeline import handle_turn
from src.guardrails import guardrail


class HallucinatingComposer:
    def compose(self, state: TurnState) -> str:
        return "I reset it, and here's 50% off your next bill for just $9.99/month!"


class GuardrailUnitTests(unittest.TestCase):
    def test_blocks_unapproved_dollar_amount(self):
        r = guardrail.check("That'll be $9.99 today.")
        self.assertTrue(r.blocked)
        self.assertIn("unapproved_amount:$9.99", r.violations)

    def test_blocks_percent_off(self):
        r = guardrail.check("I can give you 50% off.")
        self.assertTrue(r.blocked)
        self.assertIn("unapproved_discount", r.violations)

    def test_blocks_recurring_price(self):
        r = guardrail.check("Only $5/month for the upgrade.")
        self.assertTrue(r.blocked)

    def test_allows_approved_free(self):
        r = guardrail.check("Resetting your device is free — done!")
        self.assertEqual(r.action, "pass")
        self.assertEqual(r.violations, [])

    def test_allows_zero_amount(self):
        r = guardrail.check("Your balance is $0.00.")
        self.assertEqual(r.action, "pass")

    def test_redacts_card_number(self):
        r = guardrail.check("I see card 4111 1111 1111 1111 on file.")
        self.assertEqual(r.action, "redact")
        self.assertIn("[redacted-card]", r.text)
        self.assertIn("pii:card", r.violations)

    def test_blocks_abusive_tone(self):
        r = guardrail.check("Don't be stupid, just reboot it.")
        self.assertTrue(r.blocked)

    def test_clean_text_passes_unchanged(self):
        text = "Done — I reset your router and it's back online."
        r = guardrail.check(text)
        self.assertEqual(r.action, "pass")
        self.assertEqual(r.text, text)


class GuardrailPipelineTests(unittest.TestCase):
    def test_hallucinated_offer_is_blocked_and_escalated(self):
        r = handle_turn(
            "reset my router",
            auth_token="tok_alice",
            composer=HallucinatingComposer(),
        )
        self.assertEqual(r.disposition, Disposition.ESCALATE)
        self.assertTrue(r.escalated)
        self.assertEqual(r.guardrail_action, "block")
        self.assertTrue(r.guardrail_violations)
        # The made-up deal never reaches the customer.
        self.assertNotIn("$9.99", r.text)
        self.assertNotIn("50%", r.text)
        # ...but it is preserved for the human in the handoff context.
        self.assertIn("$9.99", r.handoff_context["blocked_candidate"])

    def test_clean_reset_still_passes(self):
        r = handle_turn("reset my router", auth_token="tok_alice")
        self.assertEqual(r.disposition, Disposition.RESPOND)
        self.assertEqual(r.guardrail_action, "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
