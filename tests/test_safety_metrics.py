"""Split safety-metric tests (v1.8.1).

Covers counts/rates for each Hermes finding type, safe handling of an empty audit
store, handoff-completeness derivation from durable store rows (and its
placeholder when not derivable), and that metrics are surfaced in the operator
report.
"""

from __future__ import annotations

import unittest

from src.hermes import build_report, safety_metrics


def _record(**overrides) -> dict:
    base = {
        "turn_id": "t",
        "route": "respond",
        "action_class": "send_troubleshooting_link",
        "blast_radius": "low",
        "handoff_reason": None,
        "guardrail": {"verdict": "pass", "violations": []},
        "broker_decision_packet": {"decision": "allow", "policy_handle": "x"},
    }
    base.update(overrides)
    return base


def _unsafe_escape() -> dict:
    return _record(route="auto_action", action_class="billing_refund", blast_radius="high")


def _fail_closed() -> dict:
    return _record(route="human_escalation", handoff_reason="fail_closed:guardrail_unavailable")


def _over_block() -> dict:
    return _record(route="human_escalation", handoff_reason="unverifiable")


def _guardrail_block() -> dict:
    return _record(
        route="human_escalation",
        handoff_reason="guardrail_block",
        guardrail={"verdict": "block", "violations": ["unapproved_discount"]},
    )


class SafetyMetricsTests(unittest.TestCase):
    def test_unsafe_escape_count_and_rate(self):
        m = safety_metrics([_unsafe_escape(), _record(), _record(), _record()])
        self.assertEqual(m.unsafe_escape_count, 1)
        self.assertEqual(m.unsafe_escape_rate, 0.25)
        self.assertEqual(m.total_turns, 4)

    def test_over_block_count_and_rate(self):
        m = safety_metrics([_over_block(), _over_block(), _record()])
        self.assertEqual(m.over_block_count, 2)
        self.assertAlmostEqual(m.over_block_rate, 0.6667, places=4)

    def test_fail_closed_count_and_rate(self):
        m = safety_metrics([_fail_closed(), _record()])
        self.assertEqual(m.fail_closed_count, 1)
        self.assertEqual(m.fail_closed_rate, 0.5)

    def test_guardrail_block_count(self):
        m = safety_metrics([_guardrail_block(), _guardrail_block(), _record()])
        self.assertEqual(m.guardrail_block_count, 2)

    def test_empty_store_returns_zeros_safely(self):
        m = safety_metrics([])
        self.assertEqual(m.total_turns, 0)
        self.assertEqual(m.unsafe_escape_rate, 0.0)
        self.assertEqual(m.over_block_rate, 0.0)
        self.assertEqual(m.fail_closed_rate, 0.0)
        self.assertEqual(m.guardrail_block_count, 0)
        # No escalations -> nothing to complete -> vacuously complete.
        self.assertEqual(m.handoff_completeness_score, 1.0)

    def test_handoff_completeness_from_store_rows(self):
        rows = [
            _record(route="human_escalation", handoff_reason="unverifiable", handoff_complete="1"),
            _record(route="human_escalation", handoff_reason="unverifiable", handoff_complete="0"),
            _record(),  # not an escalation
        ]
        m = safety_metrics(rows)
        self.assertEqual(m.handoff_completeness_score, 0.5)

    def test_handoff_completeness_placeholder_when_not_derivable(self):
        # In-memory escalation records carry no `handoff_complete` flag.
        m = safety_metrics([_fail_closed(), _over_block()])
        self.assertIsNone(m.handoff_completeness_score)

    def test_metrics_included_in_operator_report(self):
        report = build_report([_unsafe_escape(), _fail_closed(), _record()])
        self.assertIn("unsafe_escape_rate", report.metrics)
        self.assertEqual(report.metrics["total_turns"], 3)
        self.assertEqual(report.metrics["unsafe_escape_count"], 1)
        self.assertEqual(report.metrics["fail_closed_count"], 1)
        # to_dict round-trips the metrics dict.
        self.assertEqual(report.to_dict()["metrics"], report.metrics)


if __name__ == "__main__":
    unittest.main(verbosity=2)
