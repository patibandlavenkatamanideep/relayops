"""Deterministic approval policy (v2.7).

Given a proposed action's risk level, decide whether it must be held for human
approval. The rule is a small, readable table — the same safety property the rest
of RelayOps favours (policy as data, not buried ``if/else``):

  * low       — scoped, reversible; may proceed without approval.
  * medium    — configurable; defaults to NOT requiring approval, but an operator
                may raise the bar (``medium_requires_approval=True``).
  * high      — money/identity/scope-touching; requires approval.
  * critical  — irreversible or cross-customer; requires approval.

Also classifies example high-risk actions (refund, cancellation, billing
adjustment, contract change, outbound sensitive message, cross-customer action)
onto risk levels. This module is pure and deterministic: the same inputs always
yield the same verdict, so a replay re-derives an identical approval requirement.
"""

from __future__ import annotations

from .models import ApprovalPolicyResult, ApprovalRiskLevel, ApprovalStatus

# Example high-risk action names -> risk level. These are the sensitive
# operations RelayOps holds for human review; the taxonomy mirrors the action
# policy's money-/identity-touching bucket.
HIGH_RISK_ACTIONS: dict[str, str] = {
    "refund": ApprovalRiskLevel.HIGH,
    "credit": ApprovalRiskLevel.HIGH,
    "billing_adjustment": ApprovalRiskLevel.HIGH,
    "plan_change": ApprovalRiskLevel.HIGH,
    "outbound_sensitive_message": ApprovalRiskLevel.HIGH,
    "account_cancellation": ApprovalRiskLevel.CRITICAL,
    "contract_modification": ApprovalRiskLevel.CRITICAL,
    "account_access_change": ApprovalRiskLevel.CRITICAL,
    "cross_customer_action": ApprovalRiskLevel.CRITICAL,
}

# Low-risk, scoped, reversible actions that may proceed without approval.
LOW_RISK_ACTIONS: frozenset[str] = frozenset(
    {
        "device_reset",
        "send_troubleshooting_link",
        "account_read",
    }
)


def risk_for_action(action: str, *, cross_customer: bool = False) -> str:
    """Deterministic risk level for a named action.

    ``cross_customer=True`` forces CRITICAL: acting outside the caller's own
    customer scope is always the most sensitive case, regardless of the action.
    """
    if cross_customer:
        return ApprovalRiskLevel.CRITICAL
    if action in HIGH_RISK_ACTIONS:
        return HIGH_RISK_ACTIONS[action]
    if action in LOW_RISK_ACTIONS:
        return ApprovalRiskLevel.LOW
    # Unknown actions are treated as medium — not auto-blocked, but eligible to
    # require approval when the operator raises the bar.
    return ApprovalRiskLevel.MEDIUM


def evaluate(risk_level: str, *, medium_requires_approval: bool = False) -> ApprovalPolicyResult:
    """Decide whether an action at ``risk_level`` must be held for approval."""
    if risk_level in (ApprovalRiskLevel.HIGH, ApprovalRiskLevel.CRITICAL):
        return ApprovalPolicyResult(
            risk_level=risk_level,
            approval_required=True,
            status=ApprovalStatus.PENDING,
            rationale=f"{risk_level}-risk action requires human approval before execution",
        )
    if risk_level == ApprovalRiskLevel.MEDIUM and medium_requires_approval:
        return ApprovalPolicyResult(
            risk_level=risk_level,
            approval_required=True,
            status=ApprovalStatus.PENDING,
            rationale="medium-risk action requires approval under current operator policy",
        )
    return ApprovalPolicyResult(
        risk_level=risk_level,
        approval_required=False,
        status=ApprovalStatus.NOT_REQUIRED,
        rationale=f"{risk_level}-risk action may proceed without human approval",
    )


def evaluate_action(
    action: str,
    *,
    cross_customer: bool = False,
    medium_requires_approval: bool = False,
) -> ApprovalPolicyResult:
    """Convenience: classify ``action`` then evaluate the approval requirement."""
    risk = risk_for_action(action, cross_customer=cross_customer)
    return evaluate(risk, medium_requires_approval=medium_requires_approval)
