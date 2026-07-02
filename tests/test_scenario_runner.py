"""End-to-end scenario runner tests (v2.9).

Each scenario is a synthetic, redacted ticket that must prove one control-plane
property: safe automation, approval-before-execution, scope blocking, fail-closed
handoff, and replay verification catching drift. These tests assert the runner
walks the full lifecycle deterministically and that every scenario meets its
declared expectation — and that the runner stays read-only/advisory (Hermes never
approves, no real external execution happens).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.scenarios import (
    SAMPLE_SCENARIOS,
    STAGES,
    check_expectations,
    get_sample,
    load_scenario,
    render_markdown,
    run_scenario,
    run_scenarios,
)

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "scenarios"


class ScenarioExpectationTests(unittest.TestCase):
    def test_every_sample_meets_its_expectation(self):
        for scenario in SAMPLE_SCENARIOS:
            with self.subTest(scenario=scenario.id):
                result = run_scenario(scenario)
                self.assertEqual(check_expectations(scenario, result), [])

    def test_result_walks_every_lifecycle_stage_in_order(self):
        result = run_scenario(get_sample("device_status"))
        self.assertEqual([s.stage for s in result.stages], list(STAGES))

    def test_runner_is_deterministic(self):
        a = run_scenario(get_sample("high_risk_refund"))
        b = run_scenario(get_sample("high_risk_refund"))

        # The lifecycle shape and every human-readable summary are deterministic;
        # only opaque ids (a random approval id, audit-event timestamps) inside
        # stage detail vary run to run, so we compare the meaningful surface.
        def shape(result):
            return (
                result.route,
                result.approval_required,
                result.execution_blocked,
                result.replay_status,
                result.escalated,
                [(s.stage, s.status, s.summary) for s in result.stages],
            )

        self.assertEqual(shape(a), shape(b))


class SafeAutomationScenarioTests(unittest.TestCase):
    def setUp(self):
        self.result = run_scenario(get_sample("device_status"))

    def test_safe_path_resolves_without_human_or_approval(self):
        self.assertEqual(self.result.route, "respond")
        self.assertFalse(self.result.escalated)
        self.assertFalse(self.result.approval_required)
        self.assertFalse(self.result.execution_blocked)

    def test_replay_passes(self):
        self.assertEqual(self.result.replay_status, "pass")
        self.assertEqual(self.result.stage("replay_verification").status, "ok")


class HighRiskApprovalScenarioTests(unittest.TestCase):
    def setUp(self):
        self.result = run_scenario(get_sample("high_risk_refund"))

    def test_high_risk_requires_approval_and_is_blocked(self):
        self.assertTrue(self.result.approval_required)
        self.assertTrue(self.result.execution_blocked)
        self.assertEqual(self.result.stage("approval").status, "blocked")

    def test_escalated_to_human(self):
        self.assertTrue(self.result.escalated)
        self.assertEqual(self.result.route, "human_escalation")

    def test_approval_export_shows_a_blocked_hold(self):
        export = self.result.stage("approval_export").detail
        self.assertGreaterEqual(export["blocked_count"], 1)
        self.assertEqual(len(export["pending"]), 1)


class ScopeViolationScenarioTests(unittest.TestCase):
    def setUp(self):
        self.result = run_scenario(get_sample("cross_customer_block"))

    def test_tool_boundary_refuses_on_scope(self):
        tool_stage = self.result.stage("tool_boundary")
        self.assertEqual(tool_stage.status, "blocked")
        self.assertEqual(tool_stage.detail.get("error"), "scope_violation")

    def test_auth_scope_stage_flags_the_violation(self):
        self.assertEqual(self.result.stage("auth_scope").status, "blocked")
        self.assertTrue(self.result.execution_blocked)


class FailClosedScenarioTests(unittest.TestCase):
    def test_missing_evidence_faq_hands_off(self):
        result = run_scenario(get_sample("missing_evidence_faq"))
        self.assertTrue(result.escalated)
        self.assertEqual(result.route, "human_escalation")
        # No side effect, no approval hold — it simply routes to a human.
        self.assertFalse(result.approval_required)
        self.assertFalse(result.execution_blocked)


class ReplayMismatchScenarioTests(unittest.TestCase):
    def test_replay_verifier_catches_injected_drift(self):
        result = run_scenario(get_sample("replay_mismatch"))
        self.assertEqual(result.replay_status, "mismatch")
        stage = result.stage("replay_verification")
        self.assertEqual(stage.status, "blocked")
        self.assertIn("broker_decision_mismatch", stage.detail["reason_codes"])

    def test_injection_does_not_change_the_live_turn(self):
        # The drift is applied to a copy of the replay record only: the turn still
        # resolves normally (respond), it is only the replay comparison that fails.
        result = run_scenario(get_sample("replay_mismatch"))
        self.assertEqual(result.route, "respond")
        self.assertFalse(result.escalated)


class HermesReadOnlyTests(unittest.TestCase):
    def test_hermes_stage_is_advisory_only(self):
        result = run_scenario(get_sample("high_risk_refund"))
        hermes_stage = result.stage("hermes_review")
        self.assertEqual(hermes_stage.status, "info")
        for finding in hermes_stage.detail["findings"]:
            self.assertTrue(finding.get("human_review_required"))

    def test_runner_module_exposes_no_execution_surface(self):
        import src.scenarios.runner as runner_mod

        for forbidden in ("approve", "reject", "execute_action", "set_device_online"):
            self.assertNotIn(forbidden, dir(runner_mod))


class ScenarioLoadingTests(unittest.TestCase):
    def test_example_files_load_and_pass(self):
        for path in sorted(_EXAMPLES.glob("*.json")):
            with self.subTest(path=path.name):
                scenario = load_scenario(path)
                result = run_scenario(scenario)
                self.assertEqual(check_expectations(scenario, result), [])

    def test_loader_ignores_unknown_fields(self, tmp_key="__secret_token__"):
        payload = {
            "id": "adhoc",
            "message": "what is my device status?",
            "auth_token": "tok_alice",
            tmp_key: "must-be-ignored",
        }
        path = Path(self._make_tmp(payload))
        scenario = load_scenario(path)
        self.assertFalse(hasattr(scenario, tmp_key))
        self.assertEqual(scenario.id, "adhoc")

    def test_loader_requires_id_and_message(self):
        path = Path(self._make_tmp({"title": "no id or message"}))
        with self.assertRaises(ValueError):
            load_scenario(path)

    def _make_tmp(self, payload: dict) -> str:
        import tempfile

        fd = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, fd)
        fd.close()
        return fd.name


class BatchRenderTests(unittest.TestCase):
    def test_run_scenarios_and_markdown(self):
        results = run_scenarios(SAMPLE_SCENARIOS)
        self.assertEqual(len(results), len(SAMPLE_SCENARIOS))
        md = render_markdown(results)
        self.assertIn("RelayOps end-to-end scenario report", md)
        self.assertIn("High-risk refund request", md)
        # Serializable end to end.
        json.dumps([r.to_dict() for r in results])


if __name__ == "__main__":
    unittest.main()
