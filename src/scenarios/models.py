"""End-to-end scenario data types (v2.9).

A ``Scenario`` is one synthetic, redacted support ticket plus the caller context
to run it under, and the outcome we expect. A ``ScenarioResult`` is the readable
lifecycle the runner produces: an ordered list of ``StageResult`` steps that walk
the full RelayOps control plane for that one ticket — ingest → auth/scope → broker
→ envelope → tool boundary → approval → audit → replay → operator metrics → Hermes
→ approval export → final report.

These are plain dataclasses. The runner (``runner.py``) composes the real
RelayOps modules to fill them in; nothing here executes anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# The control-plane lifecycle, in order. Each maps to one StageResult so a reader
# can watch a single ticket traverse every layer the project builds.
STAGES: tuple[str, ...] = (
    "ingest",
    "auth_scope",
    "broker_decision",
    "action_envelope",
    "tool_boundary",
    "approval",
    "audit_record",
    "replay_verification",
    "operator_metrics",
    "hermes_review",
    "approval_export",
    "final_report",
)

# Stage status vocabulary (deterministic, for tests/dashboards).
OK = "ok"
BLOCKED = "blocked"
ESCALATED = "escalated"
INFO = "info"


@dataclass
class Scenario:
    """One synthetic, redacted ticket + caller context + expected outcome."""

    id: str
    title: str
    description: str
    message: str  # the redacted customer/ticket text
    auth_token: Optional[str] = None  # tok_alice / tok_bob / None (unauthenticated)
    device_id: Optional[str] = None  # optional target resource (may be cross-customer)
    # What this scenario is meant to prove. The runner records the observed values;
    # ``check_expectations`` compares them so a regression is caught by a test.
    expect_route: str = ""  # auto_action / respond / human_escalation
    expect_approval_required: bool = False
    expect_execution_blocked: bool = False
    expect_replay_status: str = "pass"  # pass / mismatch / blocked
    expect_escalated: bool = False
    # Demonstration hook: deliberately drift the replayed audit record so the
    # replay verifier catches an inconsistency. Synthetic; documented as such.
    inject_replay_drift: str = ""  # "" | "broker" | "scope"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StageResult:
    """One lifecycle step's outcome: which stage, its status, and the evidence."""

    stage: str
    status: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioResult:
    """The full readable lifecycle for one scenario."""

    scenario_id: str
    title: str
    stages: list[StageResult] = field(default_factory=list)
    # Observed outcomes (what the expectations are checked against).
    route: str = ""
    approval_required: bool = False
    execution_blocked: bool = False
    replay_status: str = ""
    escalated: bool = False
    final_disposition: str = ""

    def stage(self, name: str) -> Optional[StageResult]:
        for s in self.stages:
            if s.stage == name:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "route": self.route,
            "approval_required": self.approval_required,
            "execution_blocked": self.execution_blocked,
            "replay_status": self.replay_status,
            "escalated": self.escalated,
            "final_disposition": self.final_disposition,
            "stages": [s.to_dict() for s in self.stages],
        }

    def to_markdown(self) -> str:
        lines = [
            f"## {self.title}  (`{self.scenario_id}`)",
            "",
            f"- Route: **{self.route or '—'}**  ·  Escalated: **{self.escalated}**",
            f"- Approval required: **{self.approval_required}**  ·  "
            f"Execution blocked: **{self.execution_blocked}**",
            f"- Replay: **{self.replay_status or '—'}**  ·  "
            f"Final disposition: **{self.final_disposition or '—'}**",
            "",
            "| Stage | Status | Evidence |",
            "|---|---|---|",
        ]
        for s in self.stages:
            summary = s.summary.replace("|", "\\|")
            lines.append(f"| {s.stage} | {s.status} | {summary} |")
        lines.append("")
        return "\n".join(lines)


def check_expectations(scenario: Scenario, result: ScenarioResult) -> list[str]:
    """Return a list of human-readable mismatches between what a scenario expected
    and what the runner observed. Empty list == the scenario proved its point."""
    problems: list[str] = []
    if scenario.expect_route and result.route != scenario.expect_route:
        problems.append(f"route: expected {scenario.expect_route!r}, got {result.route!r}")
    if result.approval_required != scenario.expect_approval_required:
        problems.append(
            f"approval_required: expected {scenario.expect_approval_required}, "
            f"got {result.approval_required}"
        )
    if result.execution_blocked != scenario.expect_execution_blocked:
        problems.append(
            f"execution_blocked: expected {scenario.expect_execution_blocked}, "
            f"got {result.execution_blocked}"
        )
    if result.replay_status != scenario.expect_replay_status:
        problems.append(
            f"replay_status: expected {scenario.expect_replay_status!r}, "
            f"got {result.replay_status!r}"
        )
    if result.escalated != scenario.expect_escalated:
        problems.append(f"escalated: expected {scenario.expect_escalated}, got {result.escalated}")
    return problems
