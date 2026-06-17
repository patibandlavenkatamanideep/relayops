"""Request/response models for the RelayOps API.

These are the wire contract. They deliberately mirror the existing domain types
(``AgentResponse``, the audit record) without leaking enums or dataclasses, so
the HTTP surface can evolve independently of the pipeline internals.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class TurnRequest(BaseModel):
    """One customer turn.

    ``token`` is the *authority* for scope — the deterministic access gate
    resolves the customer from it, and nothing downstream can widen that scope.
    ``customer_id`` is accepted as advisory metadata only; it is NOT used to
    grant access, by design (a caller cannot assert who they are). ``device_id``
    optionally targets a specific device (e.g. to exercise scope refusal).
    """

    message: str
    token: Optional[str] = None
    customer_id: Optional[str] = None
    device_id: Optional[str] = None


class ToolResultModel(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class TurnResponse(BaseModel):
    turn_id: str
    reply: str
    intent: str
    tier: str
    disposition: str
    escalated: bool
    pre_action_intent_packet: dict[str, Any]
    broker_decision_packet: dict[str, Any]
    guardrail_result: dict[str, Any]
    audit_record: dict[str, Any]
    trace: dict[str, Any]
    tool_results: list[ToolResultModel] = Field(default_factory=list)
    handoff: Optional[dict[str, Any]] = None


class HealthResponse(BaseModel):
    ok: bool = True


class ErrorResponse(BaseModel):
    error: str


class HandoffsResponse(BaseModel):
    handoffs: list[dict[str, Any]] = Field(default_factory=list)
