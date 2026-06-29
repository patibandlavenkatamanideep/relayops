"""Customer datastore tests (v2.0).

The store backs auth-token resolution, customer/device lookup, and the
reversible device-state write the reset tool uses. These tests cover the scoped
accessors, seeding semantics, the scope boundary (a device is owned by exactly
one customer), and durability when pointed at a file.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.core.customer_store import CustomerStore
from src.core.models import Customer, Device

_CUSTOMERS = {
    "cust_alice": Customer("cust_alice", "Alice", plan="Unlimited 5G"),
    "cust_bob": Customer("cust_bob", "Bob", plan="Basic"),
}
_DEVICES = {
    "dev_a1": Device("dev_a1", owner_id="cust_alice", name="Alice's Router", online=False),
    "dev_b1": Device("dev_b1", owner_id="cust_bob", name="Bob's Router", online=True),
}
_TOKENS = {"tok_alice": "cust_alice", "tok_bob": "cust_bob"}


def _seeded(db_path: str = ":memory:") -> CustomerStore:
    s = CustomerStore(db_path)
    s.seed(customers=_CUSTOMERS, devices=_DEVICES, tokens=_TOKENS)
    return s


class AccessorTests(unittest.TestCase):
    def setUp(self):
        self.store = _seeded()

    def tearDown(self):
        self.store.close()

    def test_resolve_token(self):
        self.assertEqual(self.store.resolve_token("tok_alice"), "cust_alice")
        self.assertIsNone(self.store.resolve_token("nope"))
        self.assertIsNone(self.store.resolve_token(None))

    def test_get_customer(self):
        c = self.store.get_customer("cust_bob")
        self.assertEqual(c.name, "Bob")
        self.assertEqual(c.plan, "Basic")
        self.assertIsNone(self.store.get_customer("cust_ghost"))

    def test_devices_scoped_to_owner(self):
        alice = {d.device_id for d in self.store.devices_for("cust_alice")}
        self.assertEqual(alice, {"dev_a1"})
        # Bob's device never appears under Alice — the scope boundary.
        self.assertNotIn("dev_b1", alice)

    def test_set_device_online_roundtrip(self):
        self.assertFalse(self.store.get_device("dev_a1").online)
        self.store.set_device_online("dev_a1", True)
        self.assertTrue(self.store.get_device("dev_a1").online)

    def test_set_unknown_device_raises(self):
        with self.assertRaises(KeyError):
            self.store.set_device_online("dev_missing", True)


class SeedingTests(unittest.TestCase):
    def test_is_empty_then_seeded(self):
        s = CustomerStore(":memory:")
        self.assertTrue(s.is_empty())
        s.seed(customers=_CUSTOMERS, devices=_DEVICES, tokens=_TOKENS)
        self.assertFalse(s.is_empty())
        s.close()

    def test_seed_is_idempotent_and_preserves_writes(self):
        s = _seeded()
        s.set_device_online("dev_a1", True)
        # Re-seeding must not clobber the applied reset.
        s.seed(customers=_CUSTOMERS, devices=_DEVICES, tokens=_TOKENS)
        self.assertTrue(s.get_device("dev_a1").online)
        s.close()


class DurabilityTests(unittest.TestCase):
    def test_file_backed_store_persists(self):
        path = os.path.join(tempfile.mkdtemp(), "customers.sqlite3")
        s1 = _seeded(path)
        s1.set_device_online("dev_a1", True)
        s1.close()

        s2 = CustomerStore(path)  # reopen; seed-if-empty would no-op here
        self.assertFalse(s2.is_empty())
        self.assertTrue(s2.get_device("dev_a1").online)
        self.assertEqual(s2.resolve_token("tok_bob"), "cust_bob")
        s2.close()


class DefaultStoreTests(unittest.TestCase):
    def test_data_module_delegates_to_store(self):
        from src.core import data

        self.assertEqual(data.resolve_token("tok_alice"), "cust_alice")
        self.assertEqual(data.get_customer("cust_alice").name, "Alice")
        self.assertEqual({d.device_id for d in data.devices_for("cust_alice")}, {"dev_a1", "dev_a2"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
