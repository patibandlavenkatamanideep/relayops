"""Access gate, tool scoping, and end-to-end pipeline tests.

Runnable with either pytest or `python3 -m unittest`. Covers the access gate,
server-side tool scoping (the security property), the baseline classifier, and
the end-to-end pipeline dispositions.
"""

from __future__ import annotations

import unittest

from src.access import gate
from src.core import data
from src.core.models import Action, Disposition, Intent
from src.graph.pipeline import handle_turn
from src.mcp import tools
from src.router.classifier import BaselineClassifier


class AccessGateTests(unittest.TestCase):
    def test_valid_token_authenticates_and_scopes(self):
        ctx = gate.authenticate("tok_alice")
        self.assertTrue(ctx.authenticated)
        self.assertEqual(ctx.customer_id, "cust_alice")
        self.assertTrue(ctx.may(Action.DEVICE_RESET))

    def test_invalid_token_denied(self):
        ctx = gate.authenticate("nope")
        self.assertFalse(ctx.authenticated)
        self.assertFalse(ctx.may(Action.DEVICE_RESET))


class ToolScopeTests(unittest.TestCase):
    def test_owner_can_reset_own_device(self):
        ctx = gate.authenticate("tok_alice")
        res = tools.device_reset(ctx, "dev_a1")
        self.assertTrue(res.ok)
        self.assertTrue(data.get_device("dev_a1").online)

    def test_scope_violation_refused_server_side(self):
        ctx = gate.authenticate("tok_alice")  # Alice
        res = tools.device_reset(ctx, "dev_b1")  # Bob's device
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "scope_violation")

    def test_account_lookup_only_returns_own_devices(self):
        ctx = gate.authenticate("tok_alice")
        res = tools.account_lookup(ctx)
        self.assertTrue(res.ok)
        owners = {d["device_id"] for d in res.data["devices"]}
        self.assertEqual(owners, {"dev_a1", "dev_a2"})


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.clf = BaselineClassifier()

    def test_reset_intent(self):
        self.assertEqual(self.clf.classify("please reboot my router").intent, Intent.RESET_DEVICE)

    def test_billing_intent(self):
        self.assertEqual(self.clf.classify("I need a refund").intent, Intent.BILLING)

    def test_unknown_low_confidence(self):
        c = self.clf.classify("xyzzy")
        self.assertEqual(c.intent, Intent.UNKNOWN)
        self.assertLess(c.confidence, 0.55)


class PipelineTests(unittest.TestCase):
    def test_happy_path_reset_responds(self):
        r = handle_turn("my router isn't working, reset it", auth_token="tok_alice")
        self.assertEqual(r.disposition, Disposition.RESPOND)
        self.assertEqual(r.intent, Intent.RESET_DEVICE)
        self.assertFalse(r.escalated)
        self.assertTrue(r.tool_results[-1].ok)

    def test_scope_violation_escalates_gracefully(self):
        r = handle_turn("reset device dev_b1", auth_token="tok_alice", device_id="dev_b1")
        # Tool refused server-side -> handoff with context, not a crash.
        self.assertFalse(r.tool_results[-1].ok)
        self.assertTrue(r.escalated)
        self.assertEqual(r.disposition, Disposition.ESCALATE)
        self.assertEqual(r.handoff_context["reason"], "tool_error:scope_violation")
        self.assertIn("specialist", r.text.lower())

    def test_billing_escalates(self):
        r = handle_turn("refund my bill", auth_token="tok_alice")
        self.assertTrue(r.escalated)
        self.assertEqual(r.disposition, Disposition.ESCALATE)

    def test_unauthenticated_escalates(self):
        r = handle_turn("reset my device", auth_token=None)
        self.assertTrue(r.escalated)
        self.assertEqual(r.handoff_context["reason"], "unauthenticated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
