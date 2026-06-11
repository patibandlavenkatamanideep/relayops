"""Tests for the action taxonomy + handoff builder (v1.3).

These lock the policy *table* (blast radius / reversibility / route per action),
the intent/reason -> action mapping, and the completeness contract for handoffs.
"""

from __future__ import annotations

import unittest

from src.core.models import Intent
from src.router import action_policy as ap
from src.router.action_policy import (
    ActionClass,
    BlastRadius,
    PolicyRoute,
    Reversibility,
)


class PolicyTableTests(unittest.TestCase):
    def test_every_action_class_has_a_policy(self):
        for action in ActionClass:
            self.assertIn(action, ap.POLICY_TABLE)

    def test_low_blast_reversible_actions_are_auto_or_respond(self):
        for action, policy in ap.POLICY_TABLE.items():
            if policy.blast_radius == BlastRadius.LOW:
                self.assertIn(
                    policy.route,
                    (PolicyRoute.AUTO_ACTION, PolicyRoute.RESPOND),
                    f"{action} is low-blast but routed {policy.route}",
                )

    def test_high_blast_actions_always_escalate(self):
        for action, policy in ap.POLICY_TABLE.items():
            if policy.blast_radius == BlastRadius.HIGH:
                self.assertEqual(
                    policy.route, PolicyRoute.ESCALATE, f"{action} high-blast must escalate"
                )

    def test_only_device_reset_is_auto_action(self):
        autos = [a for a, p in ap.POLICY_TABLE.items() if p.route == PolicyRoute.AUTO_ACTION]
        self.assertEqual(autos, [ActionClass.DEVICE_RESET])

    def test_account_access_change_is_irreversible(self):
        policy = ap.policy_for(ActionClass.ACCOUNT_ACCESS_CHANGE)
        self.assertEqual(policy.reversibility, Reversibility.IRREVERSIBLE)
        self.assertEqual(policy.owner, "identity_security")


class ClassifyActionTests(unittest.TestCase):
    def test_reset_intent_maps_to_device_reset(self):
        self.assertEqual(
            ap.classify_action(Intent.RESET_DEVICE, "action:reset"), ActionClass.DEVICE_RESET
        )

    def test_billing_bucket_is_refund_not_plan(self):
        # router reason is the generic "billing/plan/payment" bucket
        self.assertEqual(
            ap.classify_action(Intent.BILLING, "billing/plan/payment"),
            ActionClass.BILLING_REFUND,
        )

    def test_scope_violation_is_access_change(self):
        self.assertEqual(
            ap.classify_action(Intent.RESET_DEVICE, "tool_error:scope_violation"),
            ActionClass.ACCOUNT_ACCESS_CHANGE,
        )

    def test_unauthenticated_is_access_change(self):
        self.assertEqual(
            ap.classify_action(Intent.RESET_DEVICE, "unauthenticated"),
            ActionClass.ACCOUNT_ACCESS_CHANGE,
        )

    def test_low_confidence_is_unknown(self):
        self.assertEqual(ap.classify_action(Intent.UNKNOWN, "low_confidence"), ActionClass.UNKNOWN)


class HandoffBuilderTests(unittest.TestCase):
    def test_handoff_is_complete_and_owner_routed(self):
        handoff = ap.build_handoff(
            intent=Intent.BILLING,
            reason="billing/plan/payment",
            customer_message="I want a refund on my last bill",
            confidence=0.9,
        )
        ok, missing = ap.handoff_completeness(handoff)
        self.assertTrue(ok, f"missing: {missing}")
        self.assertEqual(handoff["owner"], "billing_support")
        self.assertEqual(handoff["evidence_quote"], "I want a refund on my last bill")
        self.assertEqual(handoff["channel"], "human_handoff")

    def test_handoff_keeps_legacy_reason_key(self):
        handoff = ap.build_handoff(
            intent=Intent.UNKNOWN,
            reason="low_confidence",
            customer_message="??",
            confidence=0.1,
        )
        # legacy contract relied on by the pipeline + existing tests
        self.assertEqual(handoff["reason"], "low_confidence")
        self.assertEqual(handoff["intent"], "unknown")

    def test_extra_overrides_merge(self):
        handoff = ap.build_handoff(
            intent=Intent.RESET_DEVICE,
            reason="guardrail_block",
            customer_message="reset it",
            confidence=0.9,
            extra={"violations": ["unapproved_amount"]},
        )
        self.assertEqual(handoff["violations"], ["unapproved_amount"])

    def test_completeness_flags_missing_fields(self):
        ok, missing = ap.handoff_completeness({"owner": "billing_support"})
        self.assertFalse(ok)
        self.assertIn("evidence_quote", missing)
        self.assertNotIn("owner", missing)

    def test_empty_owner_counts_as_missing(self):
        handoff = ap.build_handoff(
            intent=Intent.BILLING,
            reason="billing/plan/payment",
            customer_message="refund please",
            confidence=0.9,
        )
        handoff["owner"] = "   "
        ok, missing = ap.handoff_completeness(handoff)
        self.assertFalse(ok)
        self.assertIn("owner", missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
