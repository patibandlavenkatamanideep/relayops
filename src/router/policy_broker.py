"""Policy broker packets.

The classifier/router can propose what a turn appears to need, but policy lives
outside model output. This module formalises that boundary:

    model/router proposes -> broker decides -> tool executes only if allowed
    -> final reply is generated from the broker decision packet.

The functions are deterministic and side-effect free; they only read the current
``TurnState`` and return packet dataclasses.
"""

from __future__ import annotations

from ..core.models import (
    Action,
    BrokerDecisionPacket,
    Disposition,
    FinalReplyPacket,
    Intent,
    PreActionIntentPacket,
    TurnState,
)
from . import action_policy

POLICY_VERSION = "relayops_policy_v1"

# Reason codes for the fail-closed invariant: if a safety-critical layer cannot
# render a verdict, RelayOps must fail closed (escalate / hand off) — never
# silently allow. These label *why* the safe path was forced.
FAIL_CLOSED_REASONS = frozenset(
    {
        "guardrail_unavailable",
        "rag_no_evidence",
        "composer_timeout",
        "policy_lookup_failed",
        "missing_required_evidence",
        "unsafe_candidate_blocked",
        "over_block_review",
    }
)

_GREETING_ACTION = "greeting_response"
_GREETING_POLICY_HANDLE = "conversation.greeting.respond_allowed"
_GREETING_RULE = "safe_greeting_response_allowed"


_POLICY_HANDLES = {
    action_policy.ActionClass.DEVICE_RESET: "device.reset.allowed_if_scoped",
    action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK: "faq.answer.requires_grounding",
    action_policy.ActionClass.ACCOUNT_READ: "account.status.requires_authenticated_scope",
    action_policy.ActionClass.BILLING_REFUND: "billing.refund.requires_human",
    action_policy.ActionClass.PLAN_CHANGE: "billing.plan_change.requires_human",
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: "account.change.requires_verification",
    action_policy.ActionClass.UNKNOWN: "support.unknown.requires_human",
}

_MATCHED_RULES = {
    action_policy.ActionClass.DEVICE_RESET: "device_reset_allowed_after_auth_and_scope",
    action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK: "faq_answer_requires_grounded_citation",
    action_policy.ActionClass.ACCOUNT_READ: "account_read_requires_authenticated_scope",
    action_policy.ActionClass.BILLING_REFUND: "discounts_refunds_require_human_review",
    action_policy.ActionClass.PLAN_CHANGE: "plan_changes_require_human_review",
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: "account_changes_require_verification",
    action_policy.ActionClass.UNKNOWN: "unsupported_or_low_confidence_requires_human",
}

_TARGET_RESOURCES = {
    action_policy.ActionClass.DEVICE_RESET: "customer_device",
    action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK: "knowledge_base_article",
    action_policy.ActionClass.ACCOUNT_READ: "customer_account",
    action_policy.ActionClass.BILLING_REFUND: "customer_billing_account",
    action_policy.ActionClass.PLAN_CHANGE: "customer_plan",
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: "customer_identity_or_account_scope",
    action_policy.ActionClass.UNKNOWN: "support_queue",
}

_MODEL_INTERPRETATIONS = {
    Intent.RESET_DEVICE: "Customer is asking for a device reset",
    Intent.DEVICE_STATUS: "Customer is asking to read device/account status",
    Intent.DEVICE_FAQ: "Customer is asking an informational device-support question",
    Intent.BILLING: "Customer is asking for billing, plan, payment, or discount help",
    Intent.GREETING: "Customer is greeting the support agent",
    Intent.UNKNOWN: "Customer request is unsupported or ambiguous for the current slice",
}

_PROPOSED_SAFE_RESPONSES = {
    action_policy.ActionClass.DEVICE_RESET: (
        "I can reset the scoped device only after the scoped tool confirms ownership."
    ),
    action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK: (
        "I can answer from the knowledge base when the answer is grounded."
    ),
    action_policy.ActionClass.ACCOUNT_READ: (
        "I can share read-only status for the authenticated account scope."
    ),
    action_policy.ActionClass.BILLING_REFUND: (
        "I can connect you with billing support to review available options."
    ),
    action_policy.ActionClass.PLAN_CHANGE: (
        "I can connect you with billing support to review plan changes."
    ),
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: (
        "I can connect you with a specialist for verification and account-scope review."
    ),
    action_policy.ActionClass.UNKNOWN: (
        "I can connect you with a support specialist who can review this safely."
    ),
}

