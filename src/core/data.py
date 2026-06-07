"""In-memory account/device store — the backing service the MCP server fronts.

Two customers exist so the slice can demonstrate the load-bearing security
property: customer A can never reach customer B's device, even if the model is
told to. In production this module is a real account/device microservice; the
interface (lookup by id, scoped to an owner) stays the same.
"""

from __future__ import annotations

from .models import Customer, Device

# token -> customer_id. Stands in for real authentication (session/JWT).
_TOKENS: dict[str, str] = {
    "tok_alice": "cust_alice",
    "tok_bob": "cust_bob",
}

_CUSTOMERS: dict[str, Customer] = {
    "cust_alice": Customer("cust_alice", "Alice", plan="Unlimited 5G"),
    "cust_bob": Customer("cust_bob", "Bob", plan="Basic"),
}

_DEVICES: dict[str, Device] = {
    "dev_a1": Device("dev_a1", owner_id="cust_alice", name="Alice's Router", online=False),
    "dev_a2": Device("dev_a2", owner_id="cust_alice", name="Alice's Phone", online=True),
    "dev_b1": Device("dev_b1", owner_id="cust_bob", name="Bob's Router", online=True),
}


def resolve_token(token: str | None) -> str | None:
    """Map an auth token to a customer_id, or None if invalid."""
    if not token:
        return None
    return _TOKENS.get(token)


def get_customer(customer_id: str) -> Customer | None:
    return _CUSTOMERS.get(customer_id)


def get_device(device_id: str) -> Device | None:
    return _DEVICES.get(device_id)


def devices_for(customer_id: str) -> list[Device]:
    return [d for d in _DEVICES.values() if d.owner_id == customer_id]


def set_device_online(device_id: str, online: bool) -> None:
    """Apply a (reversible) device state change. The reset uses this."""
    d = _DEVICES[device_id]
    _DEVICES[device_id] = Device(d.device_id, d.owner_id, d.name, online=online)
