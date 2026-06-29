"""Typed request/response contracts for the v2.3 tool boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ..actions import ActionEnvelope
from ..core.models import AccessContext, BrokerDecisionPacket


class ToolStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED = "refused"
    REPLAYED = "replayed"


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolRequest:
    """A single tool execution attempt at the controlled boundary."""

    tool_name: str
    turn_id: str
    caller: str
    customer_id: str
    target_resource: str
    args: dict[str, Any]
    context: AccessContext
    broker_decision: BrokerDecisionPacket
    envelope: ActionEnvelope


@dataclass
class ToolResponse:
    status: ToolStatus
    data: dict[str, Any] = field(default_factory=dict)
    error: ToolError | None = None

    @property
    def ok(self) -> bool:
        return self.status in (ToolStatus.SUCCEEDED, ToolStatus.REPLAYED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "ok": self.ok,
            "data": dict(self.data),
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class ToolAuditRecord:
    """Minimal execution evidence for one tool boundary attempt."""

    turn_id: str
    tool_name: str
    caller: str
    customer_id: str
    target_resource: str
    envelope_action_id: str
    envelope_status: str
    broker_decision: str
    status: str
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
