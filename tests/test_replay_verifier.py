"""Replay verification tests (v2.4).

Covers the verifier's comparison of two audited flows — broker decision, action
envelope, tool response, customer scope, audit completeness, and double-execution
risk — the deterministic reason codes, the replay metrics, and that Hermes
surfaces replay mismatches as read-only advisory findings.
"""

from __future__ import annotations

import unittest

from src import hermes
from src.replay import (
    ACTION_ENVELOPE_MISMATCH,
    BROKER_DECISION_MISMATCH,
    MISSING_ORIGINAL_AUDIT_RECORD,
    MISSING_REPLAY_AUDIT_RECORD,
    REPLAY_DOUBLE_EXECUTION_RISK,
    REPLAY_SCOPE_MISMATCH,
    TOOL_RESPONSE_MISMATCH,
    ReplayStatus,
    replay_metrics,
    verify,
    verify_batch,
)

_OK_TOOL = {"name": "device_reset", "ok": True, "error": ""}


def _env(
    *,
    action="device_reset",
    target="dev_a1",
    status="succeeded",
    action_id="act_1",
    policy_handle="device.reset.allowed_if_scoped",
    customer="cust_alice",
    idem=None,
):
    return {
        "action_id": action_id,
        "turn_id": "t1",
        "customer_id": customer,
        "action": action,
        "target_resource": target,
        "idempotency_key": idem or f"{customer}:{action}:{target}",
        "policy_handle": policy_handle,
        "blast_radius": "low",
        "reversibility": "yes",
        "status": status,
        "result": {"device_id": target, "online": True},
    }


def _record(
    *,
    turn_id="t1",
    customer="cust_alice",
    decision="allow",
    policy_handle="device.reset.allowed_if_scoped",
    matched_rule="device_reset_allowed_after_auth_and_scope",
    reason_code="policy_allow",
    envelope=None,
    tool=None,
):
    return {
        "turn_id": turn_id,
        "customer_id": customer,
        "broker_decision_packet": {
            "decision": decision,
            "policy_handle": policy_handle,
            "matched_rule": matched_rule,
            "reason_code": reason_code,
        },
        "action_envelopes": [envelope] if envelope else [],
        "tool_call": tool,
    }


