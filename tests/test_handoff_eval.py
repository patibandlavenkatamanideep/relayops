"""Tests for handoff-quality + support-outcome evals (v1.3).

Asserts the safe-abandonment guard (every escalation in the adversarial suite
produces a usable handoff), the support-outcome contract, and the
override/rollback metric math over the labeled file.
"""

from __future__ import annotations

import unittest

from src.eval.agent_cases import CASES, run_case
from src.eval.handoff_eval import (
    handoff_completeness_rate,
    is_escalation,
    load_review_outcomes,
    review_outcome_metrics,
    support_outcome,
    support_outcome_complete_rate,
)


class HandoffCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.responses = [run_case(c) for c in CASES]

    def test_every_escalation_has_a_complete_handoff(self):
        rate, gaps = handoff_completeness_rate(self.responses)
        self.assertEqual(rate, 1.0, f"incomplete handoffs: {gaps}")

    def test_suite_has_escalations_to_measure(self):
        n = sum(1 for r in self.responses if is_escalation(r))
        self.assertGreaterEqual(n, 3)

    def test_support_outcome_complete_for_all_cases(self):
        rate, fails = support_outcome_complete_rate(self.responses)
        self.assertEqual(rate, 1.0, f"support-outcome failures: {fails}")


class SupportOutcomeUnitTests(unittest.TestCase):
    def test_escalation_without_handoff_fails(self):
        from src.core.models import AgentResponse, Disposition, Intent, Tier

        stranded = AgentResponse(
            text="I'm connecting you with someone.",
            intent=Intent.BILLING,
            tier=Tier.NONE,
            disposition=Disposition.ESCALATE,
            escalated=True,
            handoff_context={"reason": "billing/plan/payment"},  # missing owner/evidence/...
        )
        ok, fails = support_outcome(stranded)
        self.assertFalse(ok)
        self.assertTrue(any("incomplete handoff" in f for f in fails))

    def test_empty_reply_fails(self):
        from src.core.models import AgentResponse, Disposition, Intent, Tier

        empty = AgentResponse(
            text="   ",
            intent=Intent.GREETING,
            tier=Tier.TIER1,
            disposition=Disposition.RESPOND,
        )
        ok, fails = support_outcome(empty)
        self.assertFalse(ok)
        self.assertIn("no customer-facing message", fails)


class ReviewOutcomeMetricTests(unittest.TestCase):
    def setUp(self):
        self.rows = load_review_outcomes()

    def test_labeled_file_loads(self):
        self.assertGreater(len(self.rows), 0)

    def test_override_and_rollback_rates(self):
        m = review_outcome_metrics(self.rows)
        # 2 of 10 escalations marked could_automate; 1 of 5 auto-actions reversed
        self.assertEqual(m.total_escalations, 10)
        self.assertEqual(m.overrides, 2)
        self.assertAlmostEqual(m.review_override_rate, 0.2)
        self.assertEqual(m.auto_actions, 5)
        self.assertEqual(m.rollbacks, 1)
        self.assertAlmostEqual(m.post_action_rollback_rate, 0.2)

    def test_rates_are_zero_safe_on_empty(self):
        m = review_outcome_metrics([])
        self.assertEqual(m.review_override_rate, 0.0)
        self.assertEqual(m.post_action_rollback_rate, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
