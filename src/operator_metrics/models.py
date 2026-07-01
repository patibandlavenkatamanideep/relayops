"""Operator metrics data types.

``OperatorMetrics`` is the v2.5 operator scoreboard: the counts and rates a human
uses to run the system, plus a rough efficiency/cost estimate. ``CostModel`` holds
the illustrative assumptions behind the efficiency numbers so they are explicit
and overridable rather than magic constants.

Both are plain, frozen-ish dataclasses carrying data only — the metrics layer is
read-only and produces evidence, never actions. Replay fields are ``Optional``:
they are ``None`` when no replay evidence was supplied, exactly like
``handoff_completeness_score`` in the safety metrics — a placeholder, not a score,
so a downstream alert skips them instead of scoring a false breach.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

# Illustrative default support-handling cost per turn (USD). Matches the
# "time saved" framing in the batch runner: a rough, clearly-labelled estimate
# for the dashboard, not a measured figure.
DEFAULT_COST_PER_TURN_USD: float = 0.25


@dataclass(frozen=True)
class CostModel:
    """Assumptions behind the efficiency/cost metrics. Defaults are illustrative
    and meant to be overridden per environment; they make the cost estimate
    explicit instead of a buried constant."""

    cost_per_turn_usd: float = DEFAULT_COST_PER_TURN_USD


@dataclass
class OperatorMetrics:
    """The operator dashboard scoreboard over a batch of audit records.

    Resolution/handoff/safety rates are derived purely from the audit records;
    replay rates are passed through from replay evidence when available (``None``
    otherwise). The efficiency block is a clearly-illustrative estimate.
    """

    total_turns: int
    # Resolution / handoff.
    resolved_count: int
    resolution_rate: float
    handoff_count: int
    handoff_rate: float
    # Safety (mirrors the Hermes finding taxonomy, here as operator rates).
    fail_closed_count: int
    fail_closed_rate: float
    unsafe_escape_count: int
    unsafe_escape_rate: float
    over_block_count: int
    over_block_rate: float
    # Actions actually executed against the world.
    action_execution_count: int
    action_execution_rate: float
    # Replay health (None when no replay evidence supplied).
    replay_success_rate: Optional[float]
    replay_mismatch_rate: Optional[float]
    # Efficiency.
    avg_turns_to_resolution: float
    estimated_cost_per_resolved_ticket: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
