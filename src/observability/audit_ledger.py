"""Per-turn audit ledger — execution evidence, not just architecture.

The pipeline diagram proves the *design* (gate -> router -> tool/RAG -> guardrail
-> respond/handoff). The audit ledger proves what actually happened *on each
turn*: which gate ran, what scope it applied, which policy fired, whether a tool
was called, whether the guardrail passed or blocked, and why the system
responded or escalated.

This is the artifact a regulated-telecom reviewer asks for after they accept the
architecture: "show me the decision trail for this conversation." Every record
is built deterministically from the turn's ``TurnState`` + ``AgentResponse`` — no
re-inference — so the ledger can't drift from what the agent actually did.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.models import AgentResponse, Disposition, TurnState
from ..router import action_policy

# Bump when the audit-record shape changes, so exported evidence is
# self-describing and old rows stay interpretable after the schema evolves.
SCHEMA_VERSION = "1.1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_call(state: TurnState) -> Optional[dict[str, Any]]:
    """Summarise the tool call, if any ran this turn."""
    if not state.tool_results:
        return None
    result = state.tool_results[-1]
    name = state.route.tool.value if state.route and state.route.tool else "unknown"
    return {"name": name, "ok": result.ok, "error": result.error or None}


def _route_label(response: AgentResponse, tool_called: bool) -> str:
    if response.escalated or response.disposition == Disposition.ESCALATE:
        return "human_escalation"
    if tool_called:
        return "auto_action"
    return "respond"


def _guardrail(response: AgentResponse) -> dict[str, Any]:
    """The guardrail only runs once the turn reaches the compose step. A turn
    escalated earlier (billing, low-confidence, scope, unauth) never composed a
    candidate, so it was never guardrail-checked — record that honestly rather
    than implying a check that didn't happen."""
    reason = (response.handoff_context or {}).get("reason", "")
    checked = response.disposition == Disposition.RESPOND or reason == "guardrail_block"
    return {
        "checked": checked,
        "verdict": response.guardrail_action if checked else "not_reached",
        "violations": list(response.guardrail_violations),
    }


def _evidence(state: TurnState, response: AgentResponse) -> list[str]:
    """The quote(s) that justify the decision: the triggering customer message,
    plus the cited sources when the agent answered from RAG."""
    evidence: list[str] = []
    handoff = response.handoff_context or {}
    quote = handoff.get("evidence_quote") or state.text
    if quote:
        evidence.append(quote)
    for c in response.citations:
        title = c.get("title")
        if title:
            evidence.append(f"cited: {title} ({c.get('source', '')})")
    return evidence


_POLICY_RULES = {
    action_policy.ActionClass.DEVICE_RESET: "device_reset_allowed_after_auth",
    action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK: "faq_requires_grounding",
    action_policy.ActionClass.ACCOUNT_READ: "account_read_requires_authenticated_scope",
    action_policy.ActionClass.BILLING_REFUND: "billing_refund_requires_human",
    action_policy.ActionClass.PLAN_CHANGE: "plan_change_requires_human",
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: "account_access_requires_verification",
    action_policy.ActionClass.UNKNOWN: "unsupported_or_low_confidence_requires_human",
}

_PROPOSED_ACTIONS = {
    action_policy.ActionClass.DEVICE_RESET: "device_reset",
    action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK: "faq_response",
    action_policy.ActionClass.ACCOUNT_READ: "account_status_lookup",
    action_policy.ActionClass.BILLING_REFUND: "refund_review",
    action_policy.ActionClass.PLAN_CHANGE: "plan_change_review",
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: "identity_or_scope_review",
    action_policy.ActionClass.UNKNOWN: "support_review",
}

_RISK_SIGNALS = {
    action_policy.ActionClass.DEVICE_RESET: "low_risk_reversible_device_action",
    action_policy.ActionClass.SEND_TROUBLESHOOTING_LINK: "grounded_information_request",
    action_policy.ActionClass.ACCOUNT_READ: "read_only_account_request",
    action_policy.ActionClass.BILLING_REFUND: "money_touching_request",
    action_policy.ActionClass.PLAN_CHANGE: "money_touching_request",
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: "identity_or_scope_request",
    action_policy.ActionClass.UNKNOWN: "unsupported_or_uncertain_request",
}

