"""Hermes — read-only operator agent for RelayOps.

Hermes sits on the OPERATOR side of the system, after the audit store. It reads
decision traces and produces advisory findings, suggested tests, policy-gap
flags, draft issues, and release notes for a human developer. It never touches
the customer runtime: no replies, no tools, no policy edits, no broker overrides.

    audit store -> Hermes (review) -> findings/report -> human developer review
"""

from __future__ import annotations

from .models import HermesReport, HermesReviewPacket
from .report import build_report, report_from_findings
from .reviewer import review, review_record

__all__ = [
    "HermesReviewPacket",
    "HermesReport",
    "review",
    "review_record",
    "build_report",
    "report_from_findings",
]
