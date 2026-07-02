"""Human approval queue tests (v2.7).

Covers the deterministic approval policy (which risk levels require approval),
the queue state machine (pending/approved/rejected/expired), the execution gate
(rejected/expired/pending never execute; an approved action executes once), the
approval audit trail, replay-stability of the approval requirement, and that
Hermes surfaces pending/rejected approval findings while remaining structurally
unable to approve, reject, or execute anything.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src import hermes
from src.actions import REFUSED, SUCCEEDED, IdempotencyLedger, execute_action
from src.approval import (
    ApprovalError,
    ApprovalQueue,
    ApprovalRiskLevel,
    ApprovalStatus,
    evaluate,
    evaluate_action,
    risk_for_action,
)
from src.approval.models import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_REJECTED,
    APPROVAL_REQUESTED,
    EXECUTION_BLOCKED,
    ApprovalDecision,
)
from src.core.models import AccessContext, ToolResult

_T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ok_tool() -> ToolResult:
    return ToolResult(ok=True, data={"done": True})


class ApprovalPolicyTests(unittest.TestCase):
    def test_low_risk_action_does_not_require_approval(self):
        result = evaluate(ApprovalRiskLevel.LOW)
        self.assertFalse(result.approval_required)
        self.assertEqual(result.status, ApprovalStatus.NOT_REQUIRED)

    def test_high_risk_action_requires_approval(self):
        result = evaluate_action("refund")
        self.assertEqual(result.risk_level, ApprovalRiskLevel.HIGH)
        self.assertTrue(result.approval_required)
        self.assertEqual(result.status, ApprovalStatus.PENDING)

    def test_critical_action_requires_approval(self):
        result = evaluate_action("account_cancellation")
        self.assertEqual(result.risk_level, ApprovalRiskLevel.CRITICAL)
        self.assertTrue(result.approval_required)

    def test_medium_is_configurable(self):
        self.assertFalse(evaluate(ApprovalRiskLevel.MEDIUM).approval_required)
        self.assertTrue(
            evaluate(ApprovalRiskLevel.MEDIUM, medium_requires_approval=True).approval_required
        )

    def test_cross_customer_is_always_critical(self):
        risk = risk_for_action("device_reset", cross_customer=True)
        self.assertEqual(risk, ApprovalRiskLevel.CRITICAL)


class ApprovalQueueTests(unittest.TestCase):
    def setUp(self):
        self.q = ApprovalQueue()

    def _refund(self, **kw):
        return self.q.request_approval(
            action="refund",
            target_resource="inv_123",
            customer_id="cust_alice",
            policy_handle="billing.refund.requires_human",
            now=_T0,
            **kw,
        )

    def test_low_risk_does_not_require_approval_and_can_proceed(self):
        req = self.q.request_approval(
            action="device_reset",
            target_resource="dev_a1",
            customer_id="cust_alice",
            now=_T0,
        )
        self.assertEqual(req.status, ApprovalStatus.NOT_REQUIRED)
        self.assertFalse(req.requires_approval())
        self.assertTrue(self.q.authorize_execution(req.request_id))

    def test_high_risk_action_requires_approval(self):
        req = self._refund()
        self.assertEqual(req.status, ApprovalStatus.PENDING)
        self.assertTrue(req.requires_approval())

    def test_pending_approval_blocks_execution(self):
        req = self._refund()
        self.assertFalse(self.q.authorize_execution(req.request_id, now=_T0))

    def test_rejected_approval_blocks_execution(self):
        req = self._refund()
        self.q.reject(req.request_id, reviewer="ops_jordan", reason="not a valid refund")
        self.assertEqual(self.q.get(req.request_id).status, ApprovalStatus.REJECTED)
        self.assertFalse(self.q.authorize_execution(req.request_id))

    def test_approved_action_can_proceed_once(self):
        req = self._refund()
        self.q.approve(req.request_id, reviewer="ops_jordan", reason="verified charge history")
        self.assertEqual(self.q.get(req.request_id).status, ApprovalStatus.APPROVED)
        # First execution is authorized; a second is blocked (single-use).
        self.assertTrue(self.q.authorize_execution(req.request_id))
        self.assertFalse(self.q.authorize_execution(req.request_id))

    def test_expired_approval_blocks_execution(self):
        req = self._refund(ttl_seconds=60)
        later = _T0 + timedelta(seconds=120)
        self.assertEqual(self.q.status_of(req.request_id, now=later), ApprovalStatus.EXPIRED)
        self.assertFalse(self.q.authorize_execution(req.request_id, now=later))

    def test_force_expire_then_blocks(self):
        req = self._refund()
        self.q.expire(req.request_id)
        self.assertEqual(self.q.get(req.request_id).status, ApprovalStatus.EXPIRED)
        self.assertFalse(self.q.authorize_execution(req.request_id))

    def test_cannot_approve_expired_request(self):
        req = self._refund(ttl_seconds=60)
        later = _T0 + timedelta(seconds=120)
        with self.assertRaises(ApprovalError):
            self.q.approve(req.request_id, reviewer="ops", reason="too late", now=later)

    def test_cannot_decide_twice(self):
        req = self._refund()
        self.q.approve(req.request_id, reviewer="ops", reason="ok")
        with self.assertRaises(ApprovalError):
            self.q.reject(req.request_id, reviewer="ops", reason="changed mind")

    def test_authorize_unknown_request_is_blocked(self):
        self.assertFalse(self.q.authorize_execution("apr_does_not_exist"))


class ApprovalDecisionValidationTests(unittest.TestCase):
    def test_decision_requires_reviewer(self):
        with self.assertRaises(ValueError):
            ApprovalDecision(reviewer="", approved=True, reason="ok")

    def test_decision_requires_reason(self):
        with self.assertRaises(ValueError):
            ApprovalDecision(reviewer="ops", approved=True, reason="   ")

    def test_queue_approve_requires_reviewer_and_reason(self):
        q = ApprovalQueue()
        req = q.request_approval(action="refund", target_resource="i1", customer_id="c1", now=_T0)
        with self.assertRaises(ValueError):
            q.approve(req.request_id, reviewer="", reason="ok")
        with self.assertRaises(ValueError):
            q.reject(req.request_id, reviewer="ops", reason="")


class ApprovalAuditTests(unittest.TestCase):
    def test_approval_state_is_auditable(self):
        q = ApprovalQueue()
        req = q.request_approval(action="refund", target_resource="i1", customer_id="c1", now=_T0)
        q.reject(req.request_id, reviewer="ops_jordan", reason="fraud signal")
        q.authorize_execution(req.request_id)  # blocked, audited

        events = [e.event for e in q.audit_events]
        self.assertIn(APPROVAL_REQUESTED, events)
        self.assertIn(APPROVAL_REJECTED, events)
        self.assertIn(EXECUTION_BLOCKED, events)

        rejected = next(e for e in q.audit_events if e.event == APPROVAL_REJECTED)
        self.assertEqual(rejected.reviewer, "ops_jordan")
        self.assertEqual(rejected.reason, "fraud signal")

    def test_approve_and_expire_are_audited(self):
        q = ApprovalQueue()
        req = q.request_approval(action="refund", target_resource="i1", customer_id="c1", now=_T0)
        q.approve(req.request_id, reviewer="ops", reason="verified")
        self.assertIn(APPROVAL_APPROVED, [e.event for e in q.audit_events])

        req2 = q.request_approval(
            action="refund", target_resource="i2", customer_id="c1", ttl_seconds=1, now=_T0
        )
        q.status_of(req2.request_id, now=_T0 + timedelta(seconds=5))
        self.assertIn(APPROVAL_EXPIRED, [e.event for e in q.audit_events])

    def test_no_mutation_of_unrelated_records(self):
        """Deciding one request must not alter any other request's fields."""
        q = ApprovalQueue()
        a = q.request_approval(action="refund", target_resource="i1", customer_id="c1", now=_T0)
        b = q.request_approval(
            action="account_cancellation", target_resource="acct_2", customer_id="c2", now=_T0
        )
        before = q.get(b.request_id).to_dict()
        q.approve(a.request_id, reviewer="ops", reason="ok")
        q.authorize_execution(a.request_id)
        after = q.get(b.request_id).to_dict()
        self.assertEqual(before, after)