_UNAVAILABLE_CONTEXT = {
    action_policy.ActionClass.BILLING_REFUND: [
        "billing_history",
        "payment_method",
        "prior_agent_promises",
    ],
    action_policy.ActionClass.PLAN_CHANGE: [
        "current_contract_terms",
        "retention_offer_policy",
        "billing_history",
    ],
    action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE: [
        "identity_verification",
        "authorized_third_party_permission",
        "account_admin_history",
    ],
    action_policy.ActionClass.UNKNOWN: ["specialist_domain_context"],
}


def _available_context(state: TurnState) -> list[str]:
    context = ["message"]
    if state.access and state.access.customer_id:
        context.append("customer_id")
    if state.access and state.access.authenticated:
        context.append("device_scope")
    if state.tool_results:
        context.append("tool_result")
    if state.retrieved:
        context.append("kb_citations")
    return context


def _tool_permission_reason(
    action: action_policy.ActionClass, tool: Optional[dict[str, Any]]
) -> str:
    if action == action_policy.ActionClass.BILLING_REFUND:
        return "billing action not auto-executable"
    if action == action_policy.ActionClass.PLAN_CHANGE:
        return "plan change not auto-executable"
    if action == action_policy.ActionClass.ACCOUNT_ACCESS_CHANGE:
        return "account/identity action requires verification"
    if tool:
        return tool.get("error") or "allowed"
    return "no_tool_required"


def _decision_steps(
    *,
    state: TurnState,
    response: AgentResponse,
    route_label: str,
    action: action_policy.ActionClass,
    rule: str,
    tool: Optional[dict[str, Any]],
    access_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    handoff = response.handoff_context or {}
    confidence = round(state.classification.confidence, 2) if state.classification else 0.0
    allowed = bool(access_gate["allowed"])
    pre_action = state.pre_action_intent
    broker = state.broker_decision
    steps: list[dict[str, Any]] = [
        {
            "stage": "access_gate",
            "result": "allowed" if allowed else "blocked",
            "scope": access_gate.get("scope"),
        },
        {
            "stage": "classifier",
            "intent": response.intent.value,
            "confidence": confidence,
        },
    ]

    if pre_action is not None:
        steps.append(
            {
                "stage": "pre_action_intent_packet",
                "requested_action": pre_action.requested_action,
                "policy_handle": pre_action.policy_handle,
                "confidence": pre_action.confidence,
            }
        )

    if broker is not None:
        steps.append(
            {
                "stage": "policy_broker",
                "decision": broker.decision,
                "matched_rule": broker.matched_rule,
                "reason_code": broker.reason_code,
            }
        )

    steps.append(
        {
            "stage": "policy",
            "rule": rule,
            "route": route_label,
        },
    )

    tool_allowed = bool(tool and tool.get("ok") and route_label == "auto_action")
    steps.append(
        {
            "stage": "tool_permission",
            "allowed": tool_allowed,
            "reason": _tool_permission_reason(action, tool),
        }
    )

    if response.guardrail_action not in ("pass", "not_reached") or response.guardrail_violations:
        steps.append(
            {
                "stage": "guardrail",
                "result": response.guardrail_action,
                "violations": list(response.guardrail_violations),
            }
        )

    if route_label == "human_escalation":
        steps.append(
            {
                "stage": "handoff",
                "owner": handoff.get("owner", ""),
                "deadline": handoff.get("deadline", ""),
            }
        )
    else:
        steps.append({"stage": "response", "result": route_label})

    return steps


@dataclass
class AuditRecord:
    """One row of the decision trail. ``to_dict`` is the canonical wire form."""

    schema_version: str
    turn_id: str
    timestamp: str
    customer_id: Optional[str]
    authenticated: bool
    intent: str
    classifier: str
    confidence: float
    route: str
    action_class: str
    blast_radius: str
    access_gate: dict[str, Any]
    tool_call: Optional[dict[str, Any]]
    guardrail: dict[str, Any]
    handoff_reason: Optional[str]
    evidence: list[str]
    pre_action_intent_packet: dict[str, Any]
    broker_decision_packet: dict[str, Any]
    final_reply_packet: dict[str, Any]
    decision_steps: list[dict[str, Any]]
    proposed_action: str
    blocking_rule: Optional[str]
    risk_signal: str
    available_context: list[str]
    unavailable_context: list[str]
    action_envelopes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "customer_id": self.customer_id,
            "authenticated": self.authenticated,
            "intent": self.intent,
            "classifier": self.classifier,
            "confidence": self.confidence,
            "route": self.route,
            "action_class": self.action_class,
            "blast_radius": self.blast_radius,
            "access_gate": self.access_gate,
            "tool_call": self.tool_call,
            "guardrail": self.guardrail,
            "handoff_reason": self.handoff_reason,
            "evidence": self.evidence,
            "pre_action_intent_packet": self.pre_action_intent_packet,
            "broker_decision_packet": self.broker_decision_packet,
            "final_reply_packet": self.final_reply_packet,
            "decision_steps": self.decision_steps,
            "proposed_action": self.proposed_action,
            "blocking_rule": self.blocking_rule,
            "risk_signal": self.risk_signal,
            "available_context": self.available_context,
            "unavailable_context": self.unavailable_context,
            "action_envelopes": self.action_envelopes,
        }


