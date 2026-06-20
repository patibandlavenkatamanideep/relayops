"""Fail-closed invariant tests.

If a safety-critical layer (policy broker, scoped tool, RAG, composer, guardrail)
cannot render a verdict — it raises — RelayOps must fail CLOSED: force a safe
human handoff with a reason code, generate the reply from the broker decision
packet (never the raw/failed model output), and still write an audit record.
It must never crash-through or silently allow.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.core.models import Disposition
from src.graph.pipeline import handle_turn
from src.observability.audit_ledger import AuditLedger


class BoomComposer:
    """A composer that fails to render a verdict."""

    def compose(self, state) -> str:  # noqa: ANN001
        raise RuntimeError("composer unavailable")


class FailClosedTests(unittest.TestCase):
    def _assert_safe_handoff(self, r, reason_prefix: str) -> None:
        self.assertEqual(r.disposition, Disposition.ESCALATE)
        self.assertTrue(r.escalated)
        self.assertIsNotNone(r.handoff_context)
        # Reply is the broker-generated safe text, not a crash or model output.
        self.assertIn("specialist", r.text.lower())
        self.assertTrue((r.handoff_context or {}).get("reason", "").startswith(reason_prefix))

    def test_composer_failure_fails_closed(self):
        r = handle_turn(
            "my router isn't working, can you reset it?",
            auth_token="tok_alice",
            composer=BoomComposer(),
        )
        self._assert_safe_handoff(r, "fail_closed:composer_timeout")

    def test_guardrail_failure_fails_closed(self):
        with patch("src.guardrails.guardrail.check", side_effect=RuntimeError("guardrail down")):
            r = handle_turn("my router isn't working, can you reset it?", auth_token="tok_alice")
        self._assert_safe_handoff(r, "fail_closed:guardrail_unavailable")

    def test_fail_closed_records_broker_packet_and_audit(self):
        ledger = AuditLedger()
        handle_turn(
            "my router isn't working, can you reset it?",
            auth_token="tok_alice",
            composer=BoomComposer(),
            audit=ledger,
        )
        rec = ledger.records[-1]
        self.assertEqual(rec.broker_decision_packet.get("policy_handle"), "safety.fail_closed")
        self.assertEqual(rec.broker_decision_packet.get("decision"), "escalate")
        self.assertTrue((rec.handoff_reason or "").startswith("fail_closed:"))

    def test_fail_closed_reply_does_not_leak_unverified_content(self):
        # Even if the composer "would have" produced unsafe text, a raised
        # composer never reaches the customer — the broker reply is used.
        r = handle_turn(
            "my router isn't working, can you reset it?",
            auth_token="tok_alice",
            composer=BoomComposer(),
        )
        self.assertNotIn("$", r.text)
        self.assertNotIn("%", r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
