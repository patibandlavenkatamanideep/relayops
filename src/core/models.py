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
    BILLING = "billing"          # always escalates (touches money)
    GREETING = "greeting"
    UNKNOWN = "unknown"


class Action(str, Enum):
    """Capabilities the access gate can grant. Maps onto MCP tools."""

    ACCOUNT_LOOKUP = "account_lookup"
    DEVICE_RESET = "device_reset"
    SEND_LINK = "send_link"


class Tier(str, Enum):
    TIER1 = "tier1"   # cheap/small — easy, confident cases
    TIER2 = "tier2"   # frontier — hard / low-confidence / action cases
    NONE = "none"     # no model ran (e.g. straight to escalation)


class Disposition(str, Enum):
    RESPOND = "respond"
    ESCALATE = "escalate"


# --- account/device domain (what the MCP server is a front for) ----------------


@dataclass(frozen=True)
class Device:
    device_id: str
    owner_id: str          # customer_id this device belongs to
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
    reason: str = ""


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


@dataclass
class TurnState:
    """Mutable state threaded through the pipeline nodes (future graph state)."""

    raw_text: str
    auth_token: Optional[str] = None
    device_id: Optional[str] = None   # target device for a reset, if supplied

    text: str = ""                    # normalised input
    access: Optional[AccessContext] = None
    classification: Optional[Classification] = None
    route: Optional[RouteDecision] = None
    tool_results: list[ToolResult] = field(default_factory=list)
    response: Optional[AgentResponse] = None
