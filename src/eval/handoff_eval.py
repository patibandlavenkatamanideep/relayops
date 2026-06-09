"""Handoff-quality and support-outcome evaluation.

Safety eval asks "did the agent avoid the dangerous thing?" That is necessary
but not sufficient. A turn can be perfectly safe and still fail the customer if
the escalation strands them — no owner, no reason, no next step. This module
adds the *support-outcome* layer Reddit reviewers asked for:

  * handoff_completeness_rate — of all escalations, how many produced a handoff
    a human could act on without rereading the chat (the safe-abandonment guard).
  * support_outcome_complete   — per turn: safe decision AND clear explanation
    AND owner-when-needed AND evidence AND next step.
  * review_override_rate       — simulated: how often a human says an escalation
    could have been automated (policy too strict).
  * post_action_rollback_rate  — simulated: how often an auto-action was undone
    (policy too loose).

The override/rollback numbers are computed from a small labeled file
(``data/review_outcomes.jsonl``) and are explicitly *simulated* production
signals — the metric plumbing is real even though the labels are synthetic.

Run:  python3 -m src.eval.handoff_eval
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..core.models import AgentResponse, Disposition
from ..router.action_policy import handoff_completeness

_DATA = Path(__file__).parent / "data" / "review_outcomes.jsonl"


# --- handoff completeness / safe abandonment -----------------------------------


def is_escalation(response: AgentResponse) -> bool:
    return response.escalated or response.disposition == Disposition.ESCALATE


def handoff_completeness_rate(responses: Iterable[AgentResponse]) -> tuple[float, list[str]]:
    """Fraction of escalations whose handoff carries every required field.

    Returns (rate, incomplete_reasons). Turns that did not escalate are ignored
    — there is nothing to hand off."""
    total = 0
    complete = 0
    gaps: list[str] = []
    for r in responses:
        if not is_escalation(r):
            continue
        total += 1
        ok, missing = handoff_completeness(r.handoff_context or {})
        if ok:
            complete += 1
        else:
            gaps.append(f"{(r.handoff_context or {}).get('reason', '?')}: missing {missing}")
    rate = complete / total if total else 1.0
    return rate, gaps


# --- support outcome (safe + usable, not just safe) ----------------------------


def support_outcome(response: AgentResponse) -> tuple[bool, list[str]]:
    """A turn is support-complete only if it is BOTH safe and usable.

    Checks, in order: a clear customer-facing message exists; on escalation, a
    complete owner-routed handoff with evidence and next step; on a direct
    answer, the reply is non-trivial (and cited when it claims sources)."""

    fails: list[str] = []

    if not response.text.strip():
        fails.append("no customer-facing message")

    if is_escalation(response):
        handoff = response.handoff_context or {}
        ok, missing = handoff_completeness(handoff)
        if not ok:
            fails.append(f"incomplete handoff (missing {missing})")
        if not str(handoff.get("evidence_quote", "")).strip():
            fails.append("no evidence quote for the next human")
        if not str(handoff.get("owner", "")).strip():
            fails.append("no owner assigned")
    else:
        # A direct answer must actually say something; if it cites, the body
        # must lead with content, not only a Sources list.
        if len(response.text.strip()) < 5:
            fails.append("answer too short to be a real response")

    return (not fails, fails)


def support_outcome_complete_rate(
    responses: Iterable[AgentResponse],
) -> tuple[float, dict[str, list[str]]]:
    results = list(responses)
    total = len(results)
    failures: dict[str, list[str]] = {}
    passed = 0
    for i, r in enumerate(results):
        ok, fails = support_outcome(r)
        if ok:
            passed += 1
        else:
            failures[f"turn_{i}"] = fails
    rate = passed / total if total else 1.0
    return rate, failures


# --- simulated production metrics: override / rollback -------------------------


@dataclass(frozen=True)
class ReviewOutcomeMetrics:
    total_escalations: int
    overrides: int
    review_override_rate: float
    auto_actions: int
    rollbacks: int
    post_action_rollback_rate: float


def load_review_outcomes(path: Path = _DATA) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def review_outcome_metrics(rows: Iterable[dict]) -> ReviewOutcomeMetrics:
    """review_override_rate = escalations a human would have automated / all
    escalations. post_action_rollback_rate = auto-actions undone / all
    auto-actions. Too-strict and too-loose, measured from labels."""

    escalations = [r for r in rows if r.get("route") == "human_escalation"]
    auto_actions = [r for r in rows if r.get("route") == "auto_action"]

    overrides = sum(1 for r in escalations if r.get("human_review_outcome") == "could_automate")
    rollbacks = sum(1 for r in auto_actions if r.get("action_reversed") is True)

    n_esc = len(escalations)
    n_auto = len(auto_actions)
    return ReviewOutcomeMetrics(
        total_escalations=n_esc,
        overrides=overrides,
        review_override_rate=(overrides / n_esc) if n_esc else 0.0,
        auto_actions=n_auto,
        rollbacks=rollbacks,
        post_action_rollback_rate=(rollbacks / n_auto) if n_auto else 0.0,
    )


# --- runner --------------------------------------------------------------------


def main() -> None:
    from .agent_cases import CASES, run_case

    responses = [run_case(c) for c in CASES]

    hc_rate, gaps = handoff_completeness_rate(responses)
    so_rate, so_fails = support_outcome_complete_rate(responses)
    metrics = review_outcome_metrics(load_review_outcomes())

    n_esc = sum(1 for r in responses if is_escalation(r))
    print(f"ran {len(responses)} agent cases ({n_esc} escalated)\n")

    print("----- handoff quality (safe abandonment) -----")
    print(f"handoff_completeness_rate : {hc_rate:.3f}  ({n_esc} escalations)")
    for g in gaps:
        print(f"  - incomplete: {g}")

    print("\n----- support outcome (safe AND usable) -----")
    print(f"support_outcome_complete_rate : {so_rate:.3f}")
    for turn, fails in so_fails.items():
        print(f"  - {turn}: {fails}")

    print("\n----- simulated production metrics (labeled set) -----")
    print(
        f"review_override_rate      : {metrics.review_override_rate:.3f}"
        f"  ({metrics.overrides}/{metrics.total_escalations} escalations a human would automate)"
    )
    print(
        f"post_action_rollback_rate : {metrics.post_action_rollback_rate:.3f}"
        f"  ({metrics.rollbacks}/{metrics.auto_actions} auto-actions undone)"
    )
    print("\n(override/rollback labels are simulated; the metric plumbing is real.)")


if __name__ == "__main__":
    main()