def build_record(
    state: TurnState,
    response: AgentResponse,
    classifier_name: str = "unknown",
    turn_id: Optional[str] = None,
) -> AuditRecord:
    """Derive the audit record for a completed turn. Pure: reads state, no I/O.

    ``turn_id`` lets a caller (e.g. the API layer) supply a request-scoped id so
    the same id appears in the response, the audit record, and the logs. When
    omitted a fresh opaque id is generated, preserving the prior behaviour.
    """

    access = state.access
    authenticated = bool(access and access.authenticated)
    customer_id = access.customer_id if access else None

    confidence = round(state.classification.confidence, 2) if state.classification else 0.0
    tool = _tool_call(state)
    reason = (response.handoff_context or {}).get("reason", "")
    action = action_policy.classify_action(response.intent, reason)
    policy = action_policy.policy_for(action)
    rule = _POLICY_RULES[action]
    route_label = _route_label(response, tool is not None)

    # The gate allowed the requested scope unless the turn was blocked for an
    # access reason (cross-customer scope violation or unauthenticated caller).
    scope_blocked = "scope_violation" in reason or not authenticated
    access_gate = {
        "scope": customer_id,
        "allowed": authenticated and not scope_blocked,
    }
    blocking_rule = rule if route_label == "human_escalation" else None
    pre_action_packet = asdict(state.pre_action_intent) if state.pre_action_intent else {}
    broker_decision_packet = asdict(state.broker_decision) if state.broker_decision else {}
    final_reply_packet = asdict(state.final_reply) if state.final_reply else {}

    return AuditRecord(
        schema_version=SCHEMA_VERSION,
        turn_id=turn_id or uuid.uuid4().hex[:12],
        timestamp=_utc_now_iso(),
        customer_id=customer_id,
        authenticated=authenticated,
        intent=response.intent.value,
        classifier=classifier_name,
        confidence=confidence,
        route=route_label,
        action_class=action.value,
        blast_radius=policy.blast_radius.value,
        access_gate=access_gate,
        tool_call=tool,
        guardrail=_guardrail(response),
        handoff_reason=reason or None,
        evidence=_evidence(state, response),
        pre_action_intent_packet=pre_action_packet,
        broker_decision_packet=broker_decision_packet,
        final_reply_packet=final_reply_packet,
        decision_steps=_decision_steps(
            state=state,
            response=response,
            route_label=route_label,
            action=action,
            rule=rule,
            tool=tool,
            access_gate=access_gate,
        ),
        proposed_action=_PROPOSED_ACTIONS[action],
        blocking_rule=blocking_rule,
        risk_signal=_RISK_SIGNALS[action],
        available_context=_available_context(state),
        unavailable_context=list(_UNAVAILABLE_CONTEXT.get(action, [])),
        action_envelopes=[e.to_dict() for e in state.action_envelopes],
    )


@dataclass
class AuditLedger:
    """In-memory sink for audit records. A real deployment would append these to
    a durable, tamper-evident store; the schema is the same either way."""

    records: list[AuditRecord] = field(default_factory=list)

    def record(
        self,
        state: TurnState,
        response: AgentResponse,
        classifier_name: str = "unknown",
        turn_id: Optional[str] = None,
    ) -> AuditRecord:
        rec = build_record(state, response, classifier_name, turn_id=turn_id)
        self.records.append(rec)
        return rec

    def as_dicts(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.records]
