"""MCP-style tool server boundary tests (v2.3)."""

from __future__ import annotations

import unittest

from src.access import gate
from src.actions import REFUSED, REPLAYED, SUCCEEDED, ActionEnvelope, IdempotencyLedger
from src.actions import executor as executor_mod
from src.core.models import BrokerDecisionPacket
from src.graph.pipeline import handle_turn
from src.tools import ToolRequest, ToolResponse, ToolServer, ToolStatus, default_tool_server
from src.tools import registry


def _broker(turn_id: str = "turn_tool", decision: str = "allow") -> BrokerDecisionPacket:
    return BrokerDecisionPacket(
        turn_id=turn_id,
        decision=decision,
        policy_version="test",
        policy_handle="device.reset.allowed_if_scoped",
        matched_rule="test_rule",
        reason_code="policy_allow" if decision == "allow" else "policy_denied",
        owner="device_support",
        allowed_next_actions=["call_scoped_tool"] if decision == "allow" else [],
        forbidden_next_actions=[] if decision == "allow" else ["run_tool"],
    )


class ToolServerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ledger = IdempotencyLedger()
        self.server = ToolServer(self.ledger)

    def _request(
        self,
        *,
        tool_name: str = "device.set_online_status",
        token: str | None = "tok_alice",
        customer_id: str = "cust_alice",
        target: str = "dev_a1",
        envelope_action: str = "device_reset",
        decision: str = "allow",
        args: dict | None = None,
    ) -> ToolRequest:
        turn_id = "turn_tool"
        ctx = gate.authenticate(token)
        envelope = ActionEnvelope.opened(
            turn_id=turn_id,
            customer_id=customer_id,
            action=envelope_action,
            target_resource=target,
            policy_handle="device.reset.allowed_if_scoped",
            blast_radius="low",
            reversibility="yes",
        )
        return ToolRequest(
            tool_name=tool_name,
            turn_id=turn_id,
            caller=ctx.customer_id or "anonymous",
            customer_id=customer_id,
            target_resource=target,
            args=args if args is not None else {"device_id": target, "online": True},
            context=ctx,
            broker_decision=_broker(turn_id, decision),
            envelope=envelope,
        )

    def test_unauthenticated_request_is_rejected(self):
        req = self._request(token=None, customer_id="")
        res = self.server.execute(req)
        self.assertEqual(res.status, ToolStatus.REFUSED)
        self.assertEqual(res.error.code, "unauthenticated")
        self.assertEqual(req.envelope.status, REFUSED)

    def test_cross_customer_request_is_rejected(self):
        req = self._request(target="dev_b1", args={"device_id": "dev_b1", "online": True})
        res = self.server.execute(req)
        self.assertEqual(res.status, ToolStatus.REFUSED)
        self.assertEqual(res.error.code, "scope_violation")
        self.assertEqual(req.envelope.status, REFUSED)

    def test_unknown_tool_is_rejected(self):
        req = self._request(tool_name="vendor.real_world_mutation")
        res = self.server.execute(req)
        self.assertEqual(res.status, ToolStatus.REFUSED)
        self.assertEqual(res.error.code, "unknown_tool")

    def test_allowed_scoped_tool_succeeds(self):
        req = self._request()
        res = self.server.execute(req)
        self.assertTrue(res.ok)
        self.assertEqual(res.status, ToolStatus.SUCCEEDED)
        self.assertEqual(req.envelope.status, SUCCEEDED)
        self.assertEqual(res.data["device_id"], "dev_a1")

    def test_tool_execution_creates_audit_record(self):
        req = self._request()
        self.server.execute(req)
        self.assertEqual(len(self.server.audit_events), 1)
        audit = self.server.audit_events[0]
        self.assertEqual(audit.tool_name, "device.set_online_status")
        self.assertEqual(audit.envelope_status, SUCCEEDED)
        self.assertEqual(audit.broker_decision, "allow")

    def test_duplicate_envelope_returns_idempotent_result(self):
        calls = {"n": 0}
        original = registry.REGISTRY["device.set_online_status"]

        def counting_handler(req: ToolRequest) -> ToolResponse:
            calls["n"] += 1
            return ToolResponse(
                status=ToolStatus.SUCCEEDED,
                data={"device_id": req.target_resource, "online": True, "counted": calls["n"]},
            )

        registry.REGISTRY["device.set_online_status"] = registry.ToolSpec(
            name=original.name,
            handler=counting_handler,
            envelope_action=original.envelope_action,
            required_action=original.required_action,
        )
        try:
            first = self.server.execute(self._request())
            second_req = self._request()
            second = self.server.execute(second_req)
        finally:
            registry.REGISTRY["device.set_online_status"] = original

        self.assertEqual(calls["n"], 1)
        self.assertEqual(first.status, ToolStatus.SUCCEEDED)
        self.assertEqual(second.status, ToolStatus.REPLAYED)
        self.assertEqual(second_req.envelope.status, REPLAYED)
        self.assertEqual(second.data["counted"], 1)

    def test_policy_denied_action_never_reaches_tool_handler(self):
        calls = {"n": 0}
        original = registry.REGISTRY["device.set_online_status"]

        def counting_handler(req: ToolRequest) -> ToolResponse:
            calls["n"] += 1
            return ToolResponse(status=ToolStatus.SUCCEEDED, data={"unexpected": True})

        registry.REGISTRY["device.set_online_status"] = registry.ToolSpec(
            name=original.name,
            handler=counting_handler,
            envelope_action=original.envelope_action,
            required_action=original.required_action,
        )
        try:
            req = self._request(decision="block")
            res = self.server.execute(req)
        finally:
            registry.REGISTRY["device.set_online_status"] = original

        self.assertEqual(calls["n"], 0)
        self.assertEqual(res.status, ToolStatus.REFUSED)
        self.assertEqual(res.error.code, "broker_denied")


class PipelineToolBoundaryTests(unittest.TestCase):
    def setUp(self):
        executor_mod.default_ledger.reset()
        default_tool_server.reset_audit()

    def test_reset_turn_uses_tool_server_boundary(self):
        resp = handle_turn("reset my router", auth_token="tok_alice")
        self.assertFalse(resp.escalated)
        self.assertEqual(resp.action_envelopes[0]["status"], SUCCEEDED)
        self.assertEqual(default_tool_server.audit_events[-1].tool_name, "device.set_online_status")


if __name__ == "__main__":
    unittest.main(verbosity=2)
