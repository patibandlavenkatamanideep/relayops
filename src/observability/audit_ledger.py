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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.models import AgentResponse, Disposition, TurnState
from ..router import action_policy


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
    checked = (
        response.disposition == Disposition.RESPOND or reason == "guardrail_block"
    )
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


@dataclass
class AuditRecord:
    """One row of the decision trail. ``to_dict`` is the canonical wire form."""

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

    def to_dict(self) -> dict[str, Any]:
        return {
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
        }


def build_record(
    state: TurnState,
    response: AgentResponse,
    classifier_name: str = "unknown",
) -> AuditRecord:
    """Derive the audit record for a completed turn. Pure: reads state, no I/O."""

    access = state.access
    authenticated = bool(access and access.authenticated)
    customer_id = access.customer_id if access else None

    confidence = (
        round(state.classification.confidence, 2) if state.classification else 0.0
    )
    tool = _tool_call(state)
    reason = (response.handoff_context or {}).get("reason", "")
    action = action_policy.classify_action(response.intent, reason)
    policy = action_policy.policy_for(action)

    # The gate allowed the requested scope unless the turn was blocked for an
    # access reason (cross-customer scope violation or unauthenticated caller).
    scope_blocked = "scope_violation" in reason or not authenticated
    access_gate = {
        "scope": customer_id,
        "allowed": authenticated and not scope_blocked,
    }

    return AuditRecord(
        turn_id=uuid.uuid4().hex[:12],
        timestamp=_utc_now_iso(),
        customer_id=customer_id,
        authenticated=authenticated,
        intent=response.intent.value,
        classifier=classifier_name,
        confidence=confidence,
        route=_route_label(response, tool is not None),
        action_class=action.value,
        blast_radius=policy.blast_radius.value,
        access_gate=access_gate,
        tool_call=tool,
        guardrail=_guardrail(response),
        handoff_reason=reason or None,
        evidence=_evidence(state, response),
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
    ) -> AuditRecord:
        rec = build_record(state, response, classifier_name)
        self.records.append(rec)
        return rec

    def as_dicts(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.records]
