"""Tests for the support-ticket batch runner (v1.5).

Asserts the batch processes every ticket, the outcome buckets partition the
queue, an audit record is written per ticket, the safety invariants hold
(no unsafe auto-action, no billing escape), and the rates / time-saved math.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.observability.audit_store import AuditStore
from src.workflows.ticket_runner import (
    DEFAULT_TICKETS,
    MINUTES_PER_TICKET,
    load_tickets,
    run_batch,
    token_for,
)


class TokenMappingTests(unittest.TestCase):
    def test_known_customer_maps_to_token(self):
        self.assertEqual(token_for("cust_alice"), "tok_alice")
        self.assertEqual(token_for("cust_bob"), "tok_bob")

    def test_null_customer_is_unauthenticated(self):
        self.assertIsNone(token_for(None))
        self.assertIsNone(token_for(""))


class TicketDatasetTests(unittest.TestCase):
    def test_sample_tickets_load(self):
        tickets = load_tickets(DEFAULT_TICKETS)
        self.assertGreaterEqual(len(tickets), 50)
        for t in tickets:
            self.assertIn("ticket_id", t)
            self.assertIn("message", t)


class BatchRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tickets = load_tickets(DEFAULT_TICKETS)
        # keyword classifier: fast + deterministic for tests
        cls.result = run_batch(cls.tickets, classifier_name="keyword")

    def test_every_ticket_processed(self):
        self.assertEqual(self.result.total, len(self.tickets))
        self.assertEqual(len(self.result.rows), len(self.tickets))

    def test_one_audit_record_per_ticket(self):
        self.assertEqual(len(self.result.records), len(self.tickets))

    def test_outcomes_partition_the_queue(self):
        r = self.result
        self.assertEqual(r.auto_resolved + r.human_handoff + r.blocked_unsafe, r.total)

    def test_rates_sum_to_one(self):
        r = self.result
        total_rate = r.auto_resolution_rate + r.human_escalation_rate + r.safe_block_rate
        self.assertAlmostEqual(total_rate, 1.0, places=6)

    def test_safety_invariants_hold(self):
        self.assertEqual(self.result.unsafe_auto_action, 0)
        self.assertEqual(self.result.billing_escape, 0)

    def test_billing_tickets_never_auto_resolve(self):
        for row in self.result.rows:
            if row["expected_category"] == "billing":
                self.assertNotEqual(
                    row["outcome"],
                    "auto_resolved",
                    f"{row['ticket_id']} auto-resolved a billing ticket",
                )

    def test_time_saved_is_consistent(self):
        self.assertEqual(
            self.result.manual_minutes_saved, self.result.auto_resolved * MINUTES_PER_TICKET
        )

    def test_summary_has_business_metrics(self):
        s = self.result.summary()
        for key in (
            "tickets_processed",
            "auto_resolved",
            "auto_resolution_rate",
            "human_escalation_rate",
            "safe_block_rate",
            "manual_minutes_saved",
            "unsafe_auto_action",
            "billing_escape",
        ):
            self.assertIn(key, s)


class BatchPersistenceTests(unittest.TestCase):
    def test_run_persists_audit_records(self):
        tickets = load_tickets(DEFAULT_TICKETS)[:10]
        with tempfile.TemporaryDirectory() as d:
            store = AuditStore(Path(d) / "batch.sqlite3")
            result = run_batch(tickets, classifier_name="keyword", store=store)
            self.assertEqual(store.count(), result.total)
            stats = store.stats()
            self.assertEqual(stats["unsafe_auto_action"], 0)
            self.assertEqual(stats["billing_escape"], 0)
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
