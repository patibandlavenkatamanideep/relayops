"""Operator metrics calculator — pure, deterministic, read-only.

``operator_metrics(records, replay_metrics=None, cost_model=None)`` reduces a batch
of audit-record dicts (``AuditRecord.to_dict()`` or durable store rows) plus optional
replay evidence into an ``OperatorMetrics`` scoreboard.

It only reads: it never mutates the records it is given, never replays a turn,
calls a tool, or touches policy. The safety classifications below intentionally
mirror the Hermes finding taxonomy (``src.hermes.reviewer``) so the dashboard and
the reviewer agree, but the logic is duplicated here rather than imported to keep
this package free of any dependency on Hermes (Hermes depends on it, not the
other way round).

Route vocabulary (see ``observability.audit_ledger._route_label``):

  * ``respond``          — answered the customer directly (resolved);
  * ``auto_action``      — executed a tool/action for the customer (resolved);
  * ``human_escalation`` — handed off to a human (a handoff, not a resolution).
"""

from __future__ import annotations

from typing import Any, Optional

from .models import CostModel, OperatorMetrics

# Routes that count as the agent resolving the turn without a human.
_RESOLVED_ROUTES = ("respond", "auto_action")
_HANDOFF_ROUTE = "human_escalation"
_ACTION_ROUTE = "auto_action"


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _is_unsafe_escape(record: dict[str, Any]) -> bool:
    # A high-blast action that auto-executed instead of escalating (should be 0).
    return record.get("route") == _ACTION_ROUTE and record.get("blast_radius") == "high"


def _is_fail_closed(record: dict[str, Any]) -> bool:
    # A safety-critical layer could not render a verdict; turn forced to a handoff.
    return str(record.get("handoff_reason") or "").startswith("fail_closed:")


def _is_over_block(record: dict[str, Any]) -> bool:
    # A safe, informational request escalated for lack of grounded evidence.
    return record.get("handoff_reason") == "unverifiable"


def _replay_rates(
    replay_metrics: Optional[dict[str, Any]],
) -> tuple[Optional[float], Optional[float]]:
    """Pull replay success / mismatch rates from replay evidence.

    Returns ``(None, None)`` when no replay evidence was supplied — a placeholder,
    not a score, so a downstream alert skips it instead of reading a false 0/1.
    ``replay_success_rate`` is taken as-is from ``src.replay.verifier.replay_metrics``;
    the mismatch rate is derived from the mismatch count over the replay total.
    """
    if not replay_metrics:
        return None, None
    success = replay_metrics.get("replay_success_rate")
    total = replay_metrics.get("replay_total") or 0
    mismatch_count = replay_metrics.get("replay_mismatch_count") or 0
    mismatch_rate = _rate(mismatch_count, total) if total else 0.0
    return success, mismatch_rate


def operator_metrics(
    records: list[dict[str, Any]],
    replay_metrics: Optional[dict[str, Any]] = None,
    cost_model: Optional[CostModel] = None,
) -> OperatorMetrics:
    """Compute the operator scoreboard over ``records`` (+ optional replay metrics).

    Pure and side-effect free. An empty ``records`` list yields an all-zero
    scoreboard (no division by zero), with replay rates ``None`` unless supplied.
    """
    cost_model = cost_model or CostModel()
    total = len(records)

    resolved = sum(1 for r in records if r.get("route") in _RESOLVED_ROUTES)
    handoffs = sum(1 for r in records if r.get("route") == _HANDOFF_ROUTE)
    actions = sum(1 for r in records if r.get("route") == _ACTION_ROUTE)
    unsafe = sum(1 for r in records if _is_unsafe_escape(r))
    fail_closed = sum(1 for r in records if _is_fail_closed(r))
    over_block = sum(1 for r in records if _is_over_block(r))

    replay_success_rate, replay_mismatch_rate = _replay_rates(replay_metrics)

    # Efficiency: turns of work per resolved ticket, and a clearly-illustrative
    # cost estimate (total handling cost spread over the resolved tickets). Both
    # are 0.0 when nothing resolved, rather than dividing by zero.
    avg_turns = round(total / resolved, 4) if resolved else 0.0
    cost_per_resolved = (
        round(total * cost_model.cost_per_turn_usd / resolved, 4) if resolved else 0.0
    )

    return OperatorMetrics(
        total_turns=total,
        resolved_count=resolved,
        resolution_rate=_rate(resolved, total),
        handoff_count=handoffs,
        handoff_rate=_rate(handoffs, total),
        fail_closed_count=fail_closed,
        fail_closed_rate=_rate(fail_closed, total),
        unsafe_escape_count=unsafe,
        unsafe_escape_rate=_rate(unsafe, total),
        over_block_count=over_block,
        over_block_rate=_rate(over_block, total),
        action_execution_count=actions,
        action_execution_rate=_rate(actions, total),
        replay_success_rate=replay_success_rate,
        replay_mismatch_rate=replay_mismatch_rate,
        avg_turns_to_resolution=avg_turns,
        estimated_cost_per_resolved_ticket=cost_per_resolved,
    )
