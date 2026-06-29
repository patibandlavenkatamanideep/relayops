"""External action envelopes (v2.2).

A side-effecting action (a device reset, a sent link) is wrapped in an
``ActionEnvelope`` before it runs: a structured record of what is being done, to
what resource, under which policy handle, with an idempotency key. The executor
runs the scoped tool inside the envelope and records the outcome; an
``IdempotencyLedger`` replays a previously succeeded action instead of running it
twice. This is the safe-retry / exactly-once boundary for actions that reach an
external system.
"""

from __future__ import annotations

from .envelope import (
    FAILED,
    PENDING,
    REFUSED,
    REPLAYED,
    SUCCEEDED,
    ActionEnvelope,
)
from .executor import IdempotencyLedger, default_ledger, execute_action

__all__ = [
    "ActionEnvelope",
    "PENDING",
    "SUCCEEDED",
    "FAILED",
    "REFUSED",
    "REPLAYED",
    "IdempotencyLedger",
    "execute_action",
    "default_ledger",
]
