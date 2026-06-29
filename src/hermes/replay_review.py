"""Hermes replay review (v2.4) — surface replay mismatches as advisory findings.

Bridges the replay verifier into the Hermes finding taxonomy: each
``ReplayMismatch`` becomes a read-only ``HermesReviewPacket`` carrying its
severity, reason code (as the finding type), a suggested test, and a suggested
policy gap. Like the rest of Hermes this is advisory only — it reads replay
evidence and flags it for a human; it never re-runs a replay or acts.
"""

from __future__ import annotations

from ..replay.models import ReplayVerificationResult
from .models import HermesReviewPacket

# reason_code -> (suggested_test, suggested_policy_gap). Deterministic guidance a
# human developer can act on; Hermes does not change anything itself.
_GUIDANCE: dict[str, tuple[str, str]] = {
    "broker_decision_mismatch": (
        "Add a replay test asserting the broker decision is stable for this turn.",
        "policy.replay.decision_must_be_stable",
    ),
    "action_envelope_mismatch": (
        "Add a replay test asserting the action envelope identity is unchanged.",
        "policy.replay.envelope_must_match",
    ),
    "tool_response_mismatch": (
        "Add a replay test pinning the scoped tool response for this action.",
        "policy.replay.tool_response_must_match",
    ),
    "missing_original_audit_record": (
        "Add a test asserting every replayed turn has an original audit record.",
        "audit.completeness.original_required",
    ),
    "missing_replay_audit_record": (
        "Add a test asserting every replay writes its own audit record.",
        "audit.completeness.replay_required",
    ),
    "replay_double_execution_risk": (
        "Add a replay test asserting an idempotent action is replayed, not re-run.",
        "safety.replay.no_double_execution",
    ),
    "replay_scope_mismatch": (
        "Add a replay test asserting the customer/caller scope is unchanged.",
        "safety.replay.scope_must_match",
    ),
}


def review_replay_result(result: ReplayVerificationResult) -> list[HermesReviewPacket]:
    """Advisory findings for a single replay verification result."""
    packets: list[HermesReviewPacket] = []
    for mismatch in result.mismatches:
        suggested_test, suggested_policy_gap = _GUIDANCE.get(mismatch.reason_code, ("", ""))
        packets.append(
            HermesReviewPacket(
                turn_id=result.turn_id,
                severity=mismatch.severity,
                finding_type=mismatch.reason_code,
                summary=(
                    f"Replay {mismatch.reason_code} on '{mismatch.field}': "
                    f"original={mismatch.original!r}, replayed={mismatch.replayed!r}."
                ),
                suggested_test=suggested_test,
                suggested_policy_gap=suggested_policy_gap,
            )
        )
    return packets


def review_replay_results(results: list[ReplayVerificationResult]) -> list[HermesReviewPacket]:
    """Advisory findings across a batch of replay verification results."""
    findings: list[HermesReviewPacket] = []
    for result in results:
        findings.extend(review_replay_result(result))
    return findings
