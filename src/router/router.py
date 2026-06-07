"""Tiered model router.

Maps (intent, confidence) -> a routing decision: which tier handles the turn,
whether a tool runs, and whether we escalate to a human. Cost follows difficulty.

v1 policy:
  * BILLING / plan / payment            -> always escalate (touches money)
  * low confidence (< threshold)        -> escalate (agent unsure)
  * RESET_DEVICE (confident)            -> Tier 2 + device_reset tool (an action)
  * DEVICE_STATUS (confident)           -> Tier 1 + account_lookup
  * GREETING (confident)                -> Tier 1, no tool
  * UNKNOWN                             -> escalate
"""

from __future__ import annotations

from ..core.models import Action, Classification, Disposition, Intent, RouteDecision, Tier

CONFIDENCE_THRESHOLD = 0.55


def route(c: Classification) -> RouteDecision:
    # Money-touching intents are gated regardless of confidence.
    if c.intent == Intent.BILLING:
        return RouteDecision(Tier.NONE, Disposition.ESCALATE, reason="billing/plan/payment")

    if c.confidence < CONFIDENCE_THRESHOLD or c.intent == Intent.UNKNOWN:
        return RouteDecision(Tier.NONE, Disposition.ESCALATE, reason="low_confidence")

    if c.intent == Intent.RESET_DEVICE:
        # Taking a reversible action -> frontier tier handles tool use.
        return RouteDecision(
            Tier.TIER2, Disposition.RESPOND, tool=Action.DEVICE_RESET, reason="action:reset"
        )

    if c.intent == Intent.DEVICE_STATUS:
        return RouteDecision(
            Tier.TIER1, Disposition.RESPOND, tool=Action.ACCOUNT_LOOKUP, reason="read:status"
        )

    if c.intent == Intent.GREETING:
        return RouteDecision(Tier.TIER1, Disposition.RESPOND, reason="chitchat")

    return RouteDecision(Tier.NONE, Disposition.ESCALATE, reason="unhandled")
