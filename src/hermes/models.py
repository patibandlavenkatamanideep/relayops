"""Hermes data types.

``HermesReviewPacket`` is one operator-side finding about a single turn;
``HermesReport`` aggregates findings into drafts for a human developer. Both are
plain dataclasses — Hermes is advisory and produces evidence, not actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Operator triage scale, least -> most urgent.
SEVERITIES = ("low", "medium", "high", "critical")


@dataclass
class HermesReviewPacket:
    """A single advisory finding. ``human_review_required`` is True by default:
    Hermes never decides, it flags for a human."""

    turn_id: str
    severity: str
    finding_type: str
    summary: str
    suggested_test: str = ""
    suggested_policy_gap: str = ""
    human_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HermesReport:
    """Aggregated operator output: a summary plus drafts for human review."""

    findings: list[HermesReviewPacket] = field(default_factory=list)
    failure_summary: str = ""
    suggested_tests: list[str] = field(default_factory=list)
    suggested_policy_gaps: list[str] = field(default_factory=list)
    suggested_github_issues: list[dict[str, str]] = field(default_factory=list)
    release_notes_draft: str = ""
    # Split safety metrics (see hermes.metrics.safety_metrics). Stored as a plain
    # dict so ``models`` need not import ``metrics`` (avoids a circular import).
    metrics: dict[str, Any] = field(default_factory=dict)
    # Operator dashboard metrics (see src.operator_metrics). Read-only scoreboard
    # (resolution / handoff / safety / replay / efficiency) carried alongside the
    # safety metrics. Stored as a plain dict for the same reason as ``metrics``.
    operator_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
