"""Hermes operator alerting tests (v2.5).

The alerting layer compares a metrics snapshot against named operator thresholds
and emits advisory ``AlertPacket`` breaches. These tests cover: each threshold
direction (max/min), that missing and ``None`` metrics are skipped (not scored),
that critical breaches drive the CI-gate signal and draft issues, the
end-to-end ``build_alert_report`` over audit records, and that alerting stays
read-only / advisory (no execute surface, human review always required).
"""

from __future__ import annotations

import copy
import unittest

from src import hermes
from src.hermes import (
    AlertThresholds,
    build_alert_report,
    evaluate_alerts,
    report_from_metrics,
)


def _record(**overrides) -> dict:
    base = {
        "turn_id": "turn_x",
        "route": "respond",
        "action_class": "send_troubleshooting_link",
        "blast_radius": "low",
        "handoff_reason": None,
        "guardrail": {"checked": True, "verdict": "pass", "violations": []},
        "broker_decision_packet": {
            "decision": "allow",
            "policy_handle": "faq.answer.requires_grounding",
        },
    }
    base.update(overrides)
    return base


class EvaluateAlertsTests(unittest.TestCase):
    def test_clean_metrics_raise_no_alert(self):
        metrics = {
            "unsafe_escape_rate": 0.0,
            "fail_closed_rate": 0.0,
            "over_block_rate": 0.0,
            "replay_success_rate": 1.0,
            "replay_blocked_count": 0,
            "handoff_completeness_score": 1.0,
        }
        self.assertEqual(evaluate_alerts(metrics), [])

    def test_max_metric_breaches_when_above(self):
        alerts = evaluate_alerts({"unsafe_escape_rate": 0.01})
        self.assertEqual(len(alerts), 1)
        a = alerts[0]
        self.assertEqual(a.metric, "unsafe_escape_rate")
        self.assertEqual(a.severity, "critical")
        self.assertEqual(a.comparison, "max")
        self.assertTrue(a.human_review_required)

    def test_min_metric_breaches_when_below(self):
        alerts = evaluate_alerts({"replay_success_rate": 0.90})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].metric, "replay_success_rate")
        self.assertEqual(alerts[0].comparison, "min")
        self.assertEqual(alerts[0].severity, "high")

    def test_value_exactly_on_threshold_does_not_breach(self):
        # max budget is "<= threshold"; min budget is ">= threshold".
        self.assertEqual(evaluate_alerts({"over_block_rate": 0.25}), [])
        self.assertEqual(evaluate_alerts({"replay_success_rate": 0.99}), [])

    def test_missing_and_none_metrics_are_skipped(self):
        # No replay metrics present, and handoff score is None on in-memory records.
        alerts = evaluate_alerts({"unsafe_escape_rate": 0.0, "handoff_completeness_score": None})
        self.assertEqual(alerts, [])

    def test_alerts_sorted_most_urgent_first(self):
        metrics = {
            "over_block_rate": 1.0,  # medium
            "replay_blocked_count": 1,  # critical
            "fail_closed_rate": 1.0,  # high
        }
        sevs = [a.severity for a in evaluate_alerts(metrics)]
        self.assertEqual(sevs, ["critical", "high", "medium"])

    def test_custom_thresholds_relax_a_signal(self):
        loud = {"over_block_rate": 0.30}
        self.assertTrue(evaluate_alerts(loud))  # default 0.25 -> breach
        relaxed = AlertThresholds(over_block_rate=0.50)
        self.assertEqual(evaluate_alerts(loud, relaxed), [])  # 0.30 <= 0.50 -> ok


class AlertReportTests(unittest.TestCase):
    def test_report_drafts_issues_for_high_and_critical_only(self):
        metrics = {
            "unsafe_escape_rate": 0.01,  # critical
            "over_block_rate": 1.0,  # medium
        }
        report = report_from_metrics(metrics)
        self.assertTrue(report.breached)
        self.assertTrue(report.has_critical)
        self.assertEqual(len(report.alerts), 2)
        # Only the critical breach is drafted as an issue, not the medium one.
        self.assertEqual(len(report.suggested_github_issues), 1)
        self.assertIn("unsafe_escape_rate", report.suggested_github_issues[0]["title"])

    def test_clean_report_is_not_breached(self):
        report = report_from_metrics({"unsafe_escape_rate": 0.0})
        self.assertFalse(report.breached)
        self.assertFalse(report.has_critical)
        self.assertIn("No breaches", report.summary)

    def test_medium_breach_is_breached_but_not_critical(self):
        report = report_from_metrics({"over_block_rate": 1.0})
        self.assertTrue(report.breached)
        self.assertFalse(report.has_critical)
        self.assertEqual(report.suggested_github_issues, [])