_MISSING_EVIDENCE = {
    action_policy.ActionClass.BILLING_REFUND: [
        "active_promo_id",
        "billing_history",
        "agent_authorization",
    ],
    action_policy.ActionClass.PLAN_CHANGE: [
        "current_contract_terms",
        "authorized_plan_catalog",
        "agent_authorization",
    ],
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: [
        "identity_verification",
        "authorized_resource_scope",
    ],
    action_policy.ActionClass.UNKNOWN: ["supported_policy_or_knowledge_source"],
}


def _action_from_state(state: TurnState) -> action_policy.ActionClass:
    assert state.classification is not None
    reason = state.route.reason if state.route else ""
    return action_policy.classify_action(state.classification.intent, reason)


def _is_safe_greeting(state: TurnState) -> bool:
    return (
        state.classification is not None
        and state.classification.intent == Intent.GREETING
        and state.route is not None
        and state.route.disposition == Disposition.RESPOND
        and state.route.tool is None
        and not state.route.retrieve
    )


def _handle(action: action_policy.ActionClass) -> str:
    return _POLICY_HANDLES[action]


def _rule(action: action_policy.ActionClass) -> str:
    return _MATCHED_RULES[action]


def build_pre_action_intent_packet(state: TurnState) -> PreActionIntentPacket:
    """Capture what the model/router thinks should happen before acting."""

    assert state.classification is not None
    assert state.route is not None
    if _is_safe_greeting(state):
        confidence = round(state.classification.confidence, 2)
        return PreActionIntentPacket(
            turn_id=state.turn_id,
            user_request=state.text,
            model_interpretation=_MODEL_INTERPRETATIONS[Intent.GREETING],
            requested_action=_GREETING_ACTION,
            target_resource="conversation",
            policy_handle=_GREETING_POLICY_HANDLE,
            evidence_quote=state.text,
            confidence=confidence,
            ambiguity="" if confidence >= 0.75 else "classification confidence is moderate",
            proposed_safe_response="I can greet the customer and offer supported help.",
        )

    action = _action_from_state(state)
    confidence = round(state.classification.confidence, 2)
    ambiguity = ""
    if state.route.disposition == Disposition.ESCALATE:
        ambiguity = state.route.reason or "policy requires human review"
    elif confidence < 0.75:
        ambiguity = "classification confidence is moderate"

    return PreActionIntentPacket(
        turn_id=state.turn_id,
        user_request=state.text,
        model_interpretation=_MODEL_INTERPRETATIONS[state.classification.intent],
        requested_action=action.value,
        target_resource=_TARGET_RESOURCES[action],
        policy_handle=_handle(action),
        evidence_quote=state.text,
        confidence=confidence,
        ambiguity=ambiguity,
        proposed_safe_response=_PROPOSED_SAFE_RESPONSES[action],
    )


def decide_pre_action(state: TurnState) -> BrokerDecisionPacket:
    """Decide whether the proposed action may proceed."""

    assert state.pre_action_intent is not None
    assert state.route is not None
    action = _action_from_state(state)
    policy = action_policy.policy_for(action)

    if state.access is None or not state.access.authenticated:
        return BrokerDecisionPacket(
            turn_id=state.turn_id,
            decision="escalate",
            policy_version=POLICY_VERSION,
            policy_handle="auth.customer_scope.required",
            matched_rule="authenticated_customer_scope_required_before_action",
            reason_code="unauthenticated",
            missing_evidence=["authenticated_customer_scope"],
            owner="identity_security",
            human_queue="identity_review",
            allowed_next_actions=["explain_verification_required", "route_to_human"],
            forbidden_next_actions=["run_tool", "quote_account_data", "change_account"],
        )

    if state.route.disposition == Disposition.ESCALATE:
        return _escalation_decision(
            state=state,
            action=action,
            reason_code=state.route.reason or "requires_human_review",
        )

    if _is_safe_greeting(state):
        return BrokerDecisionPacket(
            turn_id=state.turn_id,
            decision="allow",
            policy_version=POLICY_VERSION,
            policy_handle=_GREETING_POLICY_HANDLE,
            matched_rule=_GREETING_RULE,
            reason_code="policy_allow",
            owner="general_support",
            human_queue="",
            allowed_next_actions=["respond_without_tool"],
            forbidden_next_actions=[
                "run_tool",
                "quote_account_data",
                "invent_policy_or_account_data",
            ],
        )

    if policy.route == action_policy.PolicyRoute.ESCALATE:
        return _escalation_decision(
            state=state,
            action=action,
            reason_code="policy_requires_human_review",
        )

    if state.route.tool is not None and not state.access.may(state.route.tool):
        return BrokerDecisionPacket(
            turn_id=state.turn_id,
            decision="block",
            policy_version=POLICY_VERSION,
            policy_handle=_handle(action),
            matched_rule="tool_requires_authenticated_scope_permission",
            reason_code="tool_permission_denied",
            missing_evidence=["allowed_tool_permission"],
            owner=policy.owner,
            human_queue=_queue_for(policy.owner),
            allowed_next_actions=["explain_scope_limit", "route_to_human"],
            forbidden_next_actions=["run_tool", "retry_unscoped_tool"],
        )

    return BrokerDecisionPacket(
        turn_id=state.turn_id,
        decision="allow",
        policy_version=POLICY_VERSION,
        policy_handle=_handle(action),
        matched_rule=_rule(action),
        reason_code="policy_allow",
        owner=policy.owner,
        human_queue="",
        allowed_next_actions=_allowed_actions_for_allow(state),
        forbidden_next_actions=["widen_customer_scope", "invent_tool_result"],
    )


