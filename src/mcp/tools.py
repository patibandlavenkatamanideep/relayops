"""Scoped tool implementations — the server side of the MCP boundary.

These are the *real* tool bodies. In v1 they are called in-process; a thin MCP
transport wrapper (``server.py``, deferred until the `mcp` package is wired) will
expose exactly these functions as MCP tools. Either way the rule is the same and
load-bearing:

    Scope is enforced HERE, server-side, against the AccessContext — never by
    trusting what the model asked for.

That is the demonstrable property: a prompt-injection that asks to reset another
customer's device is refused by this layer, not by hoping the model behaves.
"""

from __future__ import annotations

from typing import Callable

from ..core import data
from ..core.models import AccessContext, Action, ToolResult


def account_lookup(ctx: AccessContext) -> ToolResult:
    """Read-only, implicitly scoped to the authenticated customer."""
    if not ctx.may(Action.ACCOUNT_LOOKUP) or ctx.customer_id is None:
        return ToolResult(ok=False, error="not_authorized")
    customer = data.get_customer(ctx.customer_id)
    if customer is None:
        return ToolResult(ok=False, error="not_found")
    devices = data.devices_for(ctx.customer_id)
    return ToolResult(
        ok=True,
        data={
            "customer_id": customer.customer_id,
            "name": customer.name,
            "plan": customer.plan,
            "devices": [
                {"device_id": d.device_id, "name": d.name, "online": d.online} for d in devices
            ],
        },
    )


def device_reset(ctx: AccessContext, device_id: str) -> ToolResult:
    """Reversible + idempotent reset. Refuses devices the caller doesn't own."""
    if not ctx.may(Action.DEVICE_RESET) or ctx.customer_id is None:
        return ToolResult(ok=False, error="not_authorized")
    device = data.get_device(device_id)
    if device is None:
        return ToolResult(ok=False, error="not_found")
    # The security check: ownership is verified server-side against the gate's
    # context, regardless of what the model/request claims.
    if device.owner_id != ctx.customer_id:
        return ToolResult(ok=False, error="scope_violation")
    # Idempotent reversible reset: bounce the device back online.
    data.set_device_online(device_id, online=True)
    return ToolResult(
        ok=True,
        data={"device_id": device_id, "name": device.name, "online": True, "reset": True},
    )


def send_link(ctx: AccessContext, link_type: str) -> ToolResult:
    """Reversible: send a self-service link to the authenticated customer."""
    if not ctx.may(Action.SEND_LINK) or ctx.customer_id is None:
        return ToolResult(ok=False, error="not_authorized")
    allowed = {"reset_guide", "app_download", "coverage_map"}
    if link_type not in allowed:
        return ToolResult(ok=False, error="unknown_link_type")
    return ToolResult(ok=True, data={"link_type": link_type, "sent_to": ctx.customer_id})


# Tool registry — the explicit, small surface the agent (MCP client) may call.
REGISTRY: dict[Action, Callable[..., ToolResult]] = {
    Action.ACCOUNT_LOOKUP: account_lookup,
    Action.DEVICE_RESET: device_reset,
    Action.SEND_LINK: send_link,
}
