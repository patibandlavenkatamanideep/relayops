"""Human approval queue (v2.7).

RelayOps holds high-risk actions for a human operator before they execute. The
model proposes, the broker decides, the action envelope wraps, the tool server
executes only scoped requests — and, for sensitive operations, a *human* must
approve before execution. This package adds that step:

  * ``policy``  — deterministic "does this action need approval?" table.
  * ``models``  — the request/decision/status/risk records.
  * ``queue``   — the ``ApprovalQueue`` that holds actions and gates execution.

Rejected and expired actions never execute; a pending action waits for a human;
an approved action executes once. Every transition is auditable. Nothing here
calls a vendor, moves money, or executes an action — it only decides whether an
action is *allowed* to proceed.
"""

from __future__ import annotations

from .models import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_REJECTED,
    APPROVAL_REQUESTED,
    EXECUTION_AUTHORIZED,
    EXECUTION_BLOCKED,
    SEVERITY_BY_RISK,
    ApprovalAuditEvent,
    ApprovalDecision,
    ApprovalPolicyResult,
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalStatus,
)
from .policy import (
    HIGH_RISK_ACTIONS,
    LOW_RISK_ACTIONS,
    evaluate,
    evaluate_action,
    risk_for_action,
)
from .queue import ApprovalError, ApprovalQueue, default_queue, new_request_id

__all__ = [
    # models
    "ApprovalStatus",
    "ApprovalRiskLevel",
    "ApprovalRequest",
    "ApprovalDecision",
    "ApprovalPolicyResult",
    "ApprovalAuditEvent",
    "SEVERITY_BY_RISK",
    "APPROVAL_REQUESTED",
    "APPROVAL_APPROVED",
    "APPROVAL_REJECTED",
    "APPROVAL_EXPIRED",
    "EXECUTION_BLOCKED",
    "EXECUTION_AUTHORIZED",
    # policy
    "HIGH_RISK_ACTIONS",
    "LOW_RISK_ACTIONS",
    "risk_for_action",
    "evaluate",
    "evaluate_action",
    # queue
    "ApprovalQueue",
    "ApprovalError",
    "default_queue",
    "new_request_id",
]
