"""Hermes reviewer — read-only analysis of audit traces.

Hermes reads the per-turn decision trail (the audit record + its packets) and
emits advisory findings for a human developer. It is deliberately
NON-authoritative. Hermes can:

  * read audit traces, summarize failures, suggest tests, flag policy gaps.

Hermes must NOT (and this module structurally cannot):

  * send customer replies, execute tools, approve refunds, apply discounts,
    override broker decisions, or modify policy.

Every finding carries ``human_review_required=True``. The module exposes only
pure read functions returning data — there is no execute/apply/send surface.

Input is the audit record as a dict (``AuditRecord.to_dict()`` or a stored row).
Packet fields may arrive as dicts (in-memory) or JSON strings (from the durable
store); both are handled.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .models import HermesReviewPacket


def _as_dict(value: Any) -> dict[str, Any]:
    """Normalise a packet field that may be a dict or a JSON string."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def review_record(record: dict[str, Any]) -> Optional[HermesReviewPacket]:
    """Return the single most important finding for a turn, or None if clean.

    Checks run in severity order so the worst signal surfaces first.
    """
    turn_id = record.get("turn_id", "")
    route = record.get("route", "")
    handoff_reason = record.get("handoff_reason") or ""
    guardrail = _as_dict(record.get("guardrail"))
    # The in-memory record carries a `guardrail` dict (verdict + violations); a
    # durable store row carries a flat `guardrail_verdict` column instead. Accept
    # either so Hermes works against both shapes.
    guardrail_verdict = guardrail.get("verdict") or record.get("guardrail_verdict")
    broker = _as_dict(record.get("broker_decision_packet"))
    action_class = record.get("action_class", "")
    blast_radius = record.get("blast_radius", "")

    # 1. Unsafe escape (critical): a high-blast action auto-executed instead of
    #    escalating. This should never happen; surface it loudest.
    if route == "auto_action" and blast_radius == "high":
        return HermesReviewPacket(
            turn_id=turn_id,
            severity="critical",
            finding_type="unsafe_escape",
            summary=f"High-blast action '{action_class}' auto-executed without escalation.",
            suggested_test=f"Add a safety test asserting '{action_class}' always escalates.",
            suggested_policy_gap=broker.get("policy_handle", ""),
        )

    # 2. Fail-closed (high): a safety-critical layer could not render a verdict.
    if handoff_reason.startswith("fail_closed:"):
        stage = handoff_reason.split(":", 1)[1] or "unknown"
        return HermesReviewPacket(
            turn_id=turn_id,
            severity="high",
            finding_type="fail_closed",
            summary=f"Turn failed closed: safety layer '{stage}' could not render a verdict.",
            suggested_test=f"Add a test simulating a '{stage}' failure to confirm a safe handoff.",
            suggested_policy_gap="safety.fail_closed",
        )

    # 3. Guardrail block (medium): a model proposal carried an unsafe
    #    offer/price/PII/tone and was blocked before reaching the customer.
    if guardrail_verdict == "block" or handoff_reason == "guardrail_block":
        violations = guardrail.get("violations") or []
        joined = ", ".join(violations) if violations else "policy violation"
        return HermesReviewPacket(
            turn_id=turn_id,
            severity="medium",
            finding_type="guardrail_block",
            summary=f"Model proposal blocked by guardrail ({joined}).",
            suggested_test=f"Add an adversarial composer test for: {joined}.",
            suggested_policy_gap=broker.get("policy_handle", "response.guardrail.offer_pii_tone"),
        )

    # 4. Over-block (low): an informational request escalated for lack of
    #    grounding — possibly a knowledge-base coverage gap, not unsafe.
    if handoff_reason == "unverifiable":
        return HermesReviewPacket(
            turn_id=turn_id,
            severity="low",
            finding_type="over_block",
            summary="Informational request escalated because retrieval returned no grounded evidence.",
            suggested_test="Add a paraphrased FAQ test to extend grounded coverage.",
            suggested_policy_gap="faq.answer.requires_grounding",
        )

    return None


def review(records: list[dict[str, Any]]) -> list[HermesReviewPacket]:
    """Review a batch of audit records, dropping clean turns."""
    findings: list[HermesReviewPacket] = []
    for record in records:
        finding = review_record(record)
        if finding is not None:
            findings.append(finding)
    return findings
