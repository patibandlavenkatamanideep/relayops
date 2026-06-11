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

    # --- recall on money claims the original regex-only net missed ---

    def test_blocks_spelled_out_recurring_price(self):
        # "nine ninety-nine a month" has no $ and no digits at all.
        r = guardrail.check("It's nine ninety-nine a month after the trial.")
        self.assertTrue(r.blocked)

    def test_blocks_currency_word_amount(self):
        for text in ("Just 5 bucks.", "That's twenty dollars.", "The fee is USD 20."):
            with self.subTest(text=text):
                self.assertTrue(guardrail.check(text).blocked)

    def test_blocks_non_dollar_currency_symbol(self):
        self.assertTrue(guardrail.check("Pay €20 and it ships today.").blocked)
        self.assertTrue(guardrail.check("It's £5 extra.").blocked)

    def test_blocks_percent_word_and_half_off(self):
        self.assertTrue(guardrail.check("I'll take 20 percent off.").blocked)
        self.assertTrue(guardrail.check("You can have it half price.").blocked)

    def test_blocks_bare_number_next_to_money_cue(self):
        self.assertTrue(guardrail.check("There's a fee of 15 to expedite.").blocked)
        self.assertTrue(guardrail.check("It costs 9.99 per month.").blocked)

    def test_operational_numbers_do_not_false_block(self):
        # Numbers with no money cue must still pass.
        for text in (
            "The reset takes about 5 minutes.",
            "I see ticket 4321 on your account.",
            "Try again in 10 minutes.",
            "There is no charge for a reset.",
        ):
            with self.subTest(text=text):
                self.assertFalse(guardrail.check(text).blocked)

    def test_semantic_backstop_blocks_when_lexical_passes(self):
        # A phrasing with no lexical money cue, caught only by the backstop.
        clean_to_regex = "We'll comp your next cycle as a goodwill gesture."
        self.assertFalse(guardrail.check(clean_to_regex).blocked)
        r = guardrail.check(clean_to_regex, semantic_backstop=lambda _: True)
        self.assertTrue(r.blocked)
        self.assertIn("semantic_backstop", r.violations)


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
