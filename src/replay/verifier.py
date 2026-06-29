"""Replay verifier (v2.4) — deterministic comparison of two audited flows.

``verify(original, replayed)`` takes two audit-record dicts (``AuditRecord.to_dict()``
or durable store rows) and returns a ``ReplayVerificationResult``. It compares the
broker decision, the action envelope, the tool response, customer/caller scope,
and audit completeness, and screens for double-execution risk.

Read-only and side-effect free: it inspects recorded evidence, it does not re-run
the pipeline, call a tool, or touch policy. Packet fields may arrive as dicts
(in-memory) or JSON strings (durable rows); both are handled.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .models import (
    ACTION_ENVELOPE_MISMATCH,
    BROKER_DECISION_MISMATCH,
    MISSING_ORIGINAL_AUDIT_RECORD,
    MISSING_REPLAY_AUDIT_RECORD,
    REPLAY_DOUBLE_EXECUTION_RISK,
    REPLAY_SCOPE_MISMATCH,
    SAFETY_BLOCKING,
    TOOL_RESPONSE_MISMATCH,
    ReplayComparison,
    ReplayMismatch,
    ReplayStatus,
    ReplayVerificationResult,
)

# Broker fields that must stay stable across a replay (policy drift detector).
_BROKER_FIELDS = ("decision", "policy_handle", "matched_rule", "reason_code")
# Envelope *identity* fields — deliberately excludes action_id, status,
# timestamps, and result, which legitimately differ between an original
# execution and its replay.
_ENVELOPE_IDENTITY_FIELDS = (
    "action",
    "target_resource",
    "policy_handle",
    "idempotency_key",
    "blast_radius",
    "reversibility",
)
_TOOL_FIELDS = ("name", "ok", "error")
_SUCCEEDED = "succeeded"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return value if isinstance(value, list) else []


def _is_missing(record: Optional[dict[str, Any]]) -> bool:
    return not isinstance(record, dict) or not record


def _first_envelope(record: dict[str, Any]) -> dict[str, Any]:
    envelopes = _as_list(record.get("action_envelopes"))
    return _as_dict(envelopes[0]) if envelopes else {}


def verify(
    original: Optional[dict[str, Any]],
    replayed: Optional[dict[str, Any]],
) -> ReplayVerificationResult:
    """Verify a replayed flow against its original; return evidence + verdict."""
    # Missing-record checks come first: with no pair to compare, the result is a
    # MISSING_AUDIT verdict carrying the appropriate reason code.
    if _is_missing(original) or _is_missing(replayed):
        turn_id = ""
        for rec in (replayed, original):
            if isinstance(rec, dict):
                turn_id = rec.get("turn_id", "") or turn_id
        mismatches = []
        if _is_missing(original):
            mismatches.append(
                ReplayMismatch(
                    reason_code=MISSING_ORIGINAL_AUDIT_RECORD,
                    field="original",
                    original=None,
                    replayed="present" if not _is_missing(replayed) else None,
                    detail="No original audit record to verify the replay against.",
                )
            )
        if _is_missing(replayed):
            mismatches.append(
                ReplayMismatch(
                    reason_code=MISSING_REPLAY_AUDIT_RECORD,
                    field="replay",
                    original="present" if not _is_missing(original) else None,
                    replayed=None,
                    detail="No replay audit record was produced.",
                )
            )
        return ReplayVerificationResult(
            turn_id=turn_id, status=ReplayStatus.MISSING_AUDIT, mismatches=mismatches
        )

    turn_id = replayed.get("turn_id") or original.get("turn_id") or ""
    mismatches: list[ReplayMismatch] = []
    comparisons: list[ReplayComparison] = []

    _compare_broker(original, replayed, mismatches, comparisons)
    _compare_scope(original, replayed, mismatches, comparisons)
    _compare_envelope(original, replayed, mismatches, comparisons)
    _compare_tool(original, replayed, mismatches, comparisons)

    status = _status_from(mismatches)
    return ReplayVerificationResult(
        turn_id=turn_id, status=status, mismatches=mismatches, comparisons=comparisons
    )


def _status_from(mismatches: list[ReplayMismatch]) -> str:
    if not mismatches:
        return ReplayStatus.PASS
    if any(m.reason_code in SAFETY_BLOCKING for m in mismatches):
        return ReplayStatus.BLOCKED
    return ReplayStatus.MISMATCH


def _compare_broker(original, replayed, mismatches, comparisons) -> None:
    ob = _as_dict(original.get("broker_decision_packet"))
    rb = _as_dict(replayed.get("broker_decision_packet"))
    matched = True
    for field_name in _BROKER_FIELDS:
        ov, rv = ob.get(field_name), rb.get(field_name)
        if ov != rv:
            matched = False
            mismatches.append(
                ReplayMismatch(
                    reason_code=BROKER_DECISION_MISMATCH,
                    field=f"broker_decision_packet.{field_name}",
                    original=ov,
                    replayed=rv,
                    detail="Broker decision drifted between original and replay.",
                )
            )
    comparisons.append(
        ReplayComparison(aspect="broker_decision", matched=matched, original=ob, replayed=rb)
    )


def _compare_scope(original, replayed, mismatches, comparisons) -> None:
    oc = original.get("customer_id")
    rc = replayed.get("customer_id")
    matched = oc == rc
    if not matched:
        mismatches.append(
            ReplayMismatch(
                reason_code=REPLAY_SCOPE_MISMATCH,
                field="customer_id",
                original=oc,
                replayed=rc,
                detail="Replay ran under a different customer/caller scope.",
            )
        )
    comparisons.append(ReplayComparison(aspect="scope", matched=matched, original=oc, replayed=rc))


def _compare_envelope(original, replayed, mismatches, comparisons) -> None:
    oe = _first_envelope(original)
    re_ = _first_envelope(replayed)

    # Presence must agree: an action in one flow but not the other is drift.
    if bool(oe) != bool(re_):
        mismatches.append(
            ReplayMismatch(
                reason_code=ACTION_ENVELOPE_MISMATCH,
                field="action_envelopes",
                original="present" if oe else "absent",
                replayed="present" if re_ else "absent",
                detail="An action envelope exists in only one of the two flows.",
            )
        )
        comparisons.append(
            ReplayComparison(aspect="action_envelope", matched=False, original=oe, replayed=re_)
        )
        return

    if not oe and not re_:
        # No side-effecting action in either flow — nothing to compare.
        comparisons.append(ReplayComparison(aspect="action_envelope", matched=True))
        return

    matched = True
    for field_name in _ENVELOPE_IDENTITY_FIELDS:
        ov, rv = oe.get(field_name), re_.get(field_name)
        if ov != rv:
            matched = False
            mismatches.append(
                ReplayMismatch(
                    reason_code=ACTION_ENVELOPE_MISMATCH,
                    field=f"action_envelope.{field_name}",
                    original=ov,
                    replayed=rv,
                    detail="Action envelope identity drifted between original and replay.",
                )
            )
    comparisons.append(
        ReplayComparison(aspect="action_envelope", matched=matched, original=oe, replayed=re_)
    )

    _check_double_execution(oe, re_, mismatches, comparisons)


def _check_double_execution(oe, re_, mismatches, comparisons) -> None:
    """A correct replay of an idempotent action is recorded as 'replayed' (the
    ledger prevented re-execution). Double-execution risk is a replay that ran
    the side effect AGAIN: same idempotency key, both envelopes 'succeeded', but
    a *new* action id (a genuinely fresh execution, not the same record)."""
    same_key = oe.get("idempotency_key") and oe.get("idempotency_key") == re_.get("idempotency_key")
    both_succeeded = oe.get("status") == _SUCCEEDED and re_.get("status") == _SUCCEEDED
    fresh_execution = oe.get("action_id") != re_.get("action_id")
    risk = bool(same_key and both_succeeded and fresh_execution)
    if risk:
        mismatches.append(
            ReplayMismatch(
                reason_code=REPLAY_DOUBLE_EXECUTION_RISK,
                field="action_envelope.status",
                original=oe.get("action_id"),
                replayed=re_.get("action_id"),
                detail="Replay re-executed an idempotent action instead of replaying it.",
            )
        )
    comparisons.append(ReplayComparison(aspect="idempotency", matched=not risk))


def _compare_tool(original, replayed, mismatches, comparisons) -> None:
    ot = _as_dict(original.get("tool_call"))
    rt = _as_dict(replayed.get("tool_call"))
    matched = True
    for field_name in _TOOL_FIELDS:
        ov, rv = ot.get(field_name), rt.get(field_name)
        if ov != rv:
            matched = False
            mismatches.append(
                ReplayMismatch(
                    reason_code=TOOL_RESPONSE_MISMATCH,
                    field=f"tool_call.{field_name}",
                    original=ov,
                    replayed=rv,
                    detail="Tool response drifted between original and replay.",
                )
            )
    comparisons.append(
        ReplayComparison(aspect="tool_response", matched=matched, original=ot, replayed=rt)
    )


def verify_batch(
    pairs: list[tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]],
) -> list[ReplayVerificationResult]:
    """Verify many (original, replayed) record pairs."""
    return [verify(original, replayed) for original, replayed in pairs]


def replay_metrics(results: list[ReplayVerificationResult]) -> dict[str, Any]:
    """Aggregate replay results into the v2.4 metric set."""
    total = len(results)
    passed = sum(1 for r in results if r.status == ReplayStatus.PASS)
    mismatch = sum(1 for r in results if r.status == ReplayStatus.MISMATCH)
    blocked = sum(1 for r in results if r.status == ReplayStatus.BLOCKED)
    missing = sum(1 for r in results if r.status == ReplayStatus.MISSING_AUDIT)
    return {
        "replay_total": total,
        "replay_success_rate": round(passed / total, 4) if total else 0.0,
        "replay_mismatch_count": mismatch,
        "replay_missing_audit_count": missing,
        "replay_blocked_count": blocked,
    }
