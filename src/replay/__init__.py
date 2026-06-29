"""Replay verification (v2.4).

Re-checks a prior audited action flow against a replayed flow and reports
mismatches: broker-decision drift, action-envelope drift, tool-response drift,
missing audit records, customer/caller scope drift, and double-execution risk.

Deterministic and read-only: the verifier compares two recorded flows and
returns evidence. It never re-executes a tool, calls the model, or changes
policy — the broker remains the decision authority and Hermes stays advisory.
"""

from __future__ import annotations

from .models import (
    ACTION_ENVELOPE_MISMATCH,
    BROKER_DECISION_MISMATCH,
    MISSING_ORIGINAL_AUDIT_RECORD,
    MISSING_REPLAY_AUDIT_RECORD,
    REPLAY_DOUBLE_EXECUTION_RISK,
    REPLAY_SCOPE_MISMATCH,
    TOOL_RESPONSE_MISMATCH,
    ReplayComparison,
    ReplayMismatch,
    ReplayStatus,
    ReplayVerificationResult,
    severity_for,
)
from .verifier import replay_metrics, verify, verify_batch

__all__ = [
    "ReplayStatus",
    "ReplayMismatch",
    "ReplayComparison",
    "ReplayVerificationResult",
    "severity_for",
    "verify",
    "verify_batch",
    "replay_metrics",
    "BROKER_DECISION_MISMATCH",
    "ACTION_ENVELOPE_MISMATCH",
    "TOOL_RESPONSE_MISMATCH",
    "MISSING_ORIGINAL_AUDIT_RECORD",
    "MISSING_REPLAY_AUDIT_RECORD",
    "REPLAY_DOUBLE_EXECUTION_RISK",
    "REPLAY_SCOPE_MISMATCH",
]
