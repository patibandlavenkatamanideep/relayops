"""Split safety metrics — the control-plane scoreboard.

Replaces the single blended "safe route" number with separate, honest signals,
each derived from the read-only Hermes finding taxonomy over audit traces:

  * unsafe_escape_rate   — high-blast actions that auto-executed (should be 0)
  * over_block_rate      — safe requests escalated for lack of grounding
  * fail_closed_rate     — turns where a safety layer couldn't render a verdict
  * guardrail_block_count — model proposals blocked before reaching a customer
  * handoff_completeness_score — fraction of escalations carrying a complete
    handoff (derivable from durable store rows; None when the input shape does
    not carry the flag, e.g. in-memory records)

Pure and read-only: it counts findings, it never acts. Numerator counts come from
``hermes.reviewer.review``; the denominator is the number of audit records.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Optional

from .reviewer import review


@dataclass
class SafetyMetrics:
    total_turns: int
    unsafe_escape_count: int
    unsafe_escape_rate: float
    over_block_count: int
    over_block_rate: float
    fail_closed_count: int
    fail_closed_rate: float
    guardrail_block_count: int
    handoff_completeness_score: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _handoff_completeness(records: list[dict[str, Any]]) -> Optional[float]:
    """Fraction of escalations with a complete handoff.

    1.0 when there are no escalations (nothing to complete). None when the input
    records don't carry the ``handoff_complete`` flag (e.g. in-memory records
    rather than durable store rows) — a placeholder, not a false score.
    """
    escalations = [r for r in records if r.get("route") == "human_escalation"]
    if not escalations:
        return 1.0
    scored = [r for r in escalations if r.get("handoff_complete") not in (None, "")]
    if not scored:
        return None
    complete = sum(1 for r in scored if str(r.get("handoff_complete")) == "1")
    return round(complete / len(scored), 4)


def safety_metrics(records: list[dict[str, Any]]) -> SafetyMetrics:
    """Compute split safety metrics over a batch of audit records."""
    total = len(records)
    counts = Counter(f.finding_type for f in review(records))
    unsafe = counts.get("unsafe_escape", 0)
    over = counts.get("over_block", 0)
    failed = counts.get("fail_closed", 0)
    return SafetyMetrics(
        total_turns=total,
        unsafe_escape_count=unsafe,
        unsafe_escape_rate=_rate(unsafe, total),
        over_block_count=over,
        over_block_rate=_rate(over, total),
        fail_closed_count=failed,
        fail_closed_rate=_rate(failed, total),
        guardrail_block_count=counts.get("guardrail_block", 0),
        handoff_completeness_score=_handoff_completeness(records),
    )
