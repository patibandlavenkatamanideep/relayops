"""Approval evidence export (v2.8) — operator-facing, read-only.

Turns an ``ApprovalQueue`` into exportable review evidence: for every held action
it records the approval id, the linked action id, the risk level, the current
status, who requested it, the customer scope, the reviewer + reason (once a human
decides), the created/updated timestamps, the per-request audit-event history, and
— the operator's bottom line — whether execution is currently **allowed**,
**blocked**, or **consumed** (an approved single-use action that has already run).

It renders to a JSON-compatible dict and to Markdown, grouped by status
(pending / approved / rejected / expired / not-required).

Everything here is a pure read over the queue. It computes an *effective* status
(a pending request past its expiry reads as expired) WITHOUT mutating the queue:
no request is transitioned, no audit event is appended, nothing is executed. The
export is evidence for a human; it is not a decision surface. Hermes may reference
this evidence, but only the queue's own (human-driven) methods change state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .models import ApprovalRequest, ApprovalStatus
from .queue import ApprovalQueue

# Execution-readiness labels (the operator's bottom line per request).
EXEC_ALLOWED = "allowed"  # may execute now (not-required, or approved & unused)
EXEC_BLOCKED = "blocked"  # must not execute (pending / rejected / expired)
EXEC_CONSUMED = "consumed"  # approved single-use action already executed

# Status display order for the grouped export/report.
_STATUS_ORDER = (
    ApprovalStatus.PENDING,
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED,
    ApprovalStatus.NOT_REQUIRED,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _effective_status(request: ApprovalRequest, now: Optional[datetime]) -> str:
    """Read-only effective status: a pending request past ``expires_at`` reads as
    expired, without mutating the request or recording an audit event."""
    if request.status != ApprovalStatus.PENDING or not request.expires_at:
        return request.status
    now = now or datetime.now(timezone.utc)
    if now >= datetime.fromisoformat(request.expires_at):
        return ApprovalStatus.EXPIRED
    return request.status


def _execution_state(request: ApprovalRequest, effective_status: str) -> str:
    if effective_status == ApprovalStatus.NOT_REQUIRED:
        return EXEC_ALLOWED
    if effective_status == ApprovalStatus.APPROVED:
        return EXEC_CONSUMED if request.executed else EXEC_ALLOWED
    return EXEC_BLOCKED


@dataclass
class ApprovalRecordView:
    """One row of the approval export: a held action and its review evidence."""

    approval_id: str
    action_id: str
    action: str
    target_resource: str
    risk_level: str
    status: str  # effective status (expiry applied read-only)
    requester_id: str
    customer_id: str
    reviewer_id: str
    reason: str
    created_at: str
    updated_at: str
    execution: str  # allowed / blocked / consumed
    blocked: bool
    audit_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalAuditExport:
    """The full read-only approval-evidence export, grouped by status."""

    generated_at: str
    total: int
    counts_by_status: dict[str, int]
    blocked_count: int
    allowed_count: int
    records: list[ApprovalRecordView] = field(default_factory=list)

    def by_status(self, status: str) -> list[ApprovalRecordView]:
        return [r for r in self.records if r.status == status]

    @property
    def pending(self) -> list[ApprovalRecordView]:
        return self.by_status(ApprovalStatus.PENDING)

    @property
    def approved(self) -> list[ApprovalRecordView]:
        return self.by_status(ApprovalStatus.APPROVED)

    @property
    def rejected(self) -> list[ApprovalRecordView]:
        return self.by_status(ApprovalStatus.REJECTED)

    @property
    def expired(self) -> list[ApprovalRecordView]:
        return self.by_status(ApprovalStatus.EXPIRED)

    @property
    def not_required(self) -> list[ApprovalRecordView]:
        return self.by_status(ApprovalStatus.NOT_REQUIRED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "total": self.total,
            "counts_by_status": self.counts_by_status,
            "blocked_count": self.blocked_count,
            "allowed_count": self.allowed_count,
            "pending": [r.to_dict() for r in self.pending],
            "approved": [r.to_dict() for r in self.approved],
            "rejected": [r.to_dict() for r in self.rejected],
            "expired": [r.to_dict() for r in self.expired],
            "not_required": [r.to_dict() for r in self.not_required],
            "records": [r.to_dict() for r in self.records],
        }

    def to_markdown(self) -> str:
        return _render_markdown(self)


def _events_for(queue: ApprovalQueue, request_id: str) -> list[dict[str, Any]]:
    return [e.to_dict() for e in queue.audit_events if e.request_id == request_id]


def _updated_at(request: ApprovalRequest, events: list[dict[str, Any]]) -> str:
    """The most recent audit-event timestamp for this request, or its created_at."""
    timestamps = [e["timestamp"] for e in events if e.get("timestamp")]
    return max(timestamps) if timestamps else request.created_at


def _record_view(
    queue: ApprovalQueue, request: ApprovalRequest, now: Optional[datetime]
) -> ApprovalRecordView:
    effective = _effective_status(request, now)
    events = _events_for(queue, request.request_id)
    execution = _execution_state(request, effective)
    decision = request.decision
    return ApprovalRecordView(
        approval_id=request.request_id,
        action_id=request.action_id,
        action=request.action,
        target_resource=request.target_resource,
        risk_level=request.risk_level,
        status=effective,
        requester_id=request.requester_id,
        customer_id=request.customer_id,
        reviewer_id=decision.reviewer if decision else "",
        reason=decision.reason if decision else "",
        created_at=request.created_at,
        updated_at=_updated_at(request, events),
        execution=execution,
        blocked=execution == EXEC_BLOCKED,
        audit_events=events,
    )


def build_approval_export(
    queue: ApprovalQueue, *, now: Optional[datetime] = None
) -> ApprovalAuditExport:
    """Build the read-only approval-evidence export from a queue. Pure: it reads
    the queue's requests and audit events and returns a snapshot; it never
    transitions a request, records an event, or executes anything."""
    views = [_record_view(queue, req, now) for req in queue.requests.values()]
    # Stable order: by status group, then by creation time, then approval id.
    order = {status: i for i, status in enumerate(_STATUS_ORDER)}
    views.sort(key=lambda v: (order.get(v.status, len(order)), v.created_at, v.approval_id))

    counts = Counter(v.status for v in views)
    blocked = sum(1 for v in views if v.blocked)
    return ApprovalAuditExport(
        generated_at=now.isoformat() if now else _now_iso(),
        total=len(views),
        counts_by_status={s: counts.get(s, 0) for s in _STATUS_ORDER if counts.get(s, 0)},
        blocked_count=blocked,
        allowed_count=len(views) - blocked,
        records=views,
    )


# --- markdown rendering --------------------------------------------------------

_SECTION_TITLES = {
    ApprovalStatus.PENDING: "Pending approvals",
    ApprovalStatus.APPROVED: "Approved actions",
    ApprovalStatus.REJECTED: "Rejected actions",
    ApprovalStatus.EXPIRED: "Expired actions",
    ApprovalStatus.NOT_REQUIRED: "No approval required",
}


def _render_row(view: ApprovalRecordView) -> str:
    reviewer = view.reviewer_id or "—"
    reason = view.reason or "—"
    return (
        f"| {view.approval_id} | {view.action} | {view.risk_level} | {view.status} "
        f"| {view.requester_id or '—'} | {view.customer_id or '—'} | {reviewer} "
        f"| {reason} | {view.execution} |"
    )


def _render_markdown(export: ApprovalAuditExport) -> str:
    lines = [
        "# RelayOps approval audit export",
        "",
        "_Read-only operator evidence for the human approval queue. High-risk "
        "actions are held for human review before execution; a rejected or expired "
        "action can never execute and an approved action executes once. No external "
        "call is made and this export mutates nothing — a human remains accountable._",
        "",
        f"_Generated: {export.generated_at}_",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total approvals | {export.total} |",
        f"| Blocked from executing | {export.blocked_count} |",
        f"| Allowed to execute | {export.allowed_count} |",
    ]
    for status in _STATUS_ORDER:
        count = export.counts_by_status.get(status, 0)
        if count:
            lines.append(f"| {status} | {count} |")
    lines.append("")

    for status in _STATUS_ORDER:
        views = export.by_status(status)
        if not views:
            continue
        lines += [f"## {_SECTION_TITLES[status]} ({len(views)})", ""]
        lines += [
            "| Approval | Action | Risk | Status | Requester | Customer | Reviewer | Reason | Execution |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        lines += [_render_row(v) for v in views]
        lines.append("")
        # Audit trail per request, so the export is self-contained evidence.
        for v in views:
            if not v.audit_events:
                continue
            trail = ", ".join(f"{e['event']}@{e['timestamp']}" for e in v.audit_events)
            lines.append(f"- `{v.approval_id}` audit trail: {trail}")
        lines.append("")

    lines += [
        "---",
        "",
        "_Hermes may reference pending/rejected/expired approvals as advisory "
        "findings. It cannot approve, reject, execute, or mutate any approval "
        "record; every decision is made by a named human operator._",
    ]
    return "\n".join(lines)
