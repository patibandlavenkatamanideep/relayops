"""SQLite customer / auth / device datastore (v2.0).

Through v1.x the account world lived in hardcoded module dicts (`core/data.py`).
v2.0 puts it behind a real datastore: a small SQLite schema with three tables —
``customers``, ``devices``, and ``auth_tokens`` — and the same scoped accessor
surface the gate and MCP server already trust. The interface is unchanged
(``resolve_token`` / ``get_customer`` / ``get_device`` / ``devices_for`` /
``set_device_online``), so no downstream stage re-derives permissions; they still
only read what this store returns.

Like ``observability/audit_store.py``, SQLite is an implementation detail — the
schema is the contract; a real deployment points the same accessors at a managed
Postgres. The default store is in-memory and re-seeded per process (preserving
the v1.x semantics: a device reset persists within a run, not across runs). Point
``RELAYOPS_CUSTOMER_DB`` at a path to get a durable store that seeds once and
then survives the process.

CLI:
    python3 -m src.core.customer_store --customers
    python3 -m src.core.customer_store --devices
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .models import Customer, Device

# Default to an in-memory store so the canonical fixture is re-seeded each
# process — identical to the old module-dict behaviour. Set RELAYOPS_CUSTOMER_DB
# to a file path for a durable customer/auth store.
DEFAULT_DB_PATH = os.environ.get("RELAYOPS_CUSTOMER_DB", ":memory:")

_CREATE = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    plan        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    owner_id  TEXT NOT NULL,
    name      TEXT NOT NULL,
    online    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token       TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL
);
"""


class CustomerStore:
    """Scoped account/device datastore. One connection, shareable across
    Streamlit reruns (``check_same_thread=False``)."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            parent = Path(self.db_path).parent
            if parent and not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_CREATE)
        self.conn.commit()

    # --- seeding --------------------------------------------------------------

    def is_empty(self) -> bool:
        n = self.conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()["n"]
        return n == 0

    def seed(
        self,
        *,
        customers: dict[str, Customer],
        devices: dict[str, Device],
        tokens: dict[str, str],
    ) -> None:
        """Insert the canonical fixture. Idempotent: ``INSERT OR IGNORE`` leaves
        existing rows (e.g. an applied device reset in a durable store) intact."""
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO customers (customer_id, name, plan) VALUES (?, ?, ?)",
                [(c.customer_id, c.name, c.plan) for c in customers.values()],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO devices (device_id, owner_id, name, online) VALUES (?, ?, ?, ?)",
                [(d.device_id, d.owner_id, d.name, int(d.online)) for d in devices.values()],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO auth_tokens (token, customer_id) VALUES (?, ?)",
                list(tokens.items()),
            )

    # --- scoped reads (the surface the gate + MCP server trust) ---------------

    def resolve_token(self, token: str | None) -> str | None:
        if not token:
            return None
        row = self.conn.execute(
            "SELECT customer_id FROM auth_tokens WHERE token = ?", (token,)
        ).fetchone()
        return row["customer_id"] if row else None

    def get_customer(self, customer_id: str) -> Customer | None:
        row = self.conn.execute(
            "SELECT customer_id, name, plan FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        return Customer(row["customer_id"], row["name"], row["plan"]) if row else None

    def get_device(self, device_id: str) -> Device | None:
        row = self.conn.execute(
            "SELECT device_id, owner_id, name, online FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return _device_from_row(row) if row else None

    def devices_for(self, customer_id: str) -> list[Device]:
        rows = self.conn.execute(
            "SELECT device_id, owner_id, name, online FROM devices WHERE owner_id = ? "
            "ORDER BY device_id",
            (customer_id,),
        ).fetchall()
        return [_device_from_row(r) for r in rows]

    # --- scoped writes --------------------------------------------------------

    def set_device_online(self, device_id: str, online: bool) -> None:
        """Apply a (reversible) device state change. The reset tool uses this."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE devices SET online = ? WHERE device_id = ?",
                (int(online), device_id),
            )
        if cur.rowcount == 0:
            # Match the old dict semantics: mutating an unknown device is a bug.
            raise KeyError(device_id)

    # --- listing (CLI / inspection) -------------------------------------------

    def list_customers(self) -> list[Customer]:
        rows = self.conn.execute(
            "SELECT customer_id, name, plan FROM customers ORDER BY customer_id"
        ).fetchall()
        return [Customer(r["customer_id"], r["name"], r["plan"]) for r in rows]

    def list_devices(self) -> list[Device]:
        rows = self.conn.execute(
            "SELECT device_id, owner_id, name, online FROM devices ORDER BY device_id"
        ).fetchall()
        return [_device_from_row(r) for r in rows]

    def close(self) -> None:
        self.conn.close()


def _device_from_row(row: sqlite3.Row) -> Device:
    return Device(row["device_id"], row["owner_id"], row["name"], online=bool(row["online"]))


def _main() -> None:
    import argparse

    from . import data  # seeds the default store

    parser = argparse.ArgumentParser(description="Inspect the RelayOps customer datastore")
    parser.add_argument("--customers", action="store_true", help="list customers")
    parser.add_argument("--devices", action="store_true", help="list devices")
    args = parser.parse_args()

    store = data.store()
    if args.customers or not args.devices:
        print(f"Customers ({store.db_path}):")
        for c in store.list_customers():
            print(f"  {c.customer_id}  {c.name}  ({c.plan})")
    if args.devices or not args.customers:
        print("Devices:")
        for d in store.list_devices():
            state = "online" if d.online else "offline"
            print(f"  {d.device_id}  owner={d.owner_id}  {d.name}  [{state}]")


if __name__ == "__main__":
    _main()
