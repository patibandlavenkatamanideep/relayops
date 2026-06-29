"""External action envelope tests (v2.2).

Covers the envelope lifecycle, the executor's success/refusal/replay paths, the
idempotency ledger, and the end-to-end wiring: a device reset through the
pipeline carries a succeeded envelope, and a second identical reset is replayed
(the side effect is not run twice).
"""

from __future__ import annotations

import unittest

from src.access import gate
from src.actions import (
    REFUSED,
    REPLAYED,
    SUCCEEDED,
    ActionEnvelope,
    IdempotencyLedger,
    execute_action,
)
from src.actions import executor as executor_mod
from src.core.models import ToolResult
from src.graph.pipeline import handle_turn
from src.mcp import tools


class EnvelopeModelTests(unittest.TestCase):
    def test_opened_has_idempotency_key_without_turn_id(self):
        e = ActionEnvelope.opened(
            turn_id="turn_1",
            customer_id="cust_alice",
            action="device_reset",
            target_resource="dev_a1",
            policy_handle="device.reset.allowed_if_scoped",
            blast_radius="low",
            reversibility="yes",
        )
        # Same logical action across turns shares a key (turn id excluded).
        self.assertEqual(e.idempotency_key, "cust_alice:device_reset:dev_a1")
        self.assertEqual(e.status, "pending")

    def test_refusal_vs_failure(self):
        e = ActionEnvelope.opened(
            turn_id="t", customer_id="c", action="device_reset", target_resource="d",
            policy_handle="h", blast_radius="low", reversibility="yes",
        )
        e.fail("scope_violation")
        self.assertEqual(e.status, REFUSED)
        e2 = ActionEnvelope.opened(
            turn_id="t", customer_id="c", action="device_reset", target_resource="d",
            policy_handle="h", blast_radius="low", reversibility="yes",
        )
        e2.fail("not_found")
        self.assertEqual(e2.status, "failed")


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.ctx = gate.authenticate("tok_alice")
        self.ledger = IdempotencyLedger()

    def _reset(self, target="dev_a1"):
        return execute_action(
            self.ctx,
            turn_id="turn_x",
            action="device_reset",
            target_resource=target,
            policy_handle="device.reset.allowed_if_scoped",
            blast_radius="low",
            reversibility="yes",
            tool=lambda: tools.device_reset(self.ctx, target),
            ledger=self.ledger,
        )

    def test_success_records_and_remembers(self):
        env, result = self._reset()
        self.assertTrue(result.ok)
        self.assertEqual(env.status, SUCCEEDED)
        self.assertTrue(env.completed_at)
        self.assertIsNotNone(self.ledger.get(env.idempotency_key))

    def test_replay_does_not_run_tool_again(self):
        self._reset()
        calls = {"n": 0}

        def counting_tool():
            calls["n"] += 1
            return tools.device_reset(self.ctx, "dev_a1")

        env, result = execute_action(
            self.ctx,
            turn_id="turn_y",
            action="device_reset",
            target_resource="dev_a1",
            policy_handle="device.reset.allowed_if_scoped",
            blast_radius="low",
            reversibility="yes",
            tool=counting_tool,
            ledger=self.ledger,
        )
        self.assertEqual(env.status, REPLAYED)
        self.assertTrue(result.ok)
        self.assertEqual(calls["n"], 0, "replay must not invoke the tool")

    def test_refusal_not_remembered(self):
        # Alice resetting Bob's device is refused; nothing is cached for replay.
        env, result = self._reset(target="dev_b1")
        self.assertFalse(result.ok)
        self.assertEqual(env.status, REFUSED)
        self.assertIsNone(self.ledger.get(env.idempotency_key))

    def test_failed_tool_not_remembered(self):
        env, result = execute_action(
            self.ctx,
            turn_id="t",
            action="device_reset",
            target_resource="dev_missing",
            policy_handle="h",
            blast_radius="low",
            reversibility="yes",
            tool=lambda: ToolResult(ok=False, error="not_found"),
            ledger=self.ledger,
        )
        self.assertEqual(env.status, "failed")
        self.assertIsNone(self.ledger.get(env.idempotency_key))


class PipelineWiringTests(unittest.TestCase):
    def setUp(self):
        # Isolate the process-default ledger so order doesn't leak across tests.
        executor_mod.default_ledger.reset()

    def test_reset_turn_carries_succeeded_envelope(self):
        resp = handle_turn("my router isn't working, can you reset it?", auth_token="tok_alice")
        self.assertEqual(len(resp.action_envelopes), 1)
        env = resp.action_envelopes[0]
        self.assertEqual(env["action"], "device_reset")
        self.assertEqual(env["status"], SUCCEEDED)
        self.assertEqual(env["policy_handle"], "device.reset.allowed_if_scoped")
        self.assertTrue(env["action_id"].startswith("act_"))

    def test_escalated_turn_has_no_envelope(self):
        resp = handle_turn("I want a refund on my last bill", auth_token="tok_alice")
        self.assertEqual(resp.action_envelopes, [])

    def test_second_identical_reset_is_replayed(self):
        handle_turn("reset my router", auth_token="tok_alice")
        resp2 = handle_turn("reset my router again please", auth_token="tok_alice")
        self.assertEqual(resp2.action_envelopes[0]["status"], REPLAYED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
