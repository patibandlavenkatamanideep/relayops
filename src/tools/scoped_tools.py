"""Scoped tool handlers behind the v2.3 server boundary."""

from __future__ import annotations

from ..core import data
from ..core.models import Action
from .schema import ToolError, ToolRequest, ToolResponse, ToolStatus


def _refused(code: str, message: str = "") -> ToolResponse:
    return ToolResponse(status=ToolStatus.REFUSED, error=ToolError(code, message))


def _failed(code: str, message: str = "") -> ToolResponse:
    return ToolResponse(status=ToolStatus.FAILED, error=ToolError(code, message))


def customer_lookup(req: ToolRequest) -> ToolResponse:
    """Read-only lookup, implicitly scoped to the authenticated customer."""

    ctx = req.context
    if not ctx.may(Action.ACCOUNT_LOOKUP) or ctx.customer_id is None:
        return _refused("not_authorized")
    customer = data.get_customer(ctx.customer_id)
    if customer is None:
        return _failed("not_found")
    devices = data.devices_for(ctx.customer_id)
    return ToolResponse(
        status=ToolStatus.SUCCEEDED,
        data={
            "customer_id": customer.customer_id,
            "name": customer.name,
            "plan": customer.plan,
            "devices": [
                {"device_id": d.device_id, "name": d.name, "online": d.online} for d in devices
            ],
        },
    )


def device_lookup(req: ToolRequest) -> ToolResponse:
    """Read a single device only when it belongs to the scoped customer."""

    ctx = req.context
    if not ctx.may(Action.ACCOUNT_LOOKUP) or ctx.customer_id is None:
        return _refused("not_authorized")
    device_id = str(req.args.get("device_id") or req.target_resource)
    device = data.get_device(device_id)
    if device is None:
        return _failed("not_found")
    if device.owner_id != ctx.customer_id:
        return _refused("scope_violation")
    return ToolResponse(
        status=ToolStatus.SUCCEEDED,
        data={
            "device_id": device.device_id,
            "owner_id": device.owner_id,
            "name": device.name,
            "online": device.online,
        },
    )


def device_set_online_status(req: ToolRequest) -> ToolResponse:
    """Set an owned device online/offline; used by the reset flow."""

    ctx = req.context
    if not ctx.may(Action.DEVICE_RESET) or ctx.customer_id is None:
        return _refused("not_authorized")
    device_id = str(req.args.get("device_id") or req.target_resource)
    online = bool(req.args.get("online", True))
    device = data.get_device(device_id)
    if device is None:
        return _failed("not_found")
    if device.owner_id != ctx.customer_id:
        return _refused("scope_violation")
    data.set_device_online(device_id, online=online)
    return ToolResponse(
        status=ToolStatus.SUCCEEDED,
        data={
            "device_id": device_id,
            "name": device.name,
            "online": online,
            "reset": online,
        },
    )


def ticket_create_draft(req: ToolRequest) -> ToolResponse:
    """Prepare a non-sent support ticket draft."""

    title = str(req.args.get("title") or "Support follow-up")
    body = str(req.args.get("body") or "")
    return ToolResponse(
        status=ToolStatus.SUCCEEDED,
        data={
            "draft_id": f"draft_{req.turn_id}",
            "customer_id": req.customer_id,
            "title": title,
            "body": body,
            "sent": False,
        },
    )


def handoff_prepare(req: ToolRequest) -> ToolResponse:
    """Prepare human handoff context without contacting an external queue."""

    queue = str(req.args.get("queue") or req.broker_decision.human_queue or "general_support")
    reason = str(req.args.get("reason") or req.broker_decision.reason_code)
    return ToolResponse(
        status=ToolStatus.SUCCEEDED,
        data={
            "handoff_id": f"handoff_{req.turn_id}",
            "customer_id": req.customer_id,
            "queue": queue,
            "reason": reason,
            "prepared": True,
        },
    )
