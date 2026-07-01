"""Hermes — read-only operator agent for RelayOps.

Hermes sits on the OPERATOR side of the system, after the audit store. It reads
decision traces and produces advisory findings, suggested tests, policy-gap
flags, draft issues, and release notes for a human developer. It never touches
the customer runtime: no replies, no tools, no policy edits, no broker overrides.

    audit store -> Hermes (review) -> findings/report -> human developer review
"""

from __future__ import annotations

from .alerting import (
    AlertPacket,
    AlertReport,
    AlertThresholds,
    build_alert_report,
    evaluate_alerts,
    report_from_metrics,
)
from .metrics import SafetyMetrics, safety_metrics
from .models import HermesReport, HermesReviewPacket
from .replay_review import review_replay_result, review_replay_results
from .report import build_report, report_from_findings
from .reviewer import review, review_record

__all__ = [
    "HermesReviewPacket",
    "HermesReport",
    "SafetyMetrics",
    "safety_metrics",
    "review",
    "review_record",
    "review_replay_result",
    "review_replay_results",
    "build_report",
    "report_from_findings",
    "AlertThresholds",
    "AlertPacket",
    "AlertReport",
    "evaluate_alerts",
    "report_from_metrics",
    "build_alert_report",
]
