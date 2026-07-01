"""Operator metrics tests (v2.5).

The operator metrics layer reduces audit records (+ optional replay evidence) into
the deterministic dashboard scoreboard: resolution / handoff / safety / replay /
efficiency. These tests cover each rate's arithmetic, safe handling of an empty
audit window, replay rates passed through from replay evidence, the read-only
integration into the Hermes report, and that computing metrics never mutates the
records or the supplied replay evidence.
"""

from __future__ import annotations

import copy
import unittest

from src.hermes import build_report
from src.operator_metrics import CostModel, OperatorMetrics, operator_metrics


def _record(**overrides) -> dict:
    base = {
        "turn_id": "t",
        "route": "respond",
        "action_class": "send_troubleshooting_link",
        "blast_radius": "low",
        "handoff_reason": None,
    }
    base.update(overrides)
    return base


def _respond() -> dict:
    return _record(route="respond")


def _auto_action() -> dict:
    return _record(route="auto_action", action_class="device_reset", blast_radius="low")


def _unsafe_escape() -> dict:
    return _record(route="auto_action", action_class="billing_refund", blast_radius="high")


def _fail_closed() -> dict:
    return _record(route="human_escalation", handoff_reason="fail_closed:guardrail_unavailable")


def _over_block() -> dict:
    return _record(route="human_escalation", handoff_reason="unverifiable")


def _escalation() -> dict:
    return _record(route="human_escalation", handoff_reason="billing")


class ResolutionAndHandoffTests(unittest.TestCase):
    def test_resolution_rate(self):
        # 3 resolved (2 respond + 1 auto_action) out of 4 turns.
        m = operator_metrics([_respond(), _respond(), _auto_action(), _escalation()])
        self.assertEqual(m.resolved_count, 3)
        self.assertEqual(m.resolution_rate, 0.75)
        self.assertEqual(m.total_turns, 4)

    def test_handoff_rate(self):
        m = operator_metrics([_respond(), _escalation(), _escalation(), _respond()])
        self.assertEqual(m.handoff_count, 2)
        self.assertEqual(m.handoff_rate, 0.5)

    def test_action_execution_rate(self):
        m = operator_metrics([_auto_action(), _respond(), _respond(), _respond()])
        self.assertEqual(m.action_execution_count, 1)
        self.assertEqual(m.action_execution_rate, 0.25)


class SafetyRateTests(unittest.TestCase):
    def test_fail_closed_rate(self):
        m = operator_metrics([_fail_closed(), _respond()])
        self.assertEqual(m.fail_closed_count, 1)
        self.assertEqual(m.fail_closed_rate, 0.5)

    def test_unsafe_escape_rate(self):
        m = operator_metrics([_unsafe_escape(), _respond(), _respond(), _respond()])
        self.assertEqual(m.unsafe_escape_count, 1)
        self.assertEqual(m.unsafe_escape_rate, 0.25)

    def test_over_block_rate(self):
        m = operator_metrics([_over_block(), _over_block(), _respond()])
        self.assertEqual(m.over_block_count, 2)
        self.assertAlmostEqual(m.over_block_rate, 0.6667, places=4)


class ReplayRateTests(unittest.TestCase):
    def test_replay_success_and_mismatch_rates(self):
        replay = {
            "replay_total": 4,
            "replay_success_rate": 0.75,
            "replay_mismatch_count": 1,
            "replay_blocked_count": 0,
        }
        m = operator_metrics([_respond()], replay_metrics=replay)
        self.assertEqual(m.replay_success_rate, 0.75)
        self.assertEqual(m.replay_mismatch_rate, 0.25)

    def test_replay_rates_none_without_evidence(self):
        m = operator_metrics([_respond()])
        self.assertIsNone(m.replay_success_rate)
        self.assertIsNone(m.replay_mismatch_rate)


class EfficiencyTests(unittest.TestCase):
    def test_avg_turns_to_resolution(self):
        # 4 turns, 2 resolved -> 2.0 turns of work per resolution.
        m = operator_metrics([_respond(), _respond(), _escalation(), _escalation()])
        self.assertEqual(m.avg_turns_to_resolution, 2.0)

    def test_estimated_cost_per_resolved_ticket(self):
        # 4 turns * $0.50/turn / 2 resolved = $1.00 per resolved ticket.
        model = CostModel(cost_per_turn_usd=0.50)
        m = operator_metrics(
            [_respond(), _respond(), _escalation(), _escalation()], cost_model=model
        )
        self.assertEqual(m.estimated_cost_per_resolved_ticket, 1.0)


class EmptyRecordsTests(unittest.TestCase):
    def test_empty_records_are_safe(self):
        m = operator_metrics([])
        self.assertIsInstance(m, OperatorMetrics)
        self.assertEqual(m.total_turns, 0)
        self.assertEqual(m.resolution_rate, 0.0)
        self.assertEqual(m.handoff_rate, 0.0)
        self.assertEqual(m.fail_closed_rate, 0.0)
        self.assertEqual(m.action_execution_rate, 0.0)
        # No division by zero in the efficiency block.
        self.assertEqual(m.avg_turns_to_resolution, 0.0)
        self.assertEqual(m.estimated_cost_per_resolved_ticket, 0.0)
        # Replay rates stay unscored (None), not a false zero.
        self.assertIsNone(m.replay_success_rate)
        self.assertIsNone(m.replay_mismatch_rate)


class HermesIntegrationTests(unittest.TestCase):
    def test_hermes_report_includes_operator_metrics_read_only(self):
        report = build_report([_respond(), _escalation(), _unsafe_escape()])
        om = report.operator_metrics
        self.assertEqual(om["total_turns"], 3)
        self.assertIn("resolution_rate", om)
        self.assertIn("handoff_rate", om)
        self.assertIn("unsafe_escape_rate", om)
        # to_dict round-trips the operator metrics dict.
        self.assertEqual(report.to_dict()["operator_metrics"], om)

    def test_metrics_do_not_mutate_records(self):
        records = [_respond(), _escalation(), _unsafe_escape()]
        before = copy.deepcopy(records)
        operator_metrics(records)
        build_report(records)
        self.assertEqual(records, before)

    def test_metrics_do_not_mutate_replay_evidence(self):
        replay = {"replay_total": 2, "replay_success_rate": 0.5, "replay_mismatch_count": 1}
        before = copy.deepcopy(replay)
        operator_metrics([_respond()], replay_metrics=replay)
        self.assertEqual(replay, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
