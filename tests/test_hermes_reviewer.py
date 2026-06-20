"""Hermes operator-agent tests.

Hermes is a read-only reviewer of audit traces. These tests cover the finding
taxonomy (unsafe_escape, fail_closed, guardrail_block, over_block), that clean
turns produce no finding, that packet fields work as dicts OR JSON strings, the
aggregated report, and that Hermes stays advisory (no execute surface).
"""

from __future__ import annotations

import json
import unittest

from src import hermes
from src.hermes import build_report, review, review_record


def _record(**overrides) -> dict:
    """A minimal audit-record dict; override the fields a test cares about."""
    base = {
        "turn_id": "turn_x",
        "route": "respond",
        "action_class": "send_troubleshooting_link",
        "blast_radius": "low",
        "handoff_reason": None,
        "guardrail": {"checked": True, "verdict": "pass", "violations": []},
        "broker_decision_packet": {"decision": "allow", "policy_handle": "faq.answer.requires_grounding"},
    }
    base.update(overrides)
    return base


class HermesReviewerTests(unittest.TestCase):
    def test_clean_turn_has_no_finding(self):
        self.assertIsNone(review_record(_record()))

    def test_fail_closed_finding(self):
        f = review_record(_record(route="human_escalation", handoff_reason="fail_closed:composer_timeout"))
        self.assertIsNotNone(f)
        self.assertEqual(f.finding_type, "fail_closed")
        self.assertEqual(f.severity, "high")
        self.assertIn("composer_timeout", f.suggested_test)
        self.assertEqual(f.suggested_policy_gap, "safety.fail_closed")
        self.assertTrue(f.human_review_required)

    def test_guardrail_block_finding(self):
        f = review_record(
            _record(
                route="human_escalation",
                handoff_reason="guardrail_block",
                guardrail={"checked": True, "verdict": "block", "violations": ["unapproved_discount"]},
                broker_decision_packet={"decision": "block", "policy_handle": "response.guardrail.offer_pii_tone"},
            )
        )
        self.assertEqual(f.finding_type, "guardrail_block")
        self.assertEqual(f.severity, "medium")
        self.assertIn("unapproved_discount", f.suggested_test)

    def test_over_block_finding(self):
        f = review_record(_record(route="human_escalation", handoff_reason="unverifiable"))
        self.assertEqual(f.finding_type, "over_block")
        self.assertEqual(f.severity, "low")
        self.assertEqual(f.suggested_policy_gap, "faq.answer.requires_grounding")

    def test_unsafe_escape_is_critical(self):
        f = review_record(_record(route="auto_action", action_class="billing_refund", blast_radius="high"))
        self.assertEqual(f.finding_type, "unsafe_escape")
        self.assertEqual(f.severity, "critical")

    def test_packet_fields_accept_json_strings(self):
        # Durable store rows serialise packets as JSON strings; Hermes must cope.
        f = review_record(
            _record(
                route="human_escalation",
                handoff_reason="guardrail_block",
                guardrail=json.dumps({"verdict": "block", "violations": ["unapproved_amount:$9.99"]}),
                broker_decision_packet=json.dumps({"decision": "block", "policy_handle": "response.guardrail.offer_pii_tone"}),
            )
        )
        self.assertEqual(f.finding_type, "guardrail_block")
        self.assertIn("unapproved_amount:$9.99", f.suggested_test)


class HermesReportTests(unittest.TestCase):
    def _records(self) -> list[dict]:
        return [
            _record(),  # clean -> no finding
            _record(route="human_escalation", handoff_reason="fail_closed:guardrail_unavailable"),
            _record(route="human_escalation", handoff_reason="unverifiable"),
            _record(route="auto_action", action_class="billing_refund", blast_radius="high"),
        ]

    def test_review_drops_clean_turns(self):
        findings = review(self._records())
        self.assertEqual(len(findings), 3)  # 4 records, 1 clean

    def test_report_aggregates(self):
        report = build_report(self._records())
        self.assertEqual(len(report.findings), 3)
        self.assertIn("finding(s)", report.failure_summary)
        self.assertTrue(report.suggested_tests)
        self.assertIn("faq.answer.requires_grounding", report.suggested_policy_gaps)
        # GitHub issues only for high/critical (fail_closed + unsafe_escape), not the low over_block.
        self.assertEqual(len(report.suggested_github_issues), 2)
        self.assertTrue(report.release_notes_draft.startswith("Operator review"))

    def test_empty_report_is_clean(self):
        report = build_report([_record(), _record()])
        self.assertEqual(report.findings, [])
        self.assertIn("No findings", report.failure_summary)


class HermesStoreIntegrationTests(unittest.TestCase):
    """Hermes must work against durable store rows (packets as JSON strings,
    guardrail as a flat `guardrail_verdict` column), not just in-memory dicts."""

    def _store(self):
        import os
        import tempfile

        from src.observability.audit_store import AuditStore

        path = os.path.join(tempfile.mkdtemp(), "hermes_audit.sqlite3")
        return AuditStore(path)

    def test_review_over_store_rows(self):
        store = self._store()
        store.write_row(
            {
                "turn_id": "t_fail",
                "route": "human_escalation",
                "action_class": "send_troubleshooting_link",
                "blast_radius": "low",
                "handoff_reason": "fail_closed:guardrail_unavailable",
                "broker_decision_packet": json.dumps({"decision": "escalate", "policy_handle": "safety.fail_closed"}),
            }
        )
        store.write_row(
            {
                "turn_id": "t_block",
                "route": "human_escalation",
                "action_class": "device_reset",
                "blast_radius": "low",
                "handoff_reason": "guardrail_block",
                "guardrail_verdict": "block",
                "broker_decision_packet": json.dumps({"decision": "block", "policy_handle": "response.guardrail.offer_pii_tone"}),
            }
        )
        store.write_row({"turn_id": "t_ok", "route": "respond", "blast_radius": "low"})

        report = build_report(store.list(limit=50))
        store.close()

        types = {f.finding_type for f in report.findings}
        self.assertEqual(types, {"fail_closed", "guardrail_block"})
        self.assertEqual(len(report.findings), 2)


class HermesIsAdvisoryOnlyTests(unittest.TestCase):
    def test_no_execute_surface(self):
        # Hermes must not expose any action/execution API — it only reviews.
        forbidden = ("execute", "send", "apply", "approve", "refund", "override", "run_tool", "compose")
        names = [n.lower() for n in dir(hermes)]
        for bad in forbidden:
            self.assertFalse(
                any(bad in n for n in names),
                f"Hermes unexpectedly exposes an action-like name containing '{bad}'",
            )

    def test_findings_default_to_human_review(self):
        f = review_record(_record(route="human_escalation", handoff_reason="fail_closed:composer_timeout"))
        self.assertTrue(f.human_review_required)


if __name__ == "__main__":
    unittest.main(verbosity=2)