def decision_from_tool_error(state: TurnState, error: str) -> BrokerDecisionPacket:
    """Update the broker packet after the scoped tool broker refuses/fails."""

    action = (
        action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE
        if "scope_violation" in error or "unauthenticated" in error
        else _action_from_state(state)
    )
    policy = action_policy.policy_for(action)
    return BrokerDecisionPacket(
        turn_id=state.turn_id,
        decision="escalate",
        policy_version=POLICY_VERSION,
        policy_handle=_handle(action),
        matched_rule="scoped_tool_broker_refused_or_failed",
        reason_code=f"tool_error:{error}",
        missing_evidence=["authorized_resource_scope"] if "scope_violation" in error else [],
        owner=policy.owner,
        human_queue=_queue_for(policy.owner),
        allowed_next_actions=["explain_scope_or_tool_failure", "route_to_human"],
        forbidden_next_actions=["retry_unscoped_tool", "claim_tool_succeeded"],
    )


def decision_from_unverifiable(state: TurnState) -> BrokerDecisionPacket:
    """Update the broker packet when an answer lacks grounding."""

    policy = action_policy.policy_for(action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK)
    return BrokerDecisionPacket(
        turn_id=state.turn_id,
        decision="escalate",
        policy_version=POLICY_VERSION,
        policy_handle=_handle(action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK),
        matched_rule="faq_answer_requires_grounded_citation",
        reason_code="unverifiable",
        missing_evidence=["grounded_kb_citation"],
        owner=policy.owner,
        human_queue=_queue_for(policy.owner),
        allowed_next_actions=["explain_unverifiable", "route_to_human"],
        forbidden_next_actions=["answer_without_grounding", "invent_knowledge_base_fact"],
    )


def decision_from_guardrail_block(
    state: TurnState, violations: list[str]
) -> BrokerDecisionPacket:
    """Update the broker packet after final-text guardrail failure."""

    base_action = _action_from_state(state)
    policy = action_policy.policy_for(base_action)
    owner = policy.owner if policy.owner != "device_support" else "general_support"
    return BrokerDecisionPacket(
        turn_id=state.turn_id,
        decision="block",
        policy_version=POLICY_VERSION,
        policy_handle="response.guardrail.offer_pii_tone",
        matched_rule="final_reply_guardrail_failed",
        reason_code="guardrail_block",
        missing_evidence=violations,
        owner=owner,
        human_queue=_queue_for(owner),
        allowed_next_actions=["explain_safe_handoff", "route_to_human"],
        forbidden_next_actions=["send_blocked_candidate", "promise_discount", "quote_unapproved_price"],
    )


def decision_from_fail_closed(
    state: TurnState, reason_code: str, stage: str
) -> BrokerDecisionPacket:
    """Build a fail-closed escalation when a safety-critical layer cannot render
    a verdict (an exception, timeout, or unavailable dependency).

    Deliberately self-contained: it does NOT consult ``action_policy`` (which may
    itself be the failed layer) so it can always produce a safe handoff. The
    invariant is: no verdict -> escalate, never silently allow.
    """

    return BrokerDecisionPacket(
        turn_id=state.turn_id,
        decision="escalate",
        policy_version=POLICY_VERSION,
        policy_handle="safety.fail_closed",
        matched_rule="safety_layer_unavailable_fail_closed",
        reason_code=reason_code,
        missing_evidence=[f"verdict_from:{stage}"],
        owner="general_support",
        human_queue="general_support",
        allowed_next_actions=["explain_safe_handoff", "route_to_human"],
        forbidden_next_actions=[
            "run_tool",
            "send_unverified_reply",
            "claim_action_completed",
        ],
    )


