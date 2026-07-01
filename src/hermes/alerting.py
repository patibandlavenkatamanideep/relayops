"""Hermes operator alerting (v2.5) — thresholded breach detection.

v2.4 made the safety and replay scoreboards *visible*; v2.5 makes them *loud*.
This module compares a metrics snapshot (the split safety metrics plus, when
available, the replay metrics) against named operator thresholds and emits a
deterministic ``AlertPacket`` for every breach. High/critical breaches also draft
a GitHub issue, exactly like the rest of the Hermes report.

Like all of Hermes this is read-only and NON-authoritative: an alert flags a
metric for a human, it never throttles traffic, pages, changes policy, or acts.
Every ``AlertPacket`` carries ``human_review_required=True`` and there is no
execute/apply surface — only pure functions returning data.

A threshold rule is ``(metric, comparison, threshold, severity, description)``:

  * ``comparison="max"`` — the metric must stay at or below ``threshold``; a
    breach is ``observed > threshold`` (e.g. ``unsafe_escape_rate`` must be 0).
  * ``comparison="min"`` — the metric must stay at or above ``threshold``; a
    breach is ``observed < threshold`` (e.g. ``replay_success_rate`` >= 0.99).

Missing metrics and ``None`` values (e.g. ``handoff_completeness_score`` on
in-memory records) are skipped, never scored as a breach.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from ..operator_metrics import operator_metrics
from .metrics import safety_metrics

# Breaches at or above this severity get a drafted GitHub issue (matches report.py).
_ISSUE_SEVERITIES = ("high", "critical")

# Severity rank, least -> most urgent (mirrors models.SEVERITIES).
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class AlertThresholds:
    """Operator breach thresholds. Defaults are deliberately strict; construct
    with overrides to relax a signal for a noisier environment.

    Each field is the boundary value; the comparison direction is fixed per
    metric (see ``rules``) because a rate that should be 0 can only breach upward
    and a success rate can only breach downward.
    """

    # Safety metrics (max — breach when the observed rate climbs above this).
    unsafe_escape_rate: float = 0.0  # high-blast auto-executions; must be 0
    fail_closed_rate: float = 0.05  # turns a safety layer couldn't render
    over_block_rate: float = 0.25  # safe requests escalated for grounding
    # Operator metrics (v2.5 dashboard).
    handoff_rate: float = 0.50  # max — too many turns escalating to a human
    # Replay metrics (v2.4).
    replay_success_rate: float = 0.99  # min — breach when replays drift below
    replay_blocked_count: int = 0  # max — scope / double-execution; must be 0
    # Handoff completeness (min — breach when escalations ship incomplete).
    handoff_completeness_score: float = 1.0

    def rules(self) -> list[tuple[str, str, Any, str, str]]:
        """The ordered (metric, comparison, threshold, severity, description) set."""
        return [
            (
                "unsafe_escape_rate",
                "max",
                self.unsafe_escape_rate,
                "critical",
                "High-blast action auto-executed instead of escalating.",
            ),
            (
                "replay_blocked_count",
                "max",
                self.replay_blocked_count,
                "critical",
                "Replay blocked on a scope or double-execution mismatch.",
            ),
            (
                "fail_closed_rate",
                "max",
                self.fail_closed_rate,
                "high",
                "Safety layer could not render a verdict on too many turns.",
            ),
            (
                "replay_success_rate",
                "min",
                self.replay_success_rate,
                "high",
                "Replay verification drift above the operator budget.",
            ),
            (
                "handoff_completeness_score",
                "min",
                self.handoff_completeness_score,
                "high",
                "Escalations are handed off without complete context.",
            ),
            (
                "handoff_rate",
                "max",
                self.handoff_rate,
                "high",
                "Too many turns are escalating to a human instead of resolving.",
            ),
            (
                "over_block_rate",
                "max",
                self.over_block_rate,
                "medium",
                "Safe requests escalated for lack of grounding.",
            ),
        ]


@dataclass
class AlertPacket:
    """A single advisory breach. ``human_review_required`` is True by default:
    Hermes raises the flag, a human decides what to do about it."""

    metric: str
    observed: Any
    threshold: Any
    comparison: str  # "max" (observed must be <=) or "min" (observed must be >=)
    severity: str
    summary: str
    human_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AlertReport:
    """Aggregated breach output: the alerts, the metrics they were judged
    against, and drafts for human review. Advisory only."""

    alerts: list[AlertPacket] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    suggested_github_issues: list[dict[str, str]] = field(default_factory=list)

    @property
    def breached(self) -> bool:
        """True when any threshold was crossed."""
        return bool(self.alerts)

    @property
    def has_critical(self) -> bool:
        """True when any breach is critical (the CI-gate signal)."""
        return any(a.severity == "critical" for a in self.alerts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "breached": self.breached,
            "has_critical": self.has_critical,
            "alerts": [a.to_dict() for a in self.alerts],
            "metrics": self.metrics,
            "summary": self.summary,
            "suggested_github_issues": self.suggested_github_issues,
        }


def _breached(observed: Any, comparison: str, threshold: Any) -> bool:
    return observed > threshold if comparison == "max" else observed < threshold


def evaluate_alerts(
    metrics: dict[str, Any], thresholds: Optional[AlertThresholds] = None
) -> list[AlertPacket]:
    """Compare a (possibly merged) metrics snapshot against thresholds.

    Pure and deterministic: a metric absent from ``metrics`` or carrying ``None``
    is skipped, never counted as a breach. Alerts come out in rule order, which
    is most-urgent-first.
    """
    thresholds = thresholds or AlertThresholds()
    alerts: list[AlertPacket] = []
    for metric, comparison, threshold, severity, description in thresholds.rules():
        if metric not in metrics:
            continue
        observed = metrics[metric]
        if observed is None:
            continue
        if _breached(observed, comparison, threshold):
            bound = "<=" if comparison == "max" else ">="
            alerts.append(
                AlertPacket(
                    metric=metric,
                    observed=observed,
                    threshold=threshold,
                    comparison=comparison,
                    severity=severity,
                    summary=(f"{description} {metric}={observed} (budget {bound} {threshold})."),
                )
            )
    alerts.sort(key=lambda a: _SEVERITY_RANK.get(a.severity, 0), reverse=True)
    return alerts


def _issue(alert: AlertPacket) -> dict[str, str]:
    return {
        "title": f"[{alert.severity}] alert: {alert.metric} breached operator threshold",
        "body": (
            f"Metric: {alert.metric}\n"
            f"Observed: {alert.observed}\n"
            f"Threshold: {alert.comparison} {alert.threshold}\n"
            f"Severity: {alert.severity}\n\n"
            f"{alert.summary}\n\n"
            "Advisory only — Hermes raised this from audit/replay evidence; a "
            "human decides any response. Hermes does not throttle, page, or act."
        ),
    }


def _summary(alerts: list[AlertPacket]) -> str:
    if not alerts:
        return "No breaches: every metric is within its operator threshold."
    worst = max(alerts, key=lambda a: _SEVERITY_RANK.get(a.severity, 0)).severity
    metrics = ", ".join(a.metric for a in alerts)
    return f"{len(alerts)} threshold breach(es) [worst={worst}]: {metrics}."


def report_from_metrics(
    metrics: dict[str, Any], thresholds: Optional[AlertThresholds] = None
) -> AlertReport:
    """Assemble an ``AlertReport`` from an already-computed metrics snapshot."""
    alerts = evaluate_alerts(metrics, thresholds)
    return AlertReport(
        alerts=alerts,
        metrics=dict(metrics),
        summary=_summary(alerts),
        suggested_github_issues=[_issue(a) for a in alerts if a.severity in _ISSUE_SEVERITIES],
    )


def build_alert_report(
    records: list[dict[str, Any]],
    replay_metrics: Optional[dict[str, Any]] = None,
    thresholds: Optional[AlertThresholds] = None,
) -> AlertReport:
    """Compute the safety + operator dashboard metrics over audit ``records``,
    merge in optional replay metrics (from ``src.replay.verifier.replay_metrics``),
    and raise alerts against the operator thresholds.

    Replay metrics are optional because the operator CLI reviews the audit store,
    which does not itself carry replay results; when omitted, only the safety,
    operator, and handoff thresholds are evaluated.

    Read-only and side-effect free: the safety and operator metrics are computed
    from copies of the record fields and the audit ``records`` are never mutated.
    """
    merged: dict[str, Any] = safety_metrics(records).to_dict()
    # Operator dashboard rates (handoff_rate, resolution_rate, etc.). Overlapping
    # safety keys carry identical values, so merge order is irrelevant.
    merged.update(operator_metrics(records, replay_metrics).to_dict())
    if replay_metrics:
        merged.update(replay_metrics)
    return report_from_metrics(merged, thresholds)
