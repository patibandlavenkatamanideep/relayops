"""The human approval queue (v2.7).

``ApprovalQueue`` holds high-risk actions for a human operator and gates their
execution. It is the enforcement point for the v2.7 invariant:

    high-risk actions require human approval before execution.

The queue is deterministic and local — an in-process store for the prototype, the
same way the idempotency ledger and audit ledger are. Time is injectable (every
method that reasons about expiry takes an optional ``now``), so tests are exact
and a replay re-derives the same states.

State machine per request::

    request_approval → PENDING            (or NOT_REQUIRED when policy allows)
    approve()        → APPROVED           (records reviewer + reason)
    reject()         → REJECTED           (records reviewer + reason)
    expire()/lapse   → EXPIRED
    authorize_execution():
        APPROVED (once) | NOT_REQUIRED → allowed
        PENDING | REJECTED | EXPIRED   → blocked (audited)

Guarantees:
  * rejected and expired actions never execute;
  * a pending action never executes until a human approves it;
  * an approved action executes at most once (single-use);
  * every state transition is recorded as an ``ApprovalAuditEvent``.

The queue records approve/reject only when driven by a human caller supplying a
reviewer identity and reason. Hermes (the operator agent) has no path here — it
reads the queue's records, it cannot call ``approve``/``reject``/``authorize``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import policy as approval_policy
from .models import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_REJECTED,
    APPROVAL_REQUESTED,
    EXECUTION_AUTHORIZED,
    EXECUTION_BLOCKED,
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
)


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def new_request_id() -> str:
    return f"apr_{uuid.uuid4().hex[:12]}"


class ApprovalError(Exception):
    """Raised on an invalid queue operation (unknown or already-decided request)."""


@dataclass
class ApprovalQueue:
    """In-memory, deterministic store of approval requests + their audit trail."""

    requests: dict[str, ApprovalRequest] = field(default_factory=dict)
    audit_events: list[ApprovalAuditEvent] = field(default_factory=list)

    # --- creation ------------------------------------------------------------

    def request_approval(
        self,
        *,
        action: str,
        target_resource: str,
        customer_id: str,
        policy_handle: str = "",
        cross_customer: bool = False,
        medium_requires_approval: bool = False,
        ttl_seconds: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> ApprovalRequest:
        """Register an action, deciding via policy whether it needs approval.

        High/critical actions land ``PENDING``; low (and medium, by default) land
        ``NOT_REQUIRED`` and may proceed. Every registration is audited.
        """
        now = now or _now_dt()
        result = approval_policy.evaluate_action(
            action,
            cross_customer=cross_customer,
            medium_requires_approval=medium_requires_approval,
        )
        expires_at = ""
        if result.approval_required and ttl_seconds is not None:
            expires_at = _iso(now + timedelta(seconds=ttl_seconds))

        request = ApprovalRequest(
            request_id=new_request_id(),
            action=action,
            target_resource=target_resource,
            customer_id=customer_id,
            risk_level=result.risk_level,
            policy_handle=policy_handle,
            status=result.status,
            rationale=result.rationale,
            created_at=_iso(now),
            expires_at=expires_at,
        )
        self.requests[request.request_id] = request
        self._record(APPROVAL_REQUESTED, request)
        return request

    # --- human decisions -----------------------------------------------------

    def approve(
        self, request_id: str, *, reviewer: str, reason: str, now: Optional[datetime] = None
    ) -> ApprovalRequest:
        """A human approves a pending request. Requires reviewer + reason."""
        request = self._pending_or_raise(request_id, now=now)
        decision = ApprovalDecision(reviewer=reviewer, approved=True, reason=reason)
        request.decision = decision
        request.status = ApprovalStatus.APPROVED
        self._record(APPROVAL_APPROVED, request, reviewer=reviewer, reason=reason)
        return request

    def reject(
        self, request_id: str, *, reviewer: str, reason: str, now: Optional[datetime] = None
    ) -> ApprovalRequest:
        """A human rejects a pending request. Requires reviewer + reason. A
        rejected action can never execute."""
        request = self._pending_or_raise(request_id, now=now)
        decision = ApprovalDecision(reviewer=reviewer, approved=False, reason=reason)
        request.decision = decision
        request.status = ApprovalStatus.REJECTED
        self._record(APPROVAL_REJECTED, request, reviewer=reviewer, reason=reason)
        return request

    def expire(self, request_id: str, *, now: Optional[datetime] = None) -> ApprovalRequest:
        """Force-expire a pending request (review window lapsed)."""
        request = self._get_or_raise(request_id)
        if request.status == ApprovalStatus.PENDING:
            request.status = ApprovalStatus.EXPIRED
            self._record(APPROVAL_EXPIRED, request)
        return request

    # --- execution gate ------------------------------------------------------

    def status_of(self, request_id: str, *, now: Optional[datetime] = None) -> str:
        """Effective status, applying lazy expiry: a pending request past its
        ``expires_at`` reads (and is recorded) as EXPIRED."""
        request = self._get_or_raise(request_id)
        self._lapse_if_expired(request, now=now)
        return request.status

    def authorize_execution(self, request_id: str, *, now: Optional[datetime] = None) -> bool:
        """Return True only if this action may execute now, and consume the
        approval (single-use) when it does.

        Blocks — and audits ``execution_blocked`` — for pending, rejected,
        expired, unknown, or already-executed requests. This is the boundary the
        tool executor consults so a high-risk action cannot run without approval.
        """
        request = self.requests.get(request_id)
        if request is None:
            self.audit_events.append(
                ApprovalAuditEvent(
                    event=EXECUTION_BLOCKED,
                    request_id=request_id,
                    action="",
                    customer_id="",
                    risk_level="",
                    status="unknown",
                    reason="no approval request found for this action",
                )
            )
            return False

        self._lapse_if_expired(request, now=now)

        if request.status not in ApprovalStatus.EXECUTABLE:
            self._record(
                EXECUTION_BLOCKED,
                request,
                reason=f"execution blocked: approval status is '{request.status}'",
            )
            return False

        if request.executed:
            self._record(
                EXECUTION_BLOCKED,
                request,
                reason="execution blocked: approval already consumed (single-use)",
            )
            return False

        request.executed = True
        self._record(EXECUTION_AUTHORIZED, request)
        return True

    # --- read-only views -----------------------------------------------------

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        return self.requests.get(request_id)

    def pending(self, *, now: Optional[datetime] = None) -> list[ApprovalRequest]:
        return [
            r
            for r in self.requests.values()
            if self.status_of(r.request_id, now=now) == ApprovalStatus.PENDING
        ]

    def as_dicts(self) -> list[dict]:
        return [r.to_dict() for r in self.requests.values()]

    def audit_as_dicts(self) -> list[dict]:
        return [e.to_dict() for e in self.audit_events]

    def reset(self) -> None:
        self.requests.clear()
        self.audit_events.clear()

    # --- internals -----------------------------------------------------------

    def _get_or_raise(self, request_id: str) -> ApprovalRequest:
        request = self.requests.get(request_id)
        if request is None:
            raise ApprovalError(f"unknown approval request: {request_id}")
        return request

    def _pending_or_raise(self, request_id: str, *, now: Optional[datetime]) -> ApprovalRequest:
        request = self._get_or_raise(request_id)
        self._lapse_if_expired(request, now=now)
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalError(
                f"request {request_id} is '{request.status}', not pending; cannot decide again"
            )
        return request

    def _lapse_if_expired(self, request: ApprovalRequest, *, now: Optional[datetime]) -> None:
        if request.status != ApprovalStatus.PENDING or not request.expires_at:
            return
        now = now or _now_dt()
        if now >= datetime.fromisoformat(request.expires_at):
            request.status = ApprovalStatus.EXPIRED
            self._record(APPROVAL_EXPIRED, request)

    def _record(
        self,
        event: str,
        request: ApprovalRequest,
        *,
        reviewer: str = "",
        reason: str = "",
    ) -> None:
        self.audit_events.append(
            ApprovalAuditEvent(
                event=event,
                request_id=request.request_id,
                action=request.action,
                customer_id=request.customer_id,
                risk_level=request.risk_level,
                status=request.status,
                reviewer=reviewer,
                reason=reason,
            )
        )


# Process-default queue. Swap or reset it in tests for isolation.
default_queue = ApprovalQueue()