def build_final_reply_packet(
    state: TurnState,
    *,
    text: str,
    guardrail_action: str = "not_reached",
    guardrail_violations: list[str] | None = None,
) -> FinalReplyPacket:
    """Record that the final reply is derived from the broker decision."""

    assert state.broker_decision is not None
    return FinalReplyPacket(
        turn_id=state.turn_id,
        source="broker_decision_packet",
        broker_decision=state.broker_decision.decision,
        policy_handle=state.broker_decision.policy_handle,
        text=text,
        guardrail_action=guardrail_action,
        guardrail_violations=list(guardrail_violations or []),
    )


def compose_escalation_reply(decision: BrokerDecisionPacket | None) -> str:
    """Safe customer text for a non-allow broker decision.

    This is intentionally generated from the broker packet, not from any model
    proposal, so blocked discount/price language cannot leak into the final
    answer.
    """

    if decision is None:
        return (
            "I'm connecting you with a specialist who can help with this — "
            "they'll have the full context of our chat."
        )

    handle = decision.policy_handle
    reason = decision.reason_code
    if handle.startswith("billing."):
        return (
            "I can't apply or promise billing changes, discounts, credits, or "
            "refunds from here. A billing specialist can review the account and "
            "follow up with the full context."
        )
    if handle.startswith("account.") or handle.startswith("auth."):
        return (
            "I can't bypass verification or act outside the authenticated account "
            "scope. I'll connect you with a specialist who can review this safely."
        )
    if reason.startswith("tool_error:scope_violation"):
        return (
            "I can't take action on a device outside the authenticated account "
            "scope. I'll connect you with a specialist who can review the request."
        )
    if reason == "guardrail_block":
        return (
            "I can't send an unverified offer, price, or account claim. I'll "
            "connect you with a specialist who can review this safely."
        )
    if handle == "safety.fail_closed" or reason in FAIL_CLOSED_REASONS:
        return (
            "I can't complete this safely right now, so I'm routing you to a "
            "specialist who can review and follow up with the full context."
        )
    return (
        "I'm connecting you with a specialist who can help with this — they'll "
        "have the full context of our chat."
    )


def _escalation_decision(
    *,
    state: TurnState,
    action: action_policy.ActionClass,
    reason_code: str,
) -> BrokerDecisionPacket:
    policy = action_policy.policy_for(action)
    forbidden = ["run_high_risk_tool", "claim_action_completed"]
    if action in (action_policy.ActionClass.BILLING_REFUND, action_policy.ActionClass.PLAN_CHANGE):
        forbidden = ["promise_discount", "apply_discount", "quote_unapproved_price"]
    elif action == action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE:
        forbidden = ["bypass_verification", "access_other_customer_data", "change_account"]

    return BrokerDecisionPacket(
        turn_id=state.turn_id,
        decision="escalate",
        policy_version=POLICY_VERSION,
        policy_handle=_handle(action),
        matched_rule=_rule(action),
        reason_code=reason_code,
        missing_evidence=list(_MISSING_EVIDENCE.get(action, [])),
        owner=policy.owner,
        human_queue=_queue_for(policy.owner),
        allowed_next_actions=["explain_policy", "route_to_human"],
        forbidden_next_actions=forbidden,
    )


def _allowed_actions_for_allow(state: TurnState) -> list[str]:
    assert state.route is not None
    if state.route.tool == Action.DEVICE_RESET:
        return ["call_scoped_tool", "describe_tool_result"]
    if state.route.tool == Action.ACCOUNT_LOOKUP:
        return ["call_scoped_read_tool", "summarize_scoped_result"]
    if state.route.retrieve:
        return ["retrieve_grounded_faq", "answer_with_citations"]
    return ["respond_without_tool"]


def _queue_for(owner: str) -> str:
    if owner == "billing_support":
        return "billing_review"
    if owner == "identity_security":
        return "identity_review"
    if owner == "device_support":
        return "device_support_review"
    return "general_support"
