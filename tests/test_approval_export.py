"""Approval evidence export tests (v2.8).

Covers the read-only approval export: that it surfaces pending/approved/rejected/
expired records with risk/status/reviewer/reason, renders serializable JSON and
Markdown, includes the per-request audit-event history, reflects the execution
gate (pending/rejected/expired shown blocked; an approved single-use action shown
allowed then consumed), and — critically — never mutates the queue it reads.
"""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from src.approval import (
    EXEC_ALLOWED,
    EXEC_BLOCKED,
    EXEC_CONSUMED,
    ApprovalQueue,
    ApprovalStatus,
    build_approval_export,
)
from src.approval.models import APPROVAL_REQUESTED

_T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _seed_queue() -> ApprovalQueue:
    """A queue with one request in each interesting state."""
    q = ApprovalQueue()
    q.request_approval(
        action="refund",
        target_resource="inv_1",
        customer_id="cust_alice",
        requester_id="agent_bot",
        action_id="act_abc123",
        now=_T0,
    )
    approved = q.request_approval(
        action="billing_adjustment",
        target_resource="inv_2",
        customer_id="cust_bob",
        now=_T0,
    )
    q.approve(approved.request_id, reviewer="ops_jordan", reason="verified charge", now=_T0)

    rejected = q.request_approval(
        action="account_cancellation",
        target_resource="acct_3",
        customer_id="cust_carol",
        now=_T0,
    )
    q.reject(rejected.request_id, reviewer="ops_lee", reason="not authorized", now=_T0)

    expired = q.request_approval(
        action="plan_change",
        target_resource="acct_4",
        customer_id="cust_dan",
        ttl_seconds=60,
        now=_T0,
    )
    q.expire(expired.request_id, now=_T0)

    q.request_approval(
        action="device_reset",
        target_resource="dev_5",
        customer_id="cust_erin",
        now=_T0,
    )
    return q, approved


class ApprovalExportSectionTests(unittest.TestCase):
    def setUp(self):
        self.q, self.approved = _seed_queue()
        self.export = build_approval_export(self.q, now=_T0 + timedelta(minutes=5))

    def test_exports_pending_records(self):
        self.assertEqual(len(self.export.pending), 1)
        self.assertEqual(self.export.pending[0].action, "refund")

    def test_exports_approved_records(self):
        self.assertEqual(len(self.export.approved), 1)
        self.assertEqual(self.export.approved[0].reviewer_id, "ops_jordan")

    def test_exports_rejected_records(self):
        self.assertEqual(len(self.export.rejected), 1)
        self.assertEqual(self.export.rejected[0].action, "account_cancellation")

    def test_exports_expired_records(self):
        self.assertEqual(len(self.export.expired), 1)
        self.assertEqual(self.export.expired[0].action, "plan_change")

    def test_pending_high_risk_shown_blocked(self):
        self.assertTrue(self.export.pending[0].blocked)
        self.assertEqual(self.export.pending[0].execution, EXEC_BLOCKED)

    def test_rejected_shown_blocked(self):
        self.assertTrue(self.export.rejected[0].blocked)
        self.assertEqual(self.export.rejected[0].execution, EXEC_BLOCKED)

    def test_expired_shown_blocked(self):
        self.assertTrue(self.export.expired[0].blocked)
        self.assertEqual(self.export.expired[0].execution, EXEC_BLOCKED)

    def test_record_carries_action_and_requester_ids(self):
        pending = self.export.pending[0]
        self.assertEqual(pending.action_id, "act_abc123")
        self.assertEqual(pending.requester_id, "agent_bot")
        self.assertEqual(pending.customer_id, "cust_alice")

    def test_export_includes_audit_events(self):
        pending = self.export.pending[0]
        events = [e["event"] for e in pending.audit_events]
        self.assertIn(APPROVAL_REQUESTED, events)
        # Every listed event belongs to that request only.
        self.assertTrue(all(e["request_id"] == pending.approval_id for e in pending.audit_events))


class ApprovalExecutionStateTests(unittest.TestCase):
    def test_approved_single_use_allowed_then_consumed(self):
        q, approved = _seed_queue()
        before = build_approval_export(q, now=_T0)
        self.assertEqual(before.approved[0].execution, EXEC_ALLOWED)
        self.assertFalse(before.approved[0].blocked)

        # Consume the single-use approval, then re-export.
        self.assertTrue(q.authorize_execution(approved.request_id, now=_T0))
        after = build_approval_export(q, now=_T0)
        self.assertEqual(after.approved[0].execution, EXEC_CONSUMED)
        # Consumed is not "allowed" any more, but it is not a policy block either.
        self.assertFalse(after.approved[0].blocked)

    def test_not_required_is_allowed(self):
        q, _ = _seed_queue()
        export = build_approval_export(q, now=_T0)
        self.assertEqual(export.not_required[0].execution, EXEC_ALLOWED)
        self.assertFalse(export.not_required[0].blocked)


class ApprovalExportRenderTests(unittest.TestCase):
    def setUp(self):
        self.q, _ = _seed_queue()
        self.export = build_approval_export(self.q, now=_T0)

    def test_json_export_is_serializable(self):
        payload = json.dumps(self.export.to_dict())
        restored = json.loads(payload)
        self.assertEqual(restored["total"], 5)
        self.assertIn("pending", restored)
        self.assertIn("audit_events", restored["records"][0])

    def test_markdown_includes_risk_status_reviewer_reason(self):
        md = self.export.to_markdown()
        self.assertIn("# RelayOps approval audit export", md)
        self.assertIn("ops_jordan", md)  # reviewer
        self.assertIn("verified charge", md)  # reason
        self.assertIn("critical", md)  # risk level (account_cancellation)
        self.assertIn("rejected", md)  # status
        self.assertIn("audit trail", md)

    def test_counts_by_status(self):
        counts = self.export.counts_by_status
        self.assertEqual(counts[ApprovalStatus.PENDING], 1)
        self.assertEqual(counts[ApprovalStatus.APPROVED], 1)
        self.assertEqual(counts[ApprovalStatus.REJECTED], 1)
        self.assertEqual(counts[ApprovalStatus.EXPIRED], 1)
        self.assertEqual(self.export.blocked_count, 3)  # pending + rejected + expired


class ApprovalExportPurityTests(unittest.TestCase):
    def test_export_does_not_mutate_queue_state(self):
        q, _ = _seed_queue()
        requests_before = copy.deepcopy(q.as_dicts())
        events_before = copy.deepcopy(q.audit_as_dicts())

        # Exporting well past every TTL must not transition or audit anything.
        build_approval_export(q, now=_T0 + timedelta(days=1))
        build_approval_export(q, now=_T0 + timedelta(days=1))

        self.assertEqual(q.as_dicts(), requests_before)
        self.assertEqual(q.audit_as_dicts(), events_before)

    def test_expired_by_ttl_shown_read_only(self):
        """A pending request past its TTL reads as expired in the export without
        the export writing an expiry event to the queue."""
        q = ApprovalQueue()
        req = q.request_approval(
            action="refund", target_resource="i1", customer_id="c1", ttl_seconds=60, now=_T0
        )
        events_before = len(q.audit_events)
        export = build_approval_export(q, now=_T0 + timedelta(seconds=120))
        self.assertEqual(export.records[0].status, ApprovalStatus.EXPIRED)
        self.assertTrue(export.records[0].blocked)
        # The stored request is untouched (still pending) and no event was added.
        self.assertEqual(q.get(req.request_id).status, ApprovalStatus.PENDING)
        self.assertEqual(len(q.audit_events), events_before)


if __name__ == "__main__":
    unittest.main()