class ReplayApprovalStabilityTests(unittest.TestCase):
    def test_replay_preserves_approval_requirement(self):
        """Re-deriving the policy for the same action yields the same requirement:
        a replayed flow cannot silently drop the human-approval gate."""
        original = evaluate_action("refund")
        replayed = evaluate_action("refund")
        self.assertEqual(original.to_dict(), replayed.to_dict())
        self.assertTrue(replayed.approval_required)

        # And a serialized request round-trips its requirement unchanged.
        q = ApprovalQueue()
        req = q.request_approval(action="refund", target_resource="i1", customer_id="c1", now=_T0)
        snapshot = req.to_dict()
        self.assertEqual(snapshot["status"], ApprovalStatus.PENDING)
        self.assertEqual(risk_for_action(snapshot["action"]), ApprovalRiskLevel.HIGH)


class ExecutorApprovalGateTests(unittest.TestCase):
    """The tool executor boundary refuses a high-risk action without approval."""

    def _run(self, gate):
        ledger = IdempotencyLedger()
        ctx = AccessContext(customer_id="cust_alice", authenticated=True)
        return execute_action(
            ctx,
            turn_id="t1",
            action="refund",
            target_resource="inv_1",
            policy_handle="billing.refund.requires_human",
            blast_radius="high",
            reversibility="partial",
            tool=_ok_tool,
            ledger=ledger,
            approval_gate=gate,
        )

    def test_execution_blocked_without_approval(self):
        q = ApprovalQueue()
        req = q.request_approval(
            action="refund", target_resource="inv_1", customer_id="cust_alice", now=_T0
        )
        env, result = self._run(lambda: q.authorize_execution(req.request_id))
        self.assertFalse(result.ok)
        self.assertEqual(env.status, REFUSED)
        self.assertEqual(env.error, "approval_required")

    def test_execution_proceeds_after_approval(self):
        q = ApprovalQueue()
        req = q.request_approval(
            action="refund", target_resource="inv_1", customer_id="cust_alice", now=_T0
        )
        q.approve(req.request_id, reviewer="ops", reason="verified")
        env, result = self._run(lambda: q.authorize_execution(req.request_id))
        self.assertTrue(result.ok)
        self.assertEqual(env.status, SUCCEEDED)


