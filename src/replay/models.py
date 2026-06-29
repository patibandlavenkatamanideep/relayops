"""Replay verification data types (v2.4).

``ReplayVerificationResult`` is the outcome of comparing one original audited
flow against its replay. It carries the per-aspect ``ReplayComparison`` evidence
and any ``ReplayMismatch`` records, each tagged with a deterministic reason code
and an operator-triage severity. Plain dataclasses — replay is advisory evidence,
not an action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ReplayStatus:
    """Overall outcome of verifying one replayed flow against its original."""

    PASS = "pass"
    MISMATCH = "mismatch"  # drift detected, not safety-blocking on its own
    BLOCKED = "blocked"  # safety-critical mismatch; the replay must not proceed
    MISSING_AUDIT = "missing_audit"  # an original or replay audit record is absent


# Deterministic mismatch reason codes (stable strings for tests/dashboards).
BROKER_DECISION_MISMATCH = "broker_decision_mismatch"
ACTION_ENVELOPE_MISMATCH = "action_envelope_mismatch"
TOOL_RESPONSE_MISMATCH = "tool_response_mismatch"
MISSING_ORIGINAL_AUDIT_RECORD = "missing_original_audit_record"
MISSING_REPLAY_AUDIT_RECORD = "missing_replay_audit_record"
REPLAY_DOUBLE_EXECUTION_RISK = "replay_double_execution_risk"
REPLAY_SCOPE_MISMATCH = "replay_scope_mismatch"

# A scope mismatch or a double-execution is safety-critical: the replay must be
# blocked, not merely flagged as drift.
SAFETY_BLOCKING = frozenset({REPLAY_SCOPE_MISMATCH, REPLAY_DOUBLE_EXECUTION_RISK})

# reason_code -> operator-triage severity (matches Hermes' SEVERITIES scale).
_SEVERITY = {
    BROKER_DECISION_MISMATCH: "high",
    ACTION_ENVELOPE_MISMATCH: "high",
    TOOL_RESPONSE_MISMATCH: "medium",
    MISSING_ORIGINAL_AUDIT_RECORD: "high",
    MISSING_REPLAY_AUDIT_RECORD: "high",
    REPLAY_DOUBLE_EXECUTION_RISK: "critical",
    REPLAY_SCOPE_MISMATCH: "critical",
}


def severity_for(reason_code: str) -> str:
    return _SEVERITY.get(reason_code, "medium")


@dataclass
class ReplayMismatch:
    """One detected discrepancy between the original and replayed flow."""

    reason_code: str
    field: str
    original: Any
    replayed: Any
    severity: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.severity:
            self.severity = severity_for(self.reason_code)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayComparison:
    """Evidence that one aspect (broker / envelope / tool / scope / audit) was
    compared, and whether the two flows matched on it."""

    aspect: str
    matched: bool
    original: Any = None
    replayed: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayVerificationResult:
    """The verdict for one replayed flow. ``passed`` is True only on a clean
    PASS; any mismatch yields MISMATCH or (for safety-critical) BLOCKED."""

    turn_id: str
    status: str
    mismatches: list[ReplayMismatch] = field(default_factory=list)
    comparisons: list[ReplayComparison] = field(default_factory=list)

    def passed(self) -> bool:
        return self.status == ReplayStatus.PASS

    def reason_codes(self) -> list[str]:
        return [m.reason_code for m in self.mismatches]

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "status": self.status,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "comparisons": [c.to_dict() for c in self.comparisons],
        }
