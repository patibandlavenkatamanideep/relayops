"""The action envelope (v2.2).

``ActionEnvelope`` is the record that wraps a single side-effecting action: what
action, on what resource, for which customer, under which policy handle, with an
idempotency key and a lifecycle status. It is the unit an external action
executor (or message bus) would carry, and the unit the audit trail records.

Statuses move PENDING -> SUCCEEDED | FAILED | REFUSED for a fresh run; a
duplicate request whose idempotency key already succeeded is returned as
REPLAYED (the tool is not run again).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

PENDING = "pending"
SUCCEEDED = "succeeded"
FAILED = "failed"  # the tool ran and reported a failure (e.g. not_found)
REFUSED = "refused"  # the scoped tool refused (e.g. scope_violation / not_authorized)
REPLAYED = "replayed"  # idempotent hit — a prior success was returned, not re-run

# Tool errors that mean "the server refused", not "the action failed". They map
# to REFUSED so an operator can tell a permission/scope denial from a fault.
_REFUSAL_ERRORS = frozenset({"not_authorized", "scope_violation"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_action_id() -> str:
    return f"act_{uuid.uuid4().hex[:12]}"


def idempotency_key(customer_id: str, action: str, target_resource: str) -> str:
    """Stable key for an action: the SAME logical action (same customer, action,
    and target) shares a key across turns, so a retry is recognised. Note the
    turn id is intentionally excluded — that is what makes replay possible."""
    return f"{customer_id}:{action}:{target_resource}"


@dataclass
class ActionEnvelope:
    action_id: str
    turn_id: str
    customer_id: str
    action: str
    target_resource: str
    idempotency_key: str
    policy_handle: str
    blast_radius: str
    reversibility: str
    status: str = PENDING
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_now)
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def opened(
        cls,
        *,
        turn_id: str,
        customer_id: str,
        action: str,
        target_resource: str,
        policy_handle: str,
        blast_radius: str,
        reversibility: str,
    ) -> ActionEnvelope:
        """Open a PENDING envelope for an action about to run."""
        return cls(
            action_id=new_action_id(),
            turn_id=turn_id,
            customer_id=customer_id,
            action=action,
            target_resource=target_resource,
            idempotency_key=idempotency_key(customer_id, action, target_resource),
            policy_handle=policy_handle,
            blast_radius=blast_radius,
            reversibility=reversibility,
        )

    def succeed(self, result: dict[str, Any]) -> None:
        self.status = SUCCEEDED
        self.result = dict(result)
        self.completed_at = _now()

    def fail(self, error: str) -> None:
        self.status = REFUSED if error in _REFUSAL_ERRORS else FAILED
        self.error = error
        self.completed_at = _now()
