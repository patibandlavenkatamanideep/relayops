"""Design-partner report tests (v2.6).

Covers imported/skipped totals, category breakdown, the deterministic
automation / handoff / unsafe classifications, policy + tool coverage-gap
detection, Markdown and JSON-compatible output, the advisory-only Hermes bridge,
and that building the report never mutates the imported records.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from src.importers import import_csv, import_rows
from src.reports import (
    build_design_partner_report,
    to_hermes_findings,
)

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _row(**overrides) -> dict:
    base = {
        "ticket_id": "RT-1",
        "customer_id": "cust_1",
        "message": "please reset my router",
        "category": "device_reset",
        "expected_resolution": "scoped device reset",
        "sensitivity_level": "low",
    }
    base.update(overrides)
    return base


def _sample_rows() -> list[dict]:
    return [
        _row(ticket_id="A", category="device_reset", sensitivity_level="low"),
        _row(ticket_id="B", category="device_status", sensitivity_level="low"),
        _row(ticket_id="C", category="refund", sensitivity_level="high"),
        _row(ticket_id="D", category="password_reset", sensitivity_level="restricted"),
        _row(
            ticket_id="E",
            category="roaming",
            sensitivity_level="medium",
            message="how do I enable international roaming?",
        ),
        _row(
            ticket_id="F",
            category="device_reset",
            sensitivity_level="high",
            message="reset my router, it holds sensitive notes",
        ),
    ]


def _report():
    return build_design_partner_report(import_rows(_sample_rows(), source="unit"))


class TotalsTests(unittest.TestCase):
    def test_imported_and_skipped_totals(self):
        rows = _sample_rows() + [_row(ticket_id="BAD", message="")]
        report = build_design_partner_report(import_rows(rows))
        self.assertEqual(report.total_received, 7)
        self.assertEqual(report.total_imported, 6)
        self.assertEqual(len(report.skipped), 1)
        self.assertEqual(report.skipped_reason_counts, {"empty_message": 1})

    def test_category_breakdown(self):
        report = _report()
        self.assertEqual(
            report.category_breakdown,
            {"reset_device": 2, "device_status": 1, "billing": 1, "account": 1, "unknown": 1},
        )


class ClassificationTests(unittest.TestCase):
    def test_identifies_automation_candidates(self):
        report = _report()
        # Only the two low-sensitivity automatable tickets (A, B).
        self.assertEqual(report.automation_candidates, 2)
        self.assertEqual(sorted(report.automation_ticket_ids), ["A", "B"])

    def test_identifies_handoff_candidates(self):
        report = _report()
        # Everything else: billing, account, unknown, and the sensitive reset (F).
        self.assertEqual(report.handoff_candidates, 4)
        self.assertEqual(sorted(report.handoff_ticket_ids), ["C", "D", "E", "F"])

    def test_automation_and_handoff_partition_imports(self):
        report = _report()
        self.assertEqual(
            report.automation_candidates + report.handoff_candidates, report.total_imported
        )

    def test_identifies_unsafe_sensitive_cases(self):
        report = _report()
        # High/restricted sensitivity or billing/account: C, D, F (E is medium).
        self.assertEqual(report.unsafe_sensitive_cases, 3)
        self.assertEqual(sorted(report.unsafe_ticket_ids), ["C", "D", "F"])
        self.assertEqual(report.suggested_human_review_cases, report.unsafe_ticket_ids)


class CoverageGapTests(unittest.TestCase):
    def test_suggests_missing_policy_and_tool_gaps(self):
        report = _report()
        self.assertEqual(report.missing_policy_candidates, ["roaming"])
        # "enable ... roaming" reads like an action -> missing tool too.
        self.assertEqual(report.missing_tool_candidates, ["roaming"])
        self.assertIn("support.roaming.requires_definition", report.suggested_policy_handles)

    def test_covered_category_uses_registered_handle(self):
        report = _report()
        self.assertIn("device.reset.allowed_if_scoped", report.suggested_policy_handles)

    def test_suggested_automations_mention_tool(self):
        report = _report()
        joined = "\n".join(report.suggested_automations)
        self.assertIn("device_reset", joined)
        self.assertIn("account_lookup", joined)


class OutputShapeTests(unittest.TestCase):
    def test_produces_markdown(self):
        md = _report().to_markdown()
        self.assertIsInstance(md, str)
        self.assertIn("# RelayOps design-partner report", md)
        self.assertIn("## Category breakdown", md)
        self.assertIn("## Replay-readiness notes", md)

    def test_produces_json_compatible_dict(self):
        d = _report().to_dict()
        # Round-trips through JSON without error.
        restored = json.loads(json.dumps(d))
        self.assertEqual(restored["total_imported"], 6)
        self.assertIn("category_breakdown", restored)

    def test_replay_notes_present(self):
        report = _report()
        self.assertTrue(report.replay_readiness_notes)
        self.assertTrue(any("no execution" in n for n in report.replay_readiness_notes))


class HermesBridgeTests(unittest.TestCase):
    def test_findings_are_advisory_only(self):
        findings = to_hermes_findings(_report())
        self.assertTrue(findings)  # roaming -> missing policy + missing tool
        types = {f.finding_type for f in findings}
        self.assertIn("missing_policy_candidate", types)
        self.assertIn("missing_tool_candidate", types)
        for f in findings:
            self.assertTrue(f.human_review_required)

    def test_bridge_does_not_execute_or_import(self):
        # The reports module exposes no action/import surface.
        import src.reports.design_partner_report as mod

        source = Path(mod.__file__).read_text()
        for banned in ("import requests", "import urllib", "import httpx", "subprocess"):
            self.assertNotIn(banned, source)


class NoMutationTests(unittest.TestCase):
    def test_report_does_not_mutate_records(self):
        result = import_rows(_sample_rows())
        before = copy.deepcopy(result.records)
        build_design_partner_report(result)
        to_hermes_findings(build_design_partner_report(result))
        self.assertEqual(result.records, before)

    def test_report_over_bundled_sample(self):
        report = build_design_partner_report(import_csv(_EXAMPLES / "redacted_tickets.csv"))
        self.assertEqual(report.total_imported, 10)
        self.assertEqual(report.automation_candidates, 5)
        self.assertEqual(report.handoff_candidates, 5)
        self.assertEqual(report.unsafe_sensitive_cases, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
