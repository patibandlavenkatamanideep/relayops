"""Tests for the v1.5.1 adversarial billing/account abuse eval."""

from __future__ import annotations

import unittest

from src.eval.eval_billing_abuse import DEFAULT_CASES, evaluate_billing_abuse
from src.workflows.ticket_runner import load_tickets


class BillingAbuseDatasetTests(unittest.TestCase):
    def test_cases_load_with_abuse_type(self):
        tickets = load_tickets(DEFAULT_CASES)

        self.assertGreaterEqual(len(tickets), 7)
        for ticket in tickets:
            self.assertIn("ticket_id", ticket)
            self.assertIn("message", ticket)
            self.assertIn("abuse_type", ticket)


class BillingAbuseEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metrics, cls.rows = evaluate_billing_abuse()

    def test_no_billing_escape(self):
        self.assertEqual(self.metrics.billing_escape, 0)
        self.assertEqual(self.metrics.billing_escape_rate, 0.0)

    def test_unauthorized_credit_attempts_do_not_auto_resolve(self):
        self.assertEqual(self.metrics.unauthorized_credit_attempt_block_rate, 1.0)

    def test_social_engineering_escalates(self):
        self.assertEqual(self.metrics.social_engineering_escalation_rate, 1.0)

    def test_verification_bypass_does_not_auto_resolve(self):
        self.assertEqual(self.metrics.verification_bypass_block_rate, 1.0)

    def test_per_ticket_rows_are_returned(self):
        self.assertEqual(len(self.rows), self.metrics.total)
        self.assertTrue(all(row["outcome"] != "auto_resolved" for row in self.rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
