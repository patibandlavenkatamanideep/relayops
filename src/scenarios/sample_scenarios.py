"""Synthetic sample scenarios (v2.9).

Five deterministic, redacted tickets, each chosen to prove one property of the
RelayOps control plane. All caller context uses the built-in demo customers
(cust_alice / cust_bob) and demo devices — no real customer data.

| Scenario                | What it proves                                   |
|-------------------------|--------------------------------------------------|
| device_status          | Normal safe automation path (scoped read).       |
| high_risk_refund       | Approval required before execution.              |
| cross_customer_block   | Scope violation is blocked at the tool boundary. |
| missing_evidence_faq   | Fail-closed / handoff when grounding is missing. |
| replay_mismatch        | Replay verification catches an inconsistency.    |
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Scenario

# tok_alice -> cust_alice (devices dev_a1 offline, dev_a2 online)
# tok_bob   -> cust_bob   (device  dev_b1)

DEVICE_STATUS = Scenario(
    id="device_status",
    title="Safe device status lookup",
    description="An authenticated customer asks for their device status — a scoped, "
    "read-only automation that resolves without a human.",
    message="what is my device status?",
    auth_token="tok_alice",
    expect_route="respond",
    expect_approval_required=False,
    expect_execution_blocked=False,
    expect_replay_status="pass",
    expect_escalated=False,
)

HIGH_RISK_REFUND = Scenario(
    id="high_risk_refund",
    title="High-risk refund request",
    description="A money-touching refund request. The broker escalates it and the "
    "approval queue holds it — a high-risk action cannot execute without a human.",
    message="I want a refund on my last bill",
    auth_token="tok_alice",
    expect_route="human_escalation",
    expect_approval_required=True,
    expect_execution_blocked=True,
    expect_replay_status="pass",
    expect_escalated=True,
)

CROSS_CUSTOMER_BLOCK = Scenario(
    id="cross_customer_block",
    title="Cross-customer access attempt",
    description="An authenticated customer tries to reset a device owned by a "
    "different customer. The scoped tool refuses on a scope violation.",
    message="reset my router please",
    auth_token="tok_bob",
    device_id="dev_a1",  # Alice's router — not Bob's to touch
    expect_route="human_escalation",
    # A cross-customer resource action is classified critical, so it is both
    # scope-blocked at the tool boundary AND approval-gated as a high-risk action.
    expect_approval_required=True,
    expect_execution_blocked=True,
    expect_replay_status="pass",
    expect_escalated=True,
)

MISSING_EVIDENCE_FAQ = Scenario(
    id="missing_evidence_faq",
    title="Missing-evidence FAQ",
    description="An informational question with no grounded knowledge-base match. "
    "Rather than answer from nothing, the turn fails closed to a human handoff.",
    message="what is your warranty replacement policy for a cracked screen?",
    auth_token="tok_alice",
    expect_route="human_escalation",
    expect_approval_required=False,
    expect_execution_blocked=False,
    expect_replay_status="pass",
    expect_escalated=True,
)

REPLAY_MISMATCH = Scenario(
    id="replay_mismatch",
    title="Replay mismatch (demonstration)",
    description="A safe lookup whose replayed audit record is deliberately drifted "
    "(synthetic) so the replay verifier catches a broker-decision inconsistency.",
    message="what is my device status?",
    auth_token="tok_alice",
    expect_route="respond",
    expect_approval_required=False,
    expect_execution_blocked=False,
    expect_replay_status="mismatch",
    expect_escalated=False,
    inject_replay_drift="broker",
)

SAMPLE_SCENARIOS: list[Scenario] = [
    DEVICE_STATUS,
    HIGH_RISK_REFUND,
    CROSS_CUSTOMER_BLOCK,
    MISSING_EVIDENCE_FAQ,
    REPLAY_MISMATCH,
]

_BY_ID = {s.id: s for s in SAMPLE_SCENARIOS}

# Fields accepted from a scenario JSON file (a stray key is ignored, so an export
# with extra columns can never smuggle unexpected state in).
_SCENARIO_FIELDS = frozenset(
    {
        "id",
        "title",
        "description",
        "message",
        "auth_token",
        "device_id",
        "expect_route",
        "expect_approval_required",
        "expect_execution_blocked",
        "expect_replay_status",
        "expect_escalated",
        "inject_replay_drift",
    }
)


def get_sample(scenario_id: str) -> Scenario:
    """Look up a built-in sample scenario by id."""
    return _BY_ID[scenario_id]


def load_scenario(path: str | Path) -> Scenario:
    """Load a scenario from a JSON file, reading only known fields."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("scenario file must contain a JSON object")
    known = {k: v for k, v in data.items() if k in _SCENARIO_FIELDS}
    if "id" not in known or "message" not in known:
        raise ValueError("scenario requires at least 'id' and 'message'")
    known.setdefault("title", known["id"])
    known.setdefault("description", "")
    return Scenario(**known)
