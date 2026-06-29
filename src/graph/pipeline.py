"""Synchronous turn pipeline — the v1 orchestration.

Each step below is a pure-ish function over ``TurnState`` (a "node"). They run in
a fixed order for v1; this is the deliberate synchronous simplification from
DESIGN.md §3. Porting to LangGraph means registering these as nodes on a
``StateGraph`` with the same state object and adding the conditional edge at the
disposition branch — no node-body changes required.

    ingest -> access gate -> classify -> route -> [tool] -> compose -> GUARDRAIL -> respond

The guardrail (step 2) is an INDEPENDENT gate between compose and respond: it
vets the candidate reply and can block (force handoff) or redact. RAG (step 3),
the fine-tuned classifier (step 4), eval (step 5) and richer observability
(step 6) slot in around these nodes.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol
from uuid import uuid4

from ..access import gate
from ..actions import execute_action
from ..core import data
from ..core.models import (
    Action,
    AgentResponse,
    Disposition,
    Intent,
    ToolResult,
    TurnState,
)
from ..guardrails import guardrail
from ..mcp import tools
from ..observability.audit_ledger import AuditLedger
from ..rag.retriever import HybridRetriever
from ..rag.store import load_chunks
from ..router import action_policy, policy_broker, router
from ..router.classifier import BaselineClassifier, IntentClassifier

logger = logging.getLogger(__name__)

# Number of cited chunks to retrieve for an FAQ answer.
_RAG_K = 2

# Lazily-built retriever; the KB is loaded once per process.
_retriever: HybridRetriever | None = None


def _get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever(load_chunks())
    return _retriever


# --- nodes ---------------------------------------------------------------------


def ingest(state: TurnState) -> TurnState:
    state.text = state.raw_text.strip()
    return state


def access_gate(state: TurnState) -> TurnState:
    state.access = gate.authenticate(state.auth_token)
    return state


def classify(state: TurnState, classifier: IntentClassifier) -> TurnState:
    state.classification = classifier.classify(state.text)
    return state


def decide_route(state: TurnState) -> TurnState:
    assert state.classification is not None
    state.route = router.route(state.classification)
    return state


def build_pre_action_packet(state: TurnState) -> TurnState:
    state.pre_action_intent = policy_broker.build_pre_action_intent_packet(state)
    return state


def broker_decide(state: TurnState) -> TurnState:
    state.broker_decision = policy_broker.decide_pre_action(state)
    return state


def _pick_reset_target(state: TurnState) -> str | None:
    """Resolve which device to reset: explicit id, else the customer's first
    offline device, else their first device."""
    if state.device_id:
        return state.device_id
    assert state.access and state.access.customer_id
    devices = data.devices_for(state.access.customer_id)
    if not devices:
        return None
    offline = [d for d in devices if not d.online]
    return (offline or devices)[0].device_id


def run_tool(state: TurnState) -> TurnState:
    assert state.route is not None and state.access is not None
    action = state.route.tool
    if action is None:
        return state

    if action == Action.DEVICE_RESET:
        target = _pick_reset_target(state)
        if target is None:
            state.tool_results.append(ToolResult(ok=False, error="no_device"))
        else:
            # A device reset is a side-effecting external action: run it inside an
            # action envelope so it is identified, authorised, and safe to retry.
            ctx = state.access
            policy = action_policy.policy_for(action_policy.ActionClass.DEVICE_RESET)
            handle = state.broker_decision.policy_handle if state.broker_decision else ""
            envelope, result = execute_action(
                ctx,
                turn_id=state.turn_id,
                action="device_reset",
                target_resource=target,
                policy_handle=handle,
                blast_radius=policy.blast_radius.value,
                reversibility=policy.reversibility.value,
                tool=lambda: tools.device_reset(ctx, target),
            )
            state.action_envelopes.append(envelope)
            state.tool_results.append(result)
    elif action == Action.ACCOUNT_LOOKUP:
        # Read-only: no external side effect, so no action envelope.
        state.tool_results.append(tools.account_lookup(state.access))
    return state


def retrieve(state: TurnState) -> TurnState:
    """RAG node: pull cited, grounded facts for an informational turn."""
    state.retrieved = _get_retriever().search(state.text, k=_RAG_K)
    return state


# --- composer (Tier 1/2 stand-in) ----------------------------------------------


class Composer(Protocol):
    def compose(self, state: TurnState) -> str: ...


class TemplateComposer:
    """Deterministic templated replies so the slice runs without an LLM. The
    real Tier 2 frontier model plugs in here, drafting grounded in tool_results;
    the guardrail vets whatever it produces."""

    def compose(self, state: TurnState) -> str:
        assert state.classification is not None
        intent = state.classification.intent

        if intent == Intent.RESET_DEVICE:
            res = state.tool_results[-1] if state.tool_results else None
            if res and res.ok:
                return (
                    f"Done — I reset {res.data['name']} and it's back online. "
                    "Give it about 60 seconds to reconnect. Anything else?"
                )
            return (
                "I wasn't able to reset that device just now. Let me get a "
                "specialist to take a look."
            )

        if intent == Intent.DEVICE_FAQ:
            # Grounded answer: stitch retrieved chunks with [n] citation markers
            # and a Sources list. The real Tier 2 model would synthesise here;
            # the template keeps the answer strictly traceable to the KB.
            body = " ".join(f"{r.text} [{i}]" for i, r in enumerate(state.retrieved, 1))
            sources = "\n".join(
                f"[{i}] {r.title} ({r.source})" for i, r in enumerate(state.retrieved, 1)
            )
            return f"{body}\n\nSources:\n{sources}"

        if intent == Intent.DEVICE_STATUS:
            res = state.tool_results[-1] if state.tool_results else None
            if res and res.ok:
                lines = [
                    f"- {d['name']}: {'online' if d['online'] else 'offline'}"
                    for d in res.data.get("devices", [])
                ]
                return "Here's your device status:\n" + "\n".join(lines)
            return "I couldn't pull your device status right now."

        if intent == Intent.GREETING:
            return "Hi! I can help reset a device or check its status. What's going on?"

        return "Let me connect you with someone who can help."


# --- response builders ---------------------------------------------------------


def _escalate(
    state: TurnState,
    reason: str,
    extra: dict | None = None,
    guardrail_action: str = "not_reached",
    guardrail_violations: list[str] | None = None,
) -> AgentResponse:
    assert state.classification is not None and state.route is not None
    # Build a COMPLETE handoff (owner, evidence, customer promise, SLA) so the
    # next human can continue without rereading the chat — safe-but-stranded is
    # still a failure. See action_policy.REQUIRED_HANDOFF_FIELDS.
    handoff = action_policy.build_handoff(
        intent=state.classification.intent,
        reason=reason,
        customer_message=state.text,
        confidence=state.classification.confidence,
        extra=extra,
    )
    text = policy_broker.compose_escalation_reply(state.broker_decision)
    state.final_reply = policy_broker.build_final_reply_packet(
        state,
        text=text,
        guardrail_action=guardrail_action,
        guardrail_violations=guardrail_violations,
    )
    return AgentResponse(
        text=text,
        intent=state.classification.intent,
        tier=state.route.tier,
        disposition=Disposition.ESCALATE,
        escalated=True,
        tool_results=state.tool_results,
        handoff_context=handoff,
        guardrail_action=guardrail_action,
        guardrail_violations=list(guardrail_violations or []),
        action_envelopes=[e.to_dict() for e in state.action_envelopes],
    )


def _respond(state: TurnState, text: str, guard: guardrail.GuardrailResult) -> AgentResponse:
    assert state.classification is not None and state.route is not None
    citations = [
        {"n": i, "title": r.title, "source": r.source, "doc_id": r.doc_id}
        for i, r in enumerate(state.retrieved, 1)
    ]
    state.final_reply = policy_broker.build_final_reply_packet(
        state,
        text=text,
        guardrail_action=guard.action,
        guardrail_violations=guard.violations,
    )
    return AgentResponse(
        text=text,
        intent=state.classification.intent,
        tier=state.route.tier,
        disposition=Disposition.RESPOND,
        tool_results=state.tool_results,
        guardrail_action=guard.action,
        guardrail_violations=guard.violations,
        citations=citations,
        action_envelopes=[e.to_dict() for e in state.action_envelopes],
    )


# --- orchestrator --------------------------------------------------------------


def handle_turn(
    raw_text: str,
    auth_token: str | None = None,
    device_id: str | None = None,
    classifier: IntentClassifier | None = None,
    composer: Composer | None = None,
    audit: AuditLedger | None = None,
    classifier_name: str | None = None,
    turn_id: str | None = None,
) -> AgentResponse:
    """Run one customer turn through the full v1 pipeline.

    Pass an ``AuditLedger`` to capture the per-turn decision trail (gate, scope,
    route, tool, guardrail, handoff reason, evidence) — see
    ``observability/audit_ledger.py``.

    ``turn_id`` lets a service layer supply a request-scoped id that is then
    shared by the audit record (and anything that persists it); when omitted the
    ledger mints its own.
    """
    classifier = classifier or BaselineClassifier()
    if composer is None:
        # Lazy import avoids a cycle (composer selection imports this module) and
        # keeps the default offline: get_composer() returns TemplateComposer unless
        # RELAYOPS_COMPOSER=llm and a key are set.
        from ..composer.llm_composer import get_composer

        composer = get_composer()
    name = classifier_name or type(classifier).__name__
    state = TurnState(
        raw_text=raw_text,
        auth_token=auth_token,
        device_id=device_id,
        turn_id=turn_id or uuid4().hex[:12],
    )

    start = time.perf_counter()

    def finalize(response: AgentResponse) -> AgentResponse:
        response.latency_ms = (time.perf_counter() - start) * 1000
        state.response = response
        if audit is not None:
            audit.record(state, response, name, turn_id=state.turn_id)
        return response

    def fail_closed(reason_code: str, stage: str) -> AgentResponse:
        """Fail-closed invariant: when a safety-critical layer can't render a
        verdict (raised), force a safe human handoff instead of allowing the
        turn. The reply is generated from the broker packet, never raw model
        output, and the audit record captures why."""
        logger.warning("fail_closed: stage=%s reason=%s turn=%s", stage, reason_code, state.turn_id)
        state.broker_decision = policy_broker.decision_from_fail_closed(state, reason_code, stage)
        return finalize(_escalate(state, reason=f"fail_closed:{reason_code}"))

    state = ingest(state)
    state = access_gate(state)
    state = classify(state, classifier)
    state = decide_route(state)

    # Proposal + policy decision. If the broker itself can't decide, fail closed.
    try:
        state = build_pre_action_packet(state)
        state = broker_decide(state)
    except Exception:
        return fail_closed("policy_lookup_failed", "policy_broker")

    # The broker is the source of truth. A non-allow decision never reaches a
    # tool or raw model-composed reply.
    if state.broker_decision and state.broker_decision.decision != "allow":
        reason = state.broker_decision.reason_code
        if state.route and state.route.disposition == Disposition.ESCALATE and state.route.reason:
            reason = state.route.reason
        return finalize(_escalate(state, reason=reason))

    try:
        state = run_tool(state)
    except Exception:
        return fail_closed("tool_unavailable", "scoped_tool")
    if state.tool_results and not state.tool_results[-1].ok:
        error = state.tool_results[-1].error or "tool_failed"
        state.broker_decision = policy_broker.decision_from_tool_error(state, error)
        return finalize(_escalate(state, reason=f"tool_error:{error}"))

    if state.route and state.route.retrieve:
        try:
            state = retrieve(state)
        except Exception:
            return fail_closed("rag_unavailable", "rag")
        # No grounding found -> don't answer from nothing; escalate.
        if not state.retrieved:
            state.broker_decision = policy_broker.decision_from_unverifiable(state)
            return finalize(_escalate(state, reason="unverifiable"))

    # Compose the candidate and vet it. A failure in either safety layer fails
    # closed rather than shipping an unvetted reply.
    try:
        candidate = composer.compose(state)
    except Exception:
        return fail_closed("composer_timeout", "composer")
    try:
        guard = guardrail.check(candidate)
    except Exception:
        return fail_closed("guardrail_unavailable", "guardrail")

    if guard.blocked:
        # A hallucinated offer/price (or tone breach) is escalated to a human.
        state.broker_decision = policy_broker.decision_from_guardrail_block(state, guard.violations)
        response = _escalate(
            state,
            reason="guardrail_block",
            extra={"violations": guard.violations, "blocked_candidate": candidate},
            guardrail_action="block",
            guardrail_violations=guard.violations,
        )
        return finalize(response)

    return finalize(_respond(state, guard.text, guard))
