"""Adversarial billing/account abuse eval for RelayOps v1.5.1.

Run:
    python3 -m src.eval.eval_billing_abuse

These cases exercise bad-faith billing and account-access requests through the
same batch runner used by the support-ops demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..workflows.ticket_runner import load_tickets, run_batch

DEFAULT_CASES = Path(__file__).resolve().parent / "data" / "adversarial_billing_tickets.jsonl"


@dataclass(frozen=True)
class BillingAbuseMetrics:
    total: int
    billing_escape: int
    unauthorized_credit_total: int
    unauthorized_credit_blocked: int
    social_engineering_total: int
    social_engineering_escalated: int
    verification_bypass_total: int
    verification_bypass_blocked: int

    @property
    def billing_escape_rate(self) -> float:
        return self.billing_escape / self.total if self.total else 0.0

    @property
    def unauthorized_credit_attempt_block_rate(self) -> float:
        return (
            self.unauthorized_credit_blocked / self.unauthorized_credit_total
            if self.unauthorized_credit_total
            else 0.0
        )

    @property
    def social_engineering_escalation_rate(self) -> float:
        return (
            self.social_engineering_escalated / self.social_engineering_total
            if self.social_engineering_total
            else 0.0
        )

    @property
    def verification_bypass_block_rate(self) -> float:
        return (
            self.verification_bypass_blocked / self.verification_bypass_total
            if self.verification_bypass_total
            else 0.0
        )


def _safe_non_auto(row: dict[str, Any]) -> bool:
    return row["outcome"] != "auto_resolved"


def evaluate_billing_abuse(
    path: Path | str = DEFAULT_CASES,
    classifier_name: str = "nb_calibrated",
) -> tuple[BillingAbuseMetrics, list[dict[str, Any]]]:
    tickets = load_tickets(path)
    result = run_batch(tickets, classifier_name=classifier_name)

    by_ticket = {row["ticket_id"]: row for row in result.rows}
    unauthorized_total = unauthorized_blocked = 0
    social_total = social_escalated = 0
    verification_total = verification_blocked = 0

    for ticket in tickets:
        row = by_ticket[ticket["ticket_id"]]
        abuse_type = ticket.get("abuse_type")
        if abuse_type == "unauthorized_credit_attempt":
            unauthorized_total += 1
            unauthorized_blocked += int(_safe_non_auto(row))
        elif abuse_type == "social_engineering":
            social_total += 1
            social_escalated += int(_safe_non_auto(row))
        elif abuse_type == "verification_bypass":
            verification_total += 1
            verification_blocked += int(_safe_non_auto(row))

    metrics = BillingAbuseMetrics(
        total=result.total,
        billing_escape=result.billing_escape,
        unauthorized_credit_total=unauthorized_total,
        unauthorized_credit_blocked=unauthorized_blocked,
        social_engineering_total=social_total,
        social_engineering_escalated=social_escalated,
        verification_bypass_total=verification_total,
        verification_bypass_blocked=verification_blocked,
    )
    return metrics, result.rows


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def main() -> None:
    metrics, rows = evaluate_billing_abuse()
    print(f"adversarial billing/account cases: {metrics.total}")
    print(f"billing_escape_rate: {_fmt(metrics.billing_escape_rate)} ({metrics.billing_escape}/{metrics.total})")
    print(
        "unauthorized_credit_attempt_block_rate: "
        f"{_fmt(metrics.unauthorized_credit_attempt_block_rate)} "
        f"({metrics.unauthorized_credit_blocked}/{metrics.unauthorized_credit_total})"
    )
    print(
        "social_engineering_escalation_rate: "
        f"{_fmt(metrics.social_engineering_escalation_rate)} "
        f"({metrics.social_engineering_escalated}/{metrics.social_engineering_total})"
    )
    print(
        "verification_bypass_block_rate: "
        f"{_fmt(metrics.verification_bypass_block_rate)} "
        f"({metrics.verification_bypass_blocked}/{metrics.verification_bypass_total})"
    )

    print("\nper-ticket outcomes:")
    for row in rows:
        print(
            f"{row['ticket_id']}  intent={row['intent']:<13} "
            f"route={row['route']:<16} outcome={row['outcome']}"
        )


if __name__ == "__main__":
    main()
