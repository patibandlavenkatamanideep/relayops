"""Run a scoped action inside an envelope, with idempotent replay (v2.2).

``execute_action`` opens an envelope, consults the idempotency ledger, runs the
scoped tool only if this logical action has not already succeeded, and records
the outcome. It returns the envelope plus the ``ToolResult`` the pipeline already
threads downstream — so wiring this in does not change tool-result handling.

The tool itself still enforces scope server-side; the envelope and ledger sit
*around* it, adding auditable identity and safe-retry. Scope is never weakened.
"""

from __future__ import annotations

from typing import Callable

from ..core.models import AccessContext, ToolResult
from .envelope import REPLAYED, SUCCEEDED, ActionEnvelope


class IdempotencyLedger:
    """Remembers succeeded actions by idempotency key so a duplicate request is
    replayed rather than re-run. In-process for the prototype; a real deployment
    would back this with a shared store (the key semantics are the contract)."""

    def __init__(self) -> None:
        self._succeeded: dict[str, ActionEnvelope] = {}

    def get(self, key: str) -> ActionEnvelope | None:
        return self._succeeded.get(key)

    def remember(self, envelope: ActionEnvelope) -> None:
        if envelope.status == SUCCEEDED:
            self._succeeded[envelope.idempotency_key] = envelope

    def reset(self) -> None:
        self._succeeded.clear()


# Process-default ledger. Swap or reset it in tests for isolation.
default_ledger = IdempotencyLedger()


def execute_action(
    ctx: AccessContext,
    *,
    turn_id: str,
    action: str,
    target_resource: str,
    policy_handle: str,
    blast_radius: str,
    reversibility: str,
    tool: Callable[[], ToolResult],
    ledger: IdempotencyLedger | None = None,
) -> tuple[ActionEnvelope, ToolResult]:
    """Execute ``tool`` inside an envelope, replaying a prior success if any."""
    ledger = ledger if ledger is not None else default_ledger
    envelope = ActionEnvelope.opened(
        turn_id=turn_id,
        customer_id=ctx.customer_id or "",
        action=action,
        target_resource=target_resource,
        policy_handle=policy_handle,
        blast_radius=blast_radius,
        reversibility=reversibility,
    )

    prior = ledger.get(envelope.idempotency_key)
    if prior is not None:
        # Idempotent hit: do NOT run the side effect again. Return a fresh
        # REPLAYED envelope carrying the original result, and reconstruct the
        # ToolResult so downstream sees the same success.
        envelope.status = REPLAYED
        envelope.result = dict(prior.result)
        envelope.completed_at = envelope.created_at
        return envelope, ToolResult(ok=True, data=dict(prior.result))

    result = tool()
    if result.ok:
        envelope.succeed(result.data)
        ledger.remember(envelope)
    else:
        envelope.fail(result.error or "tool_failed")
    return envelope, result
