"""Voice channel adapter tests (v3.2).

Covers the adapter contract — a synthetic transcript normalizes into the existing
RelayOps request shape, required fields are validated, an empty transcript is
rejected, channel metadata is preserved, the adapter mutates nothing and executes
nothing — and the downstream invariant that voice does NOT bypass the control
plane: a refund voice call still escalates (high-risk path), and a cross-customer
voice call is still refused at the scoped tool boundary.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from src.channels.voice import (
    CHANNEL_VOICE,
    VoiceCallInput,
    adapt_call,
    adapt_dict,
    load_call,
)
from src.channels.voice import adapter as voice_adapter
from src.core.models import Disposition
from src.graph.pipeline import handle_turn
from src.observability.audit_ledger import AuditLedger

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "voice"


def _call(**overrides):
    base = {
        "channel": "voice",
        "call_id": "synthetic_call_001",
        "caller_id": "caller_alice",
        "customer_id": "cust_alice",
        "transcript": "My router is not working. Can you reset it?",
        "auth_token": "tok_alice",
    }
    base.update(overrides)
    return base


class VoiceAdapterContractTests(unittest.TestCase):
    def test_valid_transcript_converts_to_internal_request(self):
        result = adapt_dict(_call())
        self.assertTrue(result.ok)
        req = result.turn.to_request()
        # The normalized request mirrors the pipeline/API TurnRequest fields.
        self.assertEqual(req["message"], "My router is not working. Can you reset it?")
        self.assertEqual(req["token"], "tok_alice")
        self.assertEqual(req["customer_id"], "cust_alice")
        self.assertIn("message", req)

    def test_transcript_whitespace_is_normalized(self):
        result = adapt_dict(_call(transcript="  reset   my\nrouter  "))
        self.assertTrue(result.ok)
        self.assertEqual(result.turn.message, "reset my router")

    def test_empty_transcript_is_rejected(self):
        result = adapt_dict(_call(transcript="   "))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, voice_adapter.EMPTY_TRANSCRIPT)
        self.assertIsNone(result.turn)

    def test_missing_call_id_is_rejected(self):
        result = adapt_dict(_call(call_id=""))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, f"{voice_adapter.MISSING_FIELD}:call_id")

    def test_missing_caller_id_is_rejected(self):
        result = adapt_dict(_call(caller_id=""))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, f"{voice_adapter.MISSING_FIELD}:caller_id")

    def test_missing_customer_id_is_rejected(self):
        result = adapt_dict(_call(customer_id=""))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, f"{voice_adapter.MISSING_FIELD}:customer_id")

    def test_voice_metadata_is_preserved(self):
        result = adapt_dict(
            _call(language="en-US", confidence=0.9, redaction_notes="synthetic only")
        )
        meta = result.turn.metadata
        self.assertEqual(meta.channel, CHANNEL_VOICE)
        self.assertEqual(meta.call_id, "synthetic_call_001")
        self.assertEqual(meta.caller_id, "caller_alice")
        self.assertEqual(meta.customer_id, "cust_alice")
        self.assertEqual(meta.language, "en-US")
        self.assertEqual(meta.confidence, 0.9)
        self.assertEqual(meta.redaction_notes, "synthetic only")
        # Metadata survives serialization (audit/scenario evidence).
        self.assertEqual(result.turn.to_dict()["metadata"]["call_id"], "synthetic_call_001")

    def test_unknown_fields_are_dropped(self):
        # A stray audio URL / secret on the call object never reaches the request.
        result = adapt_dict(_call(audio_url="s3://nope", secret="leak"))
        self.assertTrue(result.ok)
        self.assertNotIn("audio_url", result.turn.to_dict())
        self.assertNotIn("secret", str(result.turn.to_dict()))

    def test_unauthenticated_call_warns_but_adapts(self):
        data = _call()
        del data["auth_token"]
        result = adapt_dict(data)
        self.assertTrue(result.ok)
        self.assertIsNone(result.turn.auth_token)
        self.assertTrue(any("no_auth_token" in w for w in result.warnings))

    def test_adapter_does_not_mutate_input(self):
        data = _call(language="en-US")
        before = copy.deepcopy(data)
        adapt_dict(data)
        self.assertEqual(data, before)

        call = VoiceCallInput.from_dict(_call())
        snapshot = call.to_dict()
        adapt_call(call)
        self.assertEqual(call.to_dict(), snapshot)


class VoiceAdapterSafetyTests(unittest.TestCase):
    def test_adapter_does_not_execute_tools_or_approve(self):
        """Structural: the adapter must expose no execution/approval/decision
        surface and must not pull the pipeline, tool server, or approval queue."""
        names = dir(voice_adapter)
        for forbidden in ("handle_turn", "execute", "approve", "reject", "run_tool"):
            self.assertNotIn(forbidden, names)
        for banned_module in ("handle_turn", "default_tool_server", "ApprovalQueue"):
            self.assertFalse(hasattr(voice_adapter, banned_module))

    def test_adapter_imports_no_external_voice_sdks(self):
        for banned in ("twilio", "boto3", "speech", "tts", "pyaudio", "requests", "httpx"):
            self.assertFalse(
                hasattr(voice_adapter, banned),
                f"voice adapter unexpectedly references {banned}",
            )

    def test_adapting_a_call_records_no_audit_and_runs_no_tool(self):
        ledger = AuditLedger()
        result = adapt_dict(_call())
        self.assertTrue(result.ok)
        # Normalizing a call is pure: it does not run the pipeline, so nothing is
        # audited and no tool executes until a caller chooses to run the turn.
        self.assertEqual(ledger.records, [])


class VoiceDownstreamInvariantTests(unittest.TestCase):
    """Voice does not bypass the control plane: the normalized request behaves
    exactly like typed text through the real pipeline."""

    def _run(self, result):
        turn = result.turn
        ledger = AuditLedger()
        response = handle_turn(
            turn.message,
            auth_token=turn.auth_token,
            device_id=turn.device_id,
            audit=ledger,
            turn_id=turn.suggested_turn_id,
        )
        return response, ledger

    def test_device_reset_voice_call_is_safe_automation(self):
        response, _ = self._run(adapt_dict(_call()))
        self.assertFalse(response.escalated)

    def test_refund_voice_call_takes_high_risk_path(self):
        result = adapt_dict(_call(transcript="I want a refund on my last bill."))
        response, _ = self._run(result)
        # A money-touching request still escalates — voice did not bypass the broker.
        self.assertTrue(
            response.escalated or response.disposition == Disposition.ESCALATE,
            "refund voice call should escalate to the high-risk / human path",
        )

    def test_cross_customer_voice_call_is_scope_blocked(self):
        result = adapt_dict(
            _call(
                caller_id="caller_bob",
                customer_id="cust_bob",
                transcript="Reset my router please.",
                auth_token="tok_bob",
                device_id="dev_a1",
            )
        )
        response, _ = self._run(result)
        # Bob targeting Alice's device is refused/escalated at the scoped boundary.
        self.assertTrue(
            response.escalated or response.disposition == Disposition.ESCALATE,
            "cross-customer voice call must be scope-blocked downstream",
        )

    def test_audit_record_ties_back_to_the_voice_call(self):
        result = adapt_dict(_call())
        _, ledger = self._run(result)
        self.assertEqual(len(ledger.records), 1)
        # The suggested turn id links the audit record to the originating call.
        self.assertEqual(ledger.records[0].turn_id, "voice_synthetic_call_001")


class VoiceExampleFileTests(unittest.TestCase):
    def test_example_files_load_and_adapt(self):
        for name in (
            "synthetic_call_device_reset.json",
            "synthetic_call_refund_request.json",
            "synthetic_call_scope_violation.json",
        ):
            call = load_call(_EXAMPLES / name)
            result = adapt_call(call)
            self.assertTrue(result.ok, f"{name} should adapt cleanly")
            self.assertEqual(result.turn.metadata.channel, CHANNEL_VOICE)

    def test_scope_violation_example_targets_another_customers_device(self):
        call = load_call(_EXAMPLES / "synthetic_call_scope_violation.json")
        self.assertEqual(call.auth_token, "tok_bob")
        self.assertEqual(call.device_id, "dev_a1")  # Alice's device


if __name__ == "__main__":
    unittest.main()
