"""Shared domain types for the RelayOps v1 vertical slice.

Stdlib-only (dataclasses + enums) so the slice runs with zero external
dependencies. These types are the contract every pipeline node reads/writes;
when the pipeline is ported to a LangGraph ``StateGraph``, ``TurnState`` becomes
the graph state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Intent(str, Enum):
    """The small classified set for v1. ``RESET_DEVICE`` is the built slice."""

    RESET_DEVICE = "reset_device"
    DEVICE_STATUS = "device_status"
    DEVICE_FAQ = "device_faq"  # informational; answered from RAG with citations
    BILLING = "billing"  # always escalates (touches money)
    GREETING = "greeting"
    UNKNOWN = "unknown"


class Action(str, Enum):
    """Capabilities the access gate can grant. Maps onto MCP tools."""

    ACCOUNT_LOOKUP = "account_lookup"
    DEVICE_RESET = "device_reset"
    SEND_LINK = "send_link"


class Tier(str, Enum):
    TIER1 = "tier1"  # cheap/small — easy, confident cases
    TIER2 = "tier2"  # frontier — hard / low-confidence / action cases
    NONE = "none"  # no model ran (e.g. straight to escalation)


class Disposition(str, Enum):
    RESPOND = "respond"
    ESCALATE = "escalate"


# --- account/device domain (what the MCP server is a front for) ----------------


@dataclass(frozen=True)
class Device:
    device_id: str
    owner_id: str  # customer_id this device belongs to
    name: str
    online: bool


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    plan: str


# --- request/response plumbing -------------------------------------------------


@dataclass(frozen=True)
class AccessContext:
    """Result of the deterministic access gate. Binds a turn to exactly one
    authenticated customer and the actions they may take. No model can widen it."""

    authenticated: bool
    customer_id: Optional[str] = None
    allowed_actions: frozenset[Action] = frozenset()

    def may(self, action: Action) -> bool:
        return self.authenticated and action in self.allowed_actions


@dataclass(frozen=True)
class Classification:
    intent: Intent
    confidence: float


@dataclass(frozen=True)
class RouteDecision:
    tier: Tier
    disposition: Disposition
    tool: Optional[Action] = None
    retrieve: bool = False  # pull cited facts from RAG before composing
    reason: str = ""


@dataclass(frozen=True)
class PreActionIntentPacket:
    """Inspectable model/router proposal before any tool is allowed to run."""

    turn_id: str
    user_request: str
    model_interpretation: str
    requested_action: str
    target_resource: str
    policy_handle: str
    evidence_quote: str
    confidence: float
    ambiguity: str = ""
    proposed_safe_response: str = ""


@dataclass(frozen=True)
class BrokerDecisionPacket:
    """The deterministic broker's source-of-truth decision for the turn."""

    turn_id: str
    decision: str  # allow | block | escalate | ask_clarification
    policy_version: str
    policy_handle: str
    matched_rule: str
    reason_code: str
    missing_evidence: list[str] = field(default_factory=list)
    owner: str = ""
    human_queue: str = ""
    allowed_next_actions: list[str] = field(default_factory=list)
    forbidden_next_actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FinalReplyPacket:
    """Final customer text plus the broker decision it was derived from."""

    turn_id: str
    source: str
    broker_decision: str
    policy_handle: str
    text: str
    guardrail_action: str = "not_reached"
    guardrail_violations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class AgentResponse:
    text: str
    intent: Intent
    tier: Tier
    disposition: Disposition
    escalated: bool = False
    tool_results: list[ToolResult] = field(default_factory=list)
    handoff_context: Optional[dict[str, Any]] = None
    latency_ms: float = 0.0
    guardrail_action: str = "pass"  # pass | redact | block
    guardrail_violations: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    action_envelopes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TurnState:
    """Mutable state threaded through the pipeline nodes (future graph state)."""

    raw_text: str
    auth_token: Optional[str] = None
    device_id: Optional[str] = None  # target device for a reset, if supplied

    text: str = ""  # normalised input
    access: Optional[AccessContext] = None
    classification: Optional[Classification] = None
    route: Optional[RouteDecision] = None
    turn_id: str = ""
    pre_action_intent: Optional[PreActionIntentPacket] = None
    broker_decision: Optional[BrokerDecisionPacket] = None
    final_reply: Optional[FinalReplyPacket] = None
    tool_results: list[ToolResult] = field(default_factory=list)
    retrieved: list[Any] = field(default_factory=list)  # RetrievedChunk list
    action_envelopes: list[Any] = field(default_factory=list)  # ActionEnvelope list
    response: Optional[AgentResponse] = None
