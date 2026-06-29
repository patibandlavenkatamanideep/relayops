"""Hermes operator report — deterministic drafts for a human developer.

Aggregates review findings into a failure summary, a deduped list of suggested
tests and policy gaps, draft GitHub issues for the urgent findings, and a
release-notes draft. Everything here is a DRAFT for human review; Hermes does
not open issues, change policy, or ship notes on its own.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from .metrics import safety_metrics
from .models import HermesReport, HermesReviewPacket
from .reviewer import review

# Findings at or above this severity get a drafted GitHub issue.
_ISSUE_SEVERITIES = ("high", "critical")


def build_report(records: list[dict]) -> HermesReport:
    """Review audit records and assemble the operator report (incl. metrics)."""
    report = report_from_findings(review(records))
    report.metrics = safety_metrics(records).to_dict()
    return report


def report_from_findings(findings: list[HermesReviewPacket]) -> HermesReport:
    by_type = Counter(f.finding_type for f in findings)
    return HermesReport(
        findings=findings,
        failure_summary=_failure_summary(by_type, len(findings)),
        suggested_tests=_dedup(f.suggested_test for f in findings if f.suggested_test),
        suggested_policy_gaps=_dedup(
            f.suggested_policy_gap for f in findings if f.suggested_policy_gap
        ),
        suggested_github_issues=[_issue(f) for f in findings if f.severity in _ISSUE_SEVERITIES],
        release_notes_draft=_release_notes(by_type),
    )


def _dedup(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _failure_summary(by_type: Counter, total: int) -> str:
    if total == 0:
        return "No findings: all reviewed turns reached a safe disposition."
    parts = ", ".join(f"{n} {t}" for t, n in sorted(by_type.items()))
    return f"{total} finding(s) across reviewed turns: {parts}."


def _issue(f: HermesReviewPacket) -> dict[str, str]:
    return {
        "title": f"[{f.severity}] {f.finding_type}: {f.summary}",
        "body": (
            f"Turn: {f.turn_id}\n"
            f"Severity: {f.severity}\n"
            f"Finding: {f.finding_type}\n\n"
            f"{f.summary}\n\n"
            f"Suggested test: {f.suggested_test or 'n/a'}\n"
            f"Policy gap: {f.suggested_policy_gap or 'n/a'}\n\n"
            "Advisory only — requires human review before any change."
        ),
    }


def _release_notes(by_type: Counter) -> str:
    if not by_type:
        return "No operator-visible findings in this run."
    lines = ["Operator review (Hermes, advisory):"]
    for finding_type, n in sorted(by_type.items()):
        lines.append(f"- {n} {finding_type} finding(s) flagged for human review.")
    return "\n".join(lines)
