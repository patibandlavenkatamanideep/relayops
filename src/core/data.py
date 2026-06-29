"""Account/device data — the backing service the MCP server fronts.

Two customers exist so the slice can demonstrate the load-bearing security
property: customer A can never reach customer B's device, even if the model is
told to. In production this module is a real account/device microservice; the
interface (lookup by id, scoped to an owner) stays the same.

v2.0 moves the data itself behind a SQLite datastore (``CustomerStore``). The
dicts below are now only the canonical *seed* — the live reads and the
(reversible) device-state writes go through the store. The accessor functions
keep their original signatures, so the gate, MCP server, and pipeline are
unchanged: they still trust whatever the scoped store returns.
"""

from __future__ import annotations

from .customer_store import DEFAULT_DB_PATH, CustomerStore
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


def _build_store() -> CustomerStore:
    """The process-default store, seeded from the canonical fixture above.

    For the in-memory default this seeds a fresh store every process (v1.x
    semantics). For a durable ``RELAYOPS_CUSTOMER_DB`` it seeds only when empty,
    so a previously applied device reset survives a restart.
    """
    s = CustomerStore(DEFAULT_DB_PATH)
    if s.is_empty():
        s.seed(customers=_CUSTOMERS, devices=_DEVICES, tokens=_TOKENS)
    return s


_STORE = _build_store()


def store() -> CustomerStore:
    """Return the process-default customer store (for inspection / CLI)."""
    return _STORE


def resolve_token(token: str | None) -> str | None:
    """Map an auth token to a customer_id, or None if invalid."""
    return _STORE.resolve_token(token)


def get_customer(customer_id: str) -> Customer | None:
    return _STORE.get_customer(customer_id)


def get_device(device_id: str) -> Device | None:
    return _STORE.get_device(device_id)


def devices_for(customer_id: str) -> list[Device]:
    return _STORE.devices_for(customer_id)


def set_device_online(device_id: str, online: bool) -> None:
    """Apply a (reversible) device state change. The reset uses this."""
    _STORE.set_device_online(device_id, online=online)
