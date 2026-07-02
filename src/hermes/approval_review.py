"""Hermes approval review (v2.7) — surface approval-queue state as findings.

Bridges the human approval queue into the Hermes finding taxonomy. Hermes reads
the queue's requests and flags the ones a human should look at:

  * a high/critical action still ``PENDING`` — waiting on a human decision;
  * a ``REJECTED`` action — recorded so the operator sees what was refused;
  * an ``EXPIRED`` action — a review window lapsed without a decision.

Like the rest of Hermes this is advisory and structurally read-only: it takes
approval records (``ApprovalRequest`` or their dicts) and returns
``HermesReviewPacket``s. It has NO path to approve, reject, or execute anything —
it cannot import or call the queue's decision methods; it only reports.
"""

from __future__ import annotations

from typing import Any, Union

from ..approval.models import (
    SEVERITY_BY_RISK,
    ApprovalRequest,
    ApprovalRiskLevel,
    ApprovalStatus,
)
from .models import HermesReviewPacket

# The high-risk levels whose pending state Hermes escalates loudest.
_HIGH_RISK = frozenset({ApprovalRiskLevel.HIGH, ApprovalRiskLevel.CRITICAL})


def _as_request_view(record: Union[ApprovalRequest, dict[str, Any]]) -> dict[str, Any]:
    """Normalise a request that may be an ``ApprovalRequest`` or a stored dict."""
    if isinstance(record, ApprovalRequest):
        return record.to_dict()
    return record if isinstance(record, dict) else {}


def review_approval_request(
    record: Union[ApprovalRequest, dict[str, Any]],
) -> Union[HermesReviewPacket, None]:
    """Return an advisory finding for one approval request, or None if clean.

    Clean = the action did not require approval, or it was approved (a human
    already ruled). Pending/rejected/expired states are surfaced for review.
    """
    view = _as_request_view(record)
    status = view.get("status", "")
    risk = view.get("risk_level", "")
    action = view.get("action", "")
    turn_id = view.get("request_id", "")
    severity = SEVERITY_BY_RISK.get(risk, "medium")

    if status == ApprovalStatus.PENDING:
        # A pending high/critical action is the headline: a sensitive operation
        # is being held and needs a human decision before it can execute.
        if risk in _HIGH_RISK:
            return HermesReviewPacket(
                turn_id=turn_id,
                severity=severity,
                finding_type="approval_pending",
                summary=(
                    f"High-risk action '{action}' ({risk}) is pending human approval "
                    f"and is blocked from executing until a reviewer decides."
                ),
                suggested_test=(
                    f"Add a test asserting '{action}' cannot execute while approval is pending."
                ),
                suggested_policy_gap="approval.high_risk.requires_human",
            )
        return HermesReviewPacket(
            turn_id=turn_id,
            severity=severity,
            finding_type="approval_pending",
            summary=f"Action '{action}' ({risk}) is pending human approval.",
            suggested_test=f"Add a test asserting '{action}' waits for approval before executing.",
            suggested_policy_gap="approval.pending.requires_human",
        )

    if status == ApprovalStatus.REJECTED:
        return HermesReviewPacket(
            turn_id=turn_id,
            severity=severity,
            finding_type="approval_rejected",
            summary=f"Action '{action}' ({risk}) was rejected by a reviewer and must not execute.",
            suggested_test=f"Add a test asserting a rejected '{action}' can never execute.",
            suggested_policy_gap="approval.rejected.never_executes",
        )

    if status == ApprovalStatus.EXPIRED:
        return HermesReviewPacket(
            turn_id=turn_id,
            severity=severity,
            finding_type="approval_expired",
            summary=f"Approval for action '{action}' ({risk}) expired without a decision.",
            suggested_test=f"Add a test asserting an expired '{action}' can never execute.",
            suggested_policy_gap="approval.expired.never_executes",
        )

    return None


def review_approval_requests(
    records: list[Union[ApprovalRequest, dict[str, Any]]],
) -> list[HermesReviewPacket]:
    """Advisory findings across a batch of approval requests, dropping clean ones.

    Findings are ordered most-urgent first so the worst signal surfaces at the top.
    """
    findings: list[HermesReviewPacket] = []
    for record in records:
        finding = review_approval_request(record)
        if finding is not None:
            findings.append(finding)
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda p: severity_rank.get(p.severity, 4))
    return findings