class HermesApprovalReviewTests(unittest.TestCase):
    def test_hermes_surfaces_pending_and_rejected(self):
        q = ApprovalQueue()
        pending = q.request_approval(
            action="refund", target_resource="i1", customer_id="c1", now=_T0
        )
        rejected = q.request_approval(
            action="account_cancellation", target_resource="a2", customer_id="c2", now=_T0
        )
        q.reject(rejected.request_id, reviewer="ops", reason="not authorized")
        clean = q.request_approval(
            action="device_reset", target_resource="d3", customer_id="c3", now=_T0
        )

        findings = hermes.review_approval_requests(list(q.requests.values()))
        types = {f.finding_type for f in findings}
        self.assertIn("approval_pending", types)
        self.assertIn("approval_rejected", types)
        # A not_required (clean) action produces no finding.
        self.assertNotIn(clean.request_id, [f.turn_id for f in findings])
        self.assertNotIn(pending.request_id, [])  # sanity
        # Every finding is advisory: a human must review.
        self.assertTrue(all(f.human_review_required for f in findings))

    def test_hermes_cannot_approve_reject_or_execute(self):
        """Structural guarantee: the Hermes surface exposes no mutation path."""
        import src.hermes.approval_review as ar

        names = dir(ar)
        for forbidden in ("approve", "reject", "authorize_execution", "execute"):
            self.assertNotIn(forbidden, names)
        # The module never imports the queue (it only reads request records).
        self.assertFalse(hasattr(ar, "ApprovalQueue"))

    def test_no_external_calls_or_credentials(self):
        """The approval stack is pure/local: no network/credential imports."""
        import src.approval.policy as pol
        import src.approval.queue as que

        for mod in (pol, que):
            for banned in ("requests", "httpx", "urllib3", "openai", "boto3", "os"):
                self.assertFalse(
                    hasattr(mod, banned),
                    f"{mod.__name__} unexpectedly references {banned}",
                )


if __name__ == "__main__":
    unittest.main()
