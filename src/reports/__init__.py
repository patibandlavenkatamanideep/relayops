"""Design-partner reporting (v2.6).

Reduces an imported redacted ticket sample (``src.importers``) into a read-only
design-partner report: automation vs. handoff estimates, unsafe/sensitive cases,
policy/tool coverage gaps, suggested automations, and replay-readiness notes.
Renders to Markdown and to a JSON-compatible dict, and can bridge its gaps into
advisory Hermes findings — all without running the agent or making any external
call.

    ImportResult -> build_design_partner_report() -> DesignPartnerReport
"""

from __future__ import annotations

from .design_partner_report import (
    DesignPartnerReport,
    build_design_partner_report,
    to_hermes_findings,
)

__all__ = [
    "DesignPartnerReport",
    "build_design_partner_report",
    "to_hermes_findings",
]
