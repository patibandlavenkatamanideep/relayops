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

import time
from typing import Protocol

from ..access import gate
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
from ..rag.retriever import HybridRetriever
from ..rag.store import load_chunks
from ..router import router
from ..router.classifier import BaselineClassifier, IntentClassifier

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
            state.tool_results.append(tools.device_reset(state.access, target))
    elif action == Action.ACCOUNT_LOOKUP:
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
                    f"Done — I reset your {res.data['name']} and it's back online. "
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


def _escalate(state: TurnState, reason: str, extra: dict | None = None) -> AgentResponse:
    assert state.classification is not None and state.route is not None
    handoff = {
        "reason": reason,
        "intent": state.classification.intent.value,
        "confidence": round(state.classification.confidence, 2),
        "message": state.text,
    }
    if extra:
        handoff.update(extra)
    return AgentResponse(
        text=(
            "I'm connecting you with a specialist who can help with this — "
            "they'll have the full context of our chat."
        ),
        intent=state.classification.intent,
        tier=state.route.tier,
        disposition=Disposition.ESCALATE,
        escalated=True,
        tool_results=state.tool_results,
        handoff_context=handoff,
    )


def _respond(state: TurnState, text: str, guard: guardrail.GuardrailResult) -> AgentResponse:
    assert state.classification is not None and state.route is not None
    citations = [
        {"n": i, "title": r.title, "source": r.source, "doc_id": r.doc_id}
        for i, r in enumerate(state.retrieved, 1)
    ]
    return AgentResponse(
        text=text,
        intent=state.classification.intent,
        tier=state.route.tier,
        disposition=Disposition.RESPOND,
        tool_results=state.tool_results,
        guardrail_action=guard.action,
        guardrail_violations=guard.violations,
        citations=citations,
    )


# --- orchestrator --------------------------------------------------------------


def handle_turn(
    raw_text: str,
    auth_token: str | None = None,
    device_id: str | None = None,
    classifier: IntentClassifier | None = None,
    composer: Composer | None = None,
) -> AgentResponse:
    """Run one customer turn through the full v1 pipeline."""
    classifier = classifier or BaselineClassifier()
    composer = composer or TemplateComposer()
    state = TurnState(raw_text=raw_text, auth_token=auth_token, device_id=device_id)

    start = time.perf_counter()
    state = ingest(state)
    state = access_gate(state)
    state = classify(state, classifier)
    state = decide_route(state)

    # Unauthenticated callers never reach a model or tool.
    if state.access is not None and not state.access.authenticated:
        response = _escalate(state, reason="unauthenticated")
    elif state.route and state.route.disposition == Disposition.ESCALATE:
        response = _escalate(state, reason=state.route.reason)
    else:
        state = run_tool(state)
        if state.route and state.route.retrieve:
            state = retrieve(state)
            # No grounding found -> don't answer from nothing; escalate.
            if not state.retrieved:
                response = _escalate(state, reason="unverifiable")
                response.latency_ms = (time.perf_counter() - start) * 1000
                state.response = response
                return response
        candidate = composer.compose(state)
        # INDEPENDENT GUARDRAIL: vet the candidate before it ships.
        guard = guardrail.check(candidate)
        if guard.blocked:
            # A hallucinated offer/price (or tone breach) is escalated to a human.
            response = _escalate(
                state,
                reason="guardrail_block",
                extra={"violations": guard.violations, "blocked_candidate": candidate},
            )
            response.guardrail_action = "block"
            response.guardrail_violations = guard.violations
        else:
            response = _respond(state, guard.text, guard)

    response.latency_ms = (time.perf_counter() - start) * 1000
    state.response = response
    return response
