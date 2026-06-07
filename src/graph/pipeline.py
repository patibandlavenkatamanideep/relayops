"""Synchronous turn pipeline — the v1 orchestration.

Each step below is a pure-ish function over ``TurnState`` (a "node"). They run in
a fixed order for v1; this is the deliberate synchronous simplification from
DESIGN.md §3. Porting to LangGraph means registering these as nodes on a
``StateGraph`` with the same state object and adding the conditional edge at the
disposition branch — no node-body changes required.

    ingest -> access gate -> classify -> route -> [tool] -> compose -> respond

The guardrail layer (step 2), RAG (step 3), the fine-tuned classifier (step 4),
eval (step 5) and richer observability (step 6) slot in around these nodes.
"""

from __future__ import annotations

import time

from ..access import gate
from ..core import data
from ..core.models import (
    Action,
    AgentResponse,
    Disposition,
    Intent,
    Tier,
    ToolResult,
    TurnState,
)
from ..mcp import tools
from ..router import router
from ..router.classifier import BaselineClassifier, IntentClassifier


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


def compose(state: TurnState) -> str:
    """Turn results into a customer-facing reply.

    v1 uses deterministic templates so the slice runs without an LLM. The Tier 2
    frontier model plugs in here (it would draft the reply grounded in
    ``tool_results``); the guardrail in step 2 wraps this output before it ships.
    """
    assert state.classification is not None and state.route is not None
    intent = state.classification.intent

    if intent == Intent.RESET_DEVICE:
        res = state.tool_results[-1] if state.tool_results else None
        if res and res.ok:
            return (
                f"Done — I reset your {res.data['name']} and it's back online. "
                "Give it about 60 seconds to reconnect. Anything else?"
            )
        return (
            "I wasn't able to reset that device just now. Let me get a specialist "
            "to take a look."
        )

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


def respond_or_handoff(state: TurnState) -> AgentResponse:
    assert state.classification is not None and state.route is not None
    route = state.route

    if route.disposition == Disposition.ESCALATE or (
        state.access and not state.access.authenticated
    ):
        handoff = {
            "reason": "unauthenticated"
            if (state.access and not state.access.authenticated)
            else route.reason,
            "intent": state.classification.intent.value,
            "confidence": round(state.classification.confidence, 2),
            "message": state.text,
        }
        return AgentResponse(
            text=(
                "I'm connecting you with a specialist who can help with this — "
                "they'll have the full context of our chat."
            ),
            intent=state.classification.intent,
            tier=route.tier,
            disposition=Disposition.ESCALATE,
            escalated=True,
            tool_results=state.tool_results,
            handoff_context=handoff,
        )

    return AgentResponse(
        text=compose(state),
        intent=state.classification.intent,
        tier=route.tier,
        disposition=Disposition.RESPOND,
        tool_results=state.tool_results,
    )


# --- orchestrator --------------------------------------------------------------


def handle_turn(
    raw_text: str,
    auth_token: str | None = None,
    device_id: str | None = None,
    classifier: IntentClassifier | None = None,
) -> AgentResponse:
    """Run one customer turn through the full v1 pipeline."""
    classifier = classifier or BaselineClassifier()
    state = TurnState(raw_text=raw_text, auth_token=auth_token, device_id=device_id)

    start = time.perf_counter()
    state = ingest(state)
    state = access_gate(state)

    # Unauthenticated callers never reach a model or a tool.
    if state.access is not None and not state.access.authenticated:
        state.classification = classifier.classify(state.text)
        state.route = router.route(state.classification)
        response = respond_or_handoff(state)
    else:
        state = classify(state, classifier)
        state = decide_route(state)
        if state.route and state.route.disposition == Disposition.RESPOND:
            state = run_tool(state)
        response = respond_or_handoff(state)

    response.latency_ms = (time.perf_counter() - start) * 1000
    state.response = response
    return response
