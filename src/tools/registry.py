"""Registry of allowed tool names and their permission requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.models import Action
from . import scoped_tools
from .schema import ToolRequest, ToolResponse

ToolHandler = Callable[[ToolRequest], ToolResponse]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: ToolHandler
    envelope_action: str
    required_action: Action | None = None


REGISTRY: dict[str, ToolSpec] = {
    "customer.lookup": ToolSpec(
        name="customer.lookup",
        handler=scoped_tools.customer_lookup,
        envelope_action="account_lookup",
        required_action=Action.ACCOUNT_LOOKUP,
    ),
    "device.lookup": ToolSpec(
        name="device.lookup",
        handler=scoped_tools.device_lookup,
        envelope_action="device_lookup",
        required_action=Action.ACCOUNT_LOOKUP,
    ),
    "device.set_online_status": ToolSpec(
        name="device.set_online_status",
        handler=scoped_tools.device_set_online_status,
        envelope_action="device_reset",
        required_action=Action.DEVICE_RESET,
    ),
    "ticket.create_draft": ToolSpec(
        name="ticket.create_draft",
        handler=scoped_tools.ticket_create_draft,
        envelope_action="ticket_create_draft",
    ),
    "handoff.prepare": ToolSpec(
        name="handoff.prepare",
        handler=scoped_tools.handoff_prepare,
        envelope_action="handoff_prepare",
    ),
}


def get(tool_name: str) -> ToolSpec | None:
    return REGISTRY.get(tool_name)
