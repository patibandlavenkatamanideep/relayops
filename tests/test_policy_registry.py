"""Policy registry tests (v1.9).

The registry is the catalog of policy handles the broker stamps onto decisions.
These tests cover (1) the catalog's internal integrity and (2) the enforced
contract that every handle and matched_rule the broker actually emits is
registered — driven from real broker output, not a hand-copied list, so a new
decision path that invents an undocumented handle fails the suite.
"""

from __future__ import annotations

import unittest
from dataclasses import asdict

from src.core.models import (
    Classification,
    Disposition,
    Intent,
    RouteDecision,
    Tier,
    TurnState,
)
from src.observability.audit_ledger import AuditLedger
from src.graph.pipeline import handle_turn
from src.policy import registry
from src.router import policy_broker


def _broker_packet(msg: str, **kw) -> dict:
    led = AuditLedger()
    handle_turn(msg, audit=led, classifier_name="nb_calibrated", **kw)
    return led.records[0].to_dict()["broker_decision_packet"]


class RegistryIntegrityTests(unittest.TestCase):
    def test_validate_passes(self):
        registry.validate()  # raises on any malformed entry

    def test_handles_unique_and_nonempty(self):
        self.assertEqual(len(registry.REGISTRY), len(registry.all_handles()))
        for handle, entry in registry.REGISTRY.items():
            self.assertEqual(handle, entry.handle)
            self.assertTrue(entry.title and entry.description and entry.owner)
            self.assertIn(entry.disposition, registry.DISPOSITIONS)
            self.assertTrue(entry.rules)

    def test_handle_id_constants_resolve(self):
        # Every exported handle-id constant must be a real catalog entry.
        for const in (
            registry.DEVICE_RESET,
            registry.FAQ_ANSWER,
            registry.ACCOUNT_STATUS,
            registry.BILLING_REFUND,
            registry.BILLING_PLAN_CHANGE,
            registry.ACCOUNT_CHANGE,
            registry.SUPPORT_UNKNOWN,
            registry.GREETING,
            registry.AUTH_SCOPE,
            registry.GUARDRAIL,
            registry.FAIL_CLOSED,
        ):
            self.assertTrue(registry.exists(const), const)

    def test_version_matches_broker(self):
        self.assertEqual(registry.POLICY_VERSION, policy_broker.POLICY_VERSION)

    def test_unknown_handle_has_no_rules(self):
        self.assertFalse(registry.exists("not.a.real.handle"))
        self.assertEqual(registry.rules_for("not.a.real.handle"), ())


class BrokerEmitsRegisteredHandlesTests(unittest.TestCase):
    """For real broker output, every handle/rule pairing must be registered."""

    def _assert_registered(self, packet: dict):
        handle = packet["policy_handle"]
        rule = packet["matched_rule"]
        self.assertTrue(registry.exists(handle), f"unregistered handle: {handle}")
        self.assertIn(
            rule,
            registry.rules_for(handle),
            f"rule {rule!r} not listed under handle {handle!r}",
        )

    def test_message_driven_paths(self):
        cases = [
            ("hi there", {"auth_token": "tok_alice"}),  # greeting
            ("reset my router please", {"auth_token": "tok_alice"}),  # device reset
            ("how long does a device reset take?", {"auth_token": "tok_alice"}),  # faq
            ("I want a refund on my last bill", {"auth_token": "tok_alice"}),  # billing
            ("can you book me a flight?", {"auth_token": "tok_alice"}),  # unknown
            ("reset my router", {}),  # unauthenticated -> auth scope
            ("reset device dev_b1", {"auth_token": "tok_alice", "device_id": "dev_b1"}),  # scope
        ]
        seen = set()
        for msg, kw in cases:
            packet = _broker_packet(msg, **kw)
            self._assert_registered(packet)
            seen.add(packet["policy_handle"])
        # Sanity: these cases should exercise several distinct handles.
        self.assertGreaterEqual(len(seen), 4)

    def test_guardrail_block_path(self):
        state = TurnState(raw_text="x", turn_id="t_guard")
        state.classification = Classification(intent=Intent.BILLING, confidence=0.9)
        state.route = RouteDecision(
            tier=Tier.TIER2, disposition=Disposition.ESCALATE, reason="billing"
        )
        packet = policy_broker.decision_from_guardrail_block(state, ["unapproved_discount"])
        self._assert_registered(asdict(packet))
        self.assertEqual(packet.policy_handle, registry.GUARDRAIL)

    def test_fail_closed_path(self):
        state = TurnState(raw_text="x", turn_id="t_fail")
        packet = policy_broker.decision_from_fail_closed(
            state, reason_code="guardrail_unavailable", stage="guardrail"
        )
        self._assert_registered(asdict(packet))
        self.assertEqual(packet.policy_handle, registry.FAIL_CLOSED)

    def test_unverifiable_path(self):
        state = TurnState(raw_text="x", turn_id="t_unver")
        packet = policy_broker.decision_from_unverifiable(state)
        self._assert_registered(asdict(packet))
        self.assertEqual(packet.policy_handle, registry.FAQ_ANSWER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
