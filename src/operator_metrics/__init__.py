"""Operator metrics (v2.5) — the operator dashboard's deterministic scoreboard.

This package turns the read-only audit/replay evidence into the operator-facing
numbers a human runs the system by: how often turns resolve without a human, how
often they hand off, how often a safety layer fails closed, replay health, and a
rough efficiency/cost estimate. Every number is a pure function of the records it
is given — computing or reading a metric never replies, executes a tool, changes
policy, or mutates a record.

    audit / replay records -> operator_metrics (pure) -> dashboard + Hermes alerts

Hermes consumes these metrics for its advisory alerting layer; the metrics layer
itself knows nothing about thresholds or issues — it only counts.
"""

from __future__ import annotations

from .calculator import operator_metrics
from .models import CostModel, OperatorMetrics

__all__ = [
    "OperatorMetrics",
    "CostModel",
    "operator_metrics",
]
