"""Action taxonomy and policy engine.

The v1 router (``router.py``) decides *disposition* (respond / escalate). This
module adds the missing layer Reddit reviewers asked for: a declarative
**action policy** that classifies every supported action by

  * blast radius   — how much damage a wrong call does (low / high / unknown)
  * reversibility  — can the action be cleanly undone? (yes / partial / no)
  * evidence_needed — what must be true before we are allowed to act
  * route          — auto_action / respond / escalate

Keeping this as a *table* (not buried ``if/else``) is the point: the router stops
looking like ad-hoc control flow and starts looking like a policy engine whose
safety properties can be read off data. The same table also supplies the
**owner team** and **SLA** that make an escalation handoff usable by the next
human (see ``build_handoff``).

Nothing here changes a routing decision on its own — ``router.py`` remains the
decision-maker. This module is the *taxonomy and handoff* layer the pipeline and
the audit ledger read from, so design and execution stay one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..core.models import Intent


class ActionClass(str, Enum):
    """What the customer is actually asking the system to do."""

    DEVICE_RESET = "device_reset"
    SEND_TROUBLESHOOTING_LINK = "send_troubleshooting_link"
    ACCOUNT_READ = "account_read"          # read-only status lookup
    BILLING_REFUND = "billing_refund"
    PLAN_CHANGE = "plan_change"
    ACCOUNT_ACCESS_CHANGE = "account_access_change"
    UNKNOWN = "unknown"


class BlastRadius(str, Enum):
    LOW = "low"
    HIGH = "high"
    UNKNOWN = "unknown"


class Reversibility(str, Enum):
    REVERSIBLE = "yes"
    PARTIAL = "partial"
    IRREVERSIBLE = "no"
    UNKNOWN = "unknown"


class PolicyRoute(str, Enum):
    AUTO_ACTION = "auto_action"
    RESPOND = "respond"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class ActionPolicy:
    action: ActionClass
    blast_radius: BlastRadius
    reversibility: Reversibility
    evidence_needed: str
    route: PolicyRoute
    owner: str           # team that owns the action when a human is needed
    sla: str             # human-facing next-step / deadline


# The policy table. Reading top to bottom: only low-blast, reversible actions
# with cheap evidence are eligible to auto-run; anything money-touching,
# identity-touching, or unknown is escalated with an owner + SLA attached.
POLICY_TABLE: dict[ActionClass, ActionPolicy] = {
    ActionClass.DEVICE_RESET: ActionPolicy(
        ActionClass.DEVICE_RESET,
        BlastRadius.LOW,
        Reversibility.REVERSIBLE,
        evidence_needed="authenticated device ownership",
        route=PolicyRoute.AUTO_ACTION,
        owner="device_support",
        sla="immediate (automated)",
    ),
    ActionClass.SEND_TROUBLESHOOTING_LINK: ActionPolicy(
        ActionClass.SEND_TROUBLESHOOTING_LINK,
        BlastRadius.LOW,
        Reversibility.REVERSIBLE,
        evidence_needed="relevant FAQ match",
        route=PolicyRoute.RESPOND,
        owner="device_support",
        sla="immediate (automated)",
    ),
    ActionClass.ACCOUNT_READ: ActionPolicy(
        ActionClass.ACCOUNT_READ,
        BlastRadius.LOW,
        Reversibility.REVERSIBLE,
        evidence_needed="authenticated account scope",
        route=PolicyRoute.RESPOND,
        owner="device_support",
        sla="immediate (automated)",
    ),
    ActionClass.BILLING_REFUND: ActionPolicy(
        ActionClass.BILLING_REFUND,
        BlastRadius.HIGH,
        Reversibility.PARTIAL,
        evidence_needed="human review of charge history",
        route=PolicyRoute.ESCALATE,
        owner="billing_support",
        sla="next available billing specialist",
    ),
    ActionClass.PLAN_CHANGE: ActionPolicy(
        ActionClass.PLAN_CHANGE,
        BlastRadius.HIGH,
        Reversibility.PARTIAL,
        evidence_needed="human review of plan terms",
        route=PolicyRoute.ESCALATE,
        owner="billing_support",
        sla="next available billing specialist",
    ),
    ActionClass.ACCOUNT_ACCESS_CHANGE: ActionPolicy(
        ActionClass.ACCOUNT_ACCESS_CHANGE,
        BlastRadius.HIGH,
        Reversibility.IRREVERSIBLE,
        evidence_needed="human review + identity verification",
        route=PolicyRoute.ESCALATE,
        owner="identity_security",
        sla="next available security specialist",
    ),
    ActionClass.UNKNOWN: ActionPolicy(
        ActionClass.UNKNOWN,
        BlastRadius.UNKNOWN,
        Reversibility.UNKNOWN,
        evidence_needed="insufficient — needs a human",
        route=PolicyRoute.ESCALATE,
        owner="general_support",
        sla="next available agent",
    ),
}


def policy_for(action: ActionClass) -> ActionPolicy:
    return POLICY_TABLE[action]


# --- mapping pipeline signals onto the taxonomy --------------------------------

# Intent -> the action class it represents on the happy path.
_INTENT_ACTION: dict[Intent, ActionClass] = {
    Intent.RESET_DEVICE: ActionClass.DEVICE_RESET,
    Intent.DEVICE_FAQ: ActionClass.SEND_TROUBLESHOOTING_LINK,
    Intent.DEVICE_STATUS: ActionClass.ACCOUNT_READ,
    Intent.BILLING: ActionClass.BILLING_REFUND,
}


def action_for_intent(intent: Intent) -> ActionClass:
    return _INTENT_ACTION.get(intent, ActionClass.UNKNOWN)


def classify_action(intent: Intent, reason: str = "") -> ActionClass:
    """Resolve the action class for a turn, refining by escalation reason.

    The reason carries information the bare intent doesn't (e.g. a scope or
    auth failure is an access-change risk, not a device reset), so a blocked
    turn is taxonomised by *why* it was blocked, not just what was asked.
    """

    r = reason.lower()
    if "scope_violation" in r or "unauthenticated" in r:
        return ActionClass.ACCOUNT_ACCESS_CHANGE
    # The router's money bucket is the generic "billing/plan/payment"; treat it
    # as a refund (the common money-touching case). A bare "plan" reason without
    # the billing bucket is a plan change. Both share owner + blast radius, so
    # this only refines the audit label, never the safety decision.
    if "billing" in r or "refund" in r or "payment" in r or intent == Intent.BILLING:
        return ActionClass.BILLING_REFUND
    if "plan" in r:
        return ActionClass.PLAN_CHANGE
    if "guardrail" in r or "low_confidence" in r or "unverifiable" in r:
        return ActionClass.UNKNOWN
    return action_for_intent(intent)


# --- handoff completeness ------------------------------------------------------

# The fields a handoff must carry so the next human can continue WITHOUT
# rereading the whole chat. Safe abandonment = a block that strands the
# customer; a complete handoff is the cure.
REQUIRED_HANDOFF_FIELDS: tuple[str, ...] = (
    "blocked_action",
    "reason_blocked",
    "customer_promise",
    "owner",
    "evidence_quote",
    "channel",
    "deadline",
)


def build_handoff(
    *,
    intent: Intent,
    reason: str,
    customer_message: str,
    confidence: float,
    extra: Optional[dict] = None,
) -> dict:
    """Assemble a complete, owner-routed handoff for an escalated turn.

    Backward-compatible: it keeps the original ``reason`` / ``intent`` /
    ``confidence`` / ``message`` keys the pipeline and tests already read, and
    adds the structured fields that make the handoff usable downstream.
    """

    action = classify_action(intent, reason)
    policy = policy_for(action)
    handoff = {
        # legacy keys (kept stable for existing callers/tests)
        "reason": reason,
        "intent": intent.value,
        "confidence": round(confidence, 2),
        "message": customer_message,
        # structured handoff (v1.3)
        "blocked_action": action.value,
        "reason_blocked": _reason_blocked(action, reason),
        "customer_promise": _customer_promise(policy),
        "owner": policy.owner,
        "evidence_quote": customer_message,
        "channel": "human_handoff",
        "deadline": policy.sla,
        "blast_radius": policy.blast_radius.value,
        "reversible": policy.reversibility.value,
    }
    if extra:
        handoff.update(extra)
    return handoff


def handoff_completeness(handoff: dict) -> tuple[bool, list[str]]:
    """Return (complete?, missing_fields). A field counts as present only if it
    carries a non-empty value — an empty owner is the same as no owner."""

    missing = [
        field
        for field in REQUIRED_HANDOFF_FIELDS
        if not str(handoff.get(field, "")).strip()
    ]
    return (not missing, missing)


def _reason_blocked(action: ActionClass, reason: str) -> str:
    policy = policy_for(action)
    if action in (ActionClass.BILLING_REFUND, ActionClass.PLAN_CHANGE):
        return "money-touching action requires human review"
    if action == ActionClass.ACCOUNT_ACCESS_CHANGE:
        return "account/identity change requires verification and human review"
    if "guardrail" in reason.lower():
        return "candidate reply failed the guardrail (possible invented offer/price)"
    if "unverifiable" in reason.lower():
        return "no grounded knowledge to answer safely"
    if "low_confidence" in reason.lower():
        return "intent could not be classified with enough confidence to act"
    return f"{policy.evidence_needed} not satisfied"


def _customer_promise(policy: ActionPolicy) -> str:
    team = policy.owner.replace("_", " ")
    article = "An" if team[:1].lower() in "aeiou" else "A"
    return f"{article} {team} specialist will review this and follow up — {policy.sla}."