class BuildAlertReportTests(unittest.TestCase):
    def test_unsafe_escape_in_records_fires_critical(self):
        records = [
            _record(),
            _record(route="auto_action", action_class="billing_refund", blast_radius="high"),
        ]
        report = build_alert_report(records)
        self.assertTrue(report.has_critical)
        self.assertIn("unsafe_escape_rate", {a.metric for a in report.alerts})

    def test_replay_metrics_merge_in(self):
        records = [_record()]
        report = build_alert_report(records, replay_metrics={"replay_blocked_count": 2})
        metrics = {a.metric for a in report.alerts}
        self.assertIn("replay_blocked_count", metrics)
        self.assertTrue(report.has_critical)

    def test_clean_records_no_breach(self):
        report = build_alert_report([_record(), _record()])
        self.assertFalse(report.breached)


class OperatorMetricBreachTests(unittest.TestCase):
    """The alerting layer now evaluates the v2.5 operator dashboard metrics
    (handoff_rate, resolution, ...) merged in by ``build_alert_report``."""

    def test_high_handoff_rate_creates_advisory_alert(self):
        # 3 escalations + 1 respond -> handoff_rate 0.75 > 0.50 budget.
        records = [
            _record(route="human_escalation", handoff_reason="billing"),
            _record(route="human_escalation", handoff_reason="billing"),
            _record(route="human_escalation", handoff_reason="billing"),
            _record(),
        ]
        report = build_alert_report(records)
        breached = {a.metric: a for a in report.alerts}
        self.assertIn("handoff_rate", breached)
        self.assertEqual(breached["handoff_rate"].severity, "high")
        self.assertTrue(breached["handoff_rate"].human_review_required)
        # High severity -> drafted as an issue for a human.
        self.assertTrue(any("handoff_rate" in i["title"] for i in report.suggested_github_issues))

    def test_high_fail_closed_rate_creates_advisory_alert(self):
        records = [
            _record(route="human_escalation", handoff_reason="fail_closed:guardrail_unavailable"),
            _record(),
        ]
        report = build_alert_report(records)
        breached = {a.metric: a for a in report.alerts}
        self.assertIn("fail_closed_rate", breached)
        self.assertEqual(breached["fail_closed_rate"].severity, "high")

    def test_critical_unsafe_and_replay_blocked_behave_as_critical(self):
        records = [
            _record(),
            _record(route="auto_action", action_class="billing_refund", blast_radius="high"),
        ]
        report = build_alert_report(records, replay_metrics={"replay_blocked_count": 1})
        metrics = {a.metric: a.severity for a in report.alerts}
        self.assertEqual(metrics.get("unsafe_escape_rate"), "critical")
        self.assertEqual(metrics.get("replay_blocked_count"), "critical")
        self.assertTrue(report.has_critical)

    def test_clean_records_raise_no_operator_breach(self):
        report = build_alert_report([_record(), _record(), _record()])
        self.assertFalse(report.breached)


class AlertingDoesNotMutateTests(unittest.TestCase):
    def test_build_alert_report_does_not_mutate_records_or_replay(self):
        records = [
            _record(),
            _record(route="human_escalation", handoff_reason="fail_closed:guardrail_unavailable"),
        ]
        replay = {"replay_total": 2, "replay_success_rate": 0.5, "replay_blocked_count": 1}
        records_before = copy.deepcopy(records)
        replay_before = copy.deepcopy(replay)
        build_alert_report(records, replay_metrics=replay)
        self.assertEqual(records, records_before)
        self.assertEqual(replay, replay_before)


class AlertingIsAdvisoryOnlyTests(unittest.TestCase):
    def test_no_execute_surface_added(self):
        forbidden = ("execute", "send", "apply", "approve", "refund", "override", "run_tool")
        names = [n.lower() for n in dir(hermes)]
        for bad in forbidden:
            self.assertFalse(
                any(bad in n for n in names),
                f"Hermes unexpectedly exposes an action-like name containing '{bad}'",
            )

    def test_every_alert_requires_human_review(self):
        for a in evaluate_alerts({"unsafe_escape_rate": 1.0, "over_block_rate": 1.0}):
            self.assertTrue(a.human_review_required)


if __name__ == "__main__":
    unittest.main(verbosity=2)
