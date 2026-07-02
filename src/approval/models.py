"""Human approval queue data types (v2.7).

RelayOps holds high-risk actions for a human operator before they execute. This
module defines the records that make that hold *auditable and deterministic*:

  * ``ApprovalStatus``      — lifecycle of a held action (pending/approved/…).
  * ``ApprovalRiskLevel``   — how risky the proposed action is (low → critical).
  * ``ApprovalRequest``     — one action held for review, with its decision.
  * ``ApprovalDecision``    — a reviewer's approve/reject, with identity + reason.
  * ``ApprovalPolicyResult``— the deterministic "does this need approval?" verdict.
  * ``ApprovalAuditEvent``  — one row of the approval decision trail.

These are plain dataclasses. The queue (``queue.py``) and policy (``policy.py``)
operate on them; the model proposes, the broker decides, the *human* approves —
this module only records that chain, it never executes anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalStatus:
    """Lifecycle of an action that may need human approval."""

    PENDING = "pending"  # awaiting a human decision — must NOT execute yet
    APPROVED = "approved"  # a human approved it — may execute once
    REJECTED = "rejected"  # a human rejected it — must NEVER execute
    EXPIRED = "expired"  # the review window lapsed — must NEVER execute
    NOT_REQUIRED = "not_required"  # policy did not require approval — may proceed

    # Statuses under which an action is allowed to execute. Everything else
    # (pending / rejected / expired) blocks execution.
    EXECUTABLE = frozenset({APPROVED, NOT_REQUIRED})


class ApprovalRiskLevel:
    """How risky the proposed action is, least → most severe."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    ORDER = (LOW, MEDIUM, HIGH, CRITICAL)

    @classmethod
    def rank(cls, level: str) -> int:
        """Ordinal position of a risk level (unknown levels sort highest, so an
        unrecognised level is treated as at least as risky as ``critical``)."""
        try:
            return cls.ORDER.index(level)
        except ValueError:
            return len(cls.ORDER)


# Approval risk level -> operator-triage severity (matches Hermes' SEVERITIES).
SEVERITY_BY_RISK = {
    ApprovalRiskLevel.LOW: "low",
    ApprovalRiskLevel.MEDIUM: "medium",
    ApprovalRiskLevel.HIGH: "high",
    ApprovalRiskLevel.CRITICAL: "critical",
}


# Approval audit event names (stable strings for tests/dashboards).
APPROVAL_REQUESTED = "approval_requested"
APPROVAL_APPROVED = "approval_approved"
APPROVAL_REJECTED = "approval_rejected"
APPROVAL_EXPIRED = "approval_expired"
EXECUTION_BLOCKED = "execution_blocked"
EXECUTION_AUTHORIZED = "execution_authorized"


@dataclass
class ApprovalDecision:
    """A human reviewer's ruling on a held action. A decision is only valid if it
    carries *who* decided and *why* — accountability, not an anonymous flag."""

    reviewer: str
    approved: bool
    reason: str
    decided_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not str(self.reviewer).strip():
            raise ValueError("approval decision requires a reviewer identity")
        if not str(self.reason).strip():
            raise ValueError("approval decision requires a reason")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRequest:
    """One action held for human review. ``status`` is the source of truth for
    whether the action may execute; ``decision`` records the human ruling once
    made. ``executed`` enforces single-use: an approved action proceeds once."""

    request_id: str
    action: str
    target_resource: str
    customer_id: str
    risk_level: str
    policy_handle: str
    status: str
    action_id: str = ""  # links to the action envelope this hold guards, if any
    requester_id: str = ""  # who/what requested the action (caller / operator / system)
    rationale: str = ""  # why approval was (or was not) required
    created_at: str = field(default_factory=_now)
    expires_at: str = ""  # ISO timestamp; empty means no expiry
    decision: Optional[ApprovalDecision] = None
    executed: bool = False

    def requires_approval(self) -> bool:
        return self.status != ApprovalStatus.NOT_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.to_dict() if self.decision else None
        return data


@dataclass
class ApprovalPolicyResult:
    """The deterministic verdict for "does this action need human approval?"."""

    risk_level: str
    approval_required: bool
    status: str  # PENDING when approval is required, NOT_REQUIRED otherwise
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalAuditEvent:
    """One row of the approval decision trail. Recorded on the queue, separate
    from the main audit ledger so approval events never mutate unrelated
    action/audit records."""

    event: str
    request_id: str
    action: str
    customer_id: str
    risk_level: str
    status: str
    reviewer: str = ""
    reason: str = ""
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
