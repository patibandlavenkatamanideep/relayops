"""End-to-end scenario runner (v2.9).

Connects every RelayOps layer into one readable, deterministic demo: run a single
synthetic, redacted ticket and watch it traverse the full control plane —

    ingest → auth/scope → broker decision → action envelope → tool boundary →
    approval requirement → audit record → replay verification → operator metrics →
    Hermes review/alerting → approval/audit export → final report

Instead of explaining each module separately, run one scenario and see the whole
lifecycle. Deterministic and local: no vendor calls, no credentials, no real
customer data, no real external execution; a human/operator stays accountable.
"""

from __future__ import annotations

from .models import (
    STAGES,
    Scenario,
    ScenarioResult,
    StageResult,
    check_expectations,
)
from .runner import render_markdown, run_scenario, run_scenarios
from .sample_scenarios import (
    SAMPLE_SCENARIOS,
    get_sample,
    load_scenario,
)

__all__ = [
    "STAGES",
    "Scenario",
    "ScenarioResult",
    "StageResult",
    "check_expectations",
    "run_scenario",
    "run_scenarios",
    "render_markdown",
    "SAMPLE_SCENARIOS",
    "get_sample",
    "load_scenario",
]