class VerifierTests(unittest.TestCase):
    def test_identical_replay_passes(self):
        rec = _record(envelope=_env(), tool=dict(_OK_TOOL))
        result = verify(rec, rec)
        self.assertEqual(result.status, ReplayStatus.PASS)
        self.assertTrue(result.passed())
        self.assertEqual(result.mismatches, [])
        self.assertTrue(result.comparisons)  # evidence recorded

    def test_broker_decision_mismatch_fails(self):
        original = _record(decision="allow", tool=dict(_OK_TOOL), envelope=_env())
        replayed = _record(decision="escalate", tool=dict(_OK_TOOL), envelope=_env())
        result = verify(original, replayed)
        self.assertFalse(result.passed())
        self.assertEqual(result.status, ReplayStatus.MISMATCH)
        self.assertIn(BROKER_DECISION_MISMATCH, result.reason_codes())

    def test_action_envelope_mismatch_fails(self):
        original = _record(envelope=_env(target="dev_a1"), tool=dict(_OK_TOOL))
        replayed = _record(envelope=_env(target="dev_a2"), tool=dict(_OK_TOOL))
        result = verify(original, replayed)
        self.assertFalse(result.passed())
        self.assertIn(ACTION_ENVELOPE_MISMATCH, result.reason_codes())

    def test_tool_response_mismatch_fails(self):
        original = _record(envelope=_env(), tool={"name": "device_reset", "ok": True, "error": ""})
        replayed = _record(
            envelope=_env(), tool={"name": "device_reset", "ok": False, "error": "scope_violation"}
        )
        result = verify(original, replayed)
        self.assertFalse(result.passed())
        self.assertIn(TOOL_RESPONSE_MISMATCH, result.reason_codes())

    def test_missing_original_audit_record_fails(self):
        result = verify(None, _record(envelope=_env(), tool=dict(_OK_TOOL)))
        self.assertFalse(result.passed())
        self.assertEqual(result.status, ReplayStatus.MISSING_AUDIT)
        self.assertIn(MISSING_ORIGINAL_AUDIT_RECORD, result.reason_codes())

    def test_missing_replay_audit_record_fails(self):
        result = verify(_record(envelope=_env(), tool=dict(_OK_TOOL)), None)
        self.assertFalse(result.passed())
        self.assertEqual(result.status, ReplayStatus.MISSING_AUDIT)
        self.assertIn(MISSING_REPLAY_AUDIT_RECORD, result.reason_codes())

    def test_replay_does_not_double_execute_idempotent_action(self):
        # Correct replay: the ledger marks it 'replayed' rather than re-running.
        original = _record(envelope=_env(status="succeeded", action_id="act_1"))
        replayed = _record(envelope=_env(status="replayed", action_id="act_2"))
        result = verify(original, replayed)
        self.assertTrue(result.passed())
        self.assertNotIn(REPLAY_DOUBLE_EXECUTION_RISK, result.reason_codes())

    def test_double_execution_risk_detected(self):
        # Bad replay: re-ran the side effect (fresh action_id, both succeeded).
        original = _record(envelope=_env(status="succeeded", action_id="act_1"))
        replayed = _record(envelope=_env(status="succeeded", action_id="act_2"))
        result = verify(original, replayed)
        self.assertFalse(result.passed())
        self.assertEqual(result.status, ReplayStatus.BLOCKED)
        self.assertIn(REPLAY_DOUBLE_EXECUTION_RISK, result.reason_codes())

    def test_cross_customer_scope_mismatch_fails(self):
        original = _record(customer="cust_alice")
        replayed = _record(customer="cust_bob")
        result = verify(original, replayed)
        self.assertFalse(result.passed())
        self.assertEqual(result.status, ReplayStatus.BLOCKED)
        self.assertIn(REPLAY_SCOPE_MISMATCH, result.reason_codes())


class MetricsTests(unittest.TestCase):
    def test_replay_metrics(self):
        clean = _record(envelope=_env(), tool=dict(_OK_TOOL))
        results = verify_batch(
            [
                (clean, clean),  # pass
                (_record(decision="allow"), _record(decision="escalate")),  # mismatch
                (_record(customer="cust_alice"), _record(customer="cust_bob")),  # blocked
                (None, clean),  # missing audit
            ]
        )
        m = replay_metrics(results)
        self.assertEqual(m["replay_total"], 4)
        self.assertEqual(m["replay_success_rate"], 0.25)
        self.assertEqual(m["replay_mismatch_count"], 1)
        self.assertEqual(m["replay_blocked_count"], 1)
        self.assertEqual(m["replay_missing_audit_count"], 1)


class HermesReplayFindingTests(unittest.TestCase):
    def test_hermes_surfaces_replay_mismatch_finding(self):
        original = _record(decision="allow", tool=dict(_OK_TOOL), envelope=_env())
        replayed = _record(decision="escalate", tool=dict(_OK_TOOL), envelope=_env())
        result = verify(original, replayed)

        findings = hermes.review_replay_result(result)
        self.assertTrue(findings)
        f = findings[0]
        self.assertEqual(f.finding_type, BROKER_DECISION_MISMATCH)
        self.assertEqual(f.severity, "high")
        self.assertTrue(f.human_review_required)
        self.assertTrue(f.suggested_test)
        self.assertTrue(f.suggested_policy_gap)

        # Batch helper aggregates across results.
        batch = hermes.review_replay_results([result])
        self.assertEqual(len(batch), len(findings))

    def test_hermes_replay_review_is_advisory_only(self):
        # No execute/act surface leaks into the hermes namespace via v2.4.
        forbidden = ("execute", "send", "apply", "approve", "refund", "override", "run_tool")
        names = [n.lower() for n in dir(hermes)]
        for bad in forbidden:
            self.assertFalse(any(bad in n for n in names), f"hermes exposes '{bad}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
