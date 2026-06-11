"""Support-ticket batch runner — process a queue, not a single prompt.

This is the operational answer to "who would use this and what does it do?":
load a batch of support tickets, run each through the full agent, auto-resolve
the low-risk reversible ones, escalate billing/account-risk ones, block unsafe
ones, write an audit record per ticket, and report how much work was safely
processed.

Outcome buckets (mutually exclusive, one per ticket):
  * auto_resolved  — agent responded/acted without a human (RESPOND disposition)
  * blocked_unsafe — an unsafe action was actively refused (guardrail block, or
                     an identity/scope/account-access attempt) — a *safe block*
  * human_handoff  — everything else escalated for a human (billing, plan,
                     low-confidence, unverifiable)

Run:
    python3 -m src.workflows.ticket_runner --input src/eval/data/sample_tickets.jsonl
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core import data
from ..core.models import AgentResponse, Disposition
from ..graph.pipeline import handle_turn
from ..observability.audit_ledger import AuditLedger, AuditRecord
from ..router.registry import get_classifier

DEFAULT_TICKETS = Path(__file__).resolve().parents[1] / "eval" / "data" / "sample_tickets.jsonl"

# Illustrative only — a stand-in for "agent handle time" per ticket. NOT a
# measured figure; the README labels it as an estimate.
MINUTES_PER_TICKET = 4

# Reverse the demo token table so a ticket's customer_id resolves to a token.
_CUSTOMER_TOKENS = {cid: tok for tok, cid in data._TOKENS.items()}


def token_for(customer_id: Optional[str]) -> Optional[str]:
    """Map a ticket's customer_id to an auth token (None = unauthenticated)."""
    if not customer_id:
        return None
    return _CUSTOMER_TOKENS.get(customer_id)


def load_tickets(path: Path | str = DEFAULT_TICKETS) -> list[dict[str, Any]]:
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def classify_outcome(record: AuditRecord, response: AgentResponse) -> str:
    """Bucket a processed ticket. Guardrail block or an account-access refusal is
    a *safe block*; other escalations are routine human handoffs; a RESPOND is an
    auto-resolution."""
    if response.guardrail_action == "block":
        return "blocked_unsafe"
    if response.disposition == Disposition.RESPOND:
        return "auto_resolved"
    if record.action_class == "account_access_change":
        return "blocked_unsafe"
    return "human_handoff"


@dataclass
class BatchResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    records: list[AuditRecord] = field(default_factory=list)
    source: Optional[str] = None

    # counts
    total: int = 0
    auto_resolved: int = 0
    human_handoff: int = 0
    blocked_unsafe: int = 0
    unsafe_auto_action: int = 0
    billing_escape: int = 0
    unsupported: int = 0
    category_matches: int = 0
    category_scored: int = 0
    failure_reasons: Counter = field(default_factory=Counter)

    @property
    def auto_resolution_rate(self) -> float:
        return self.auto_resolved / self.total if self.total else 0.0

    @property
    def human_escalation_rate(self) -> float:
        return self.human_handoff / self.total if self.total else 0.0

    @property
    def safe_block_rate(self) -> float:
        return self.blocked_unsafe / self.total if self.total else 0.0

    @property
    def unsupported_rate(self) -> float:
        """Share of tickets outside the v1 supported intent set (intent=unknown)
        — the work the current slice can't action, only escalate."""
        return self.unsupported / self.total if self.total else 0.0

    @property
    def category_match_rate(self) -> float:
        return self.category_matches / self.category_scored if self.category_scored else 0.0

    @property
    def manual_minutes_saved(self) -> int:
        return self.auto_resolved * MINUTES_PER_TICKET

    def top_failure_categories(self, n: int = 5) -> list[tuple[str, int]]:
        """Most common reasons a ticket was not auto-resolved."""
        return self.failure_reasons.most_common(n)

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "tickets_processed": self.total,
            "audit_records_written": len(self.records),
            "auto_resolved": self.auto_resolved,
            "human_handoff": self.human_handoff,
            "blocked_unsafe": self.blocked_unsafe,
            "auto_resolution_rate": round(self.auto_resolution_rate, 3),
            "human_escalation_rate": round(self.human_escalation_rate, 3),
            "safe_block_rate": round(self.safe_block_rate, 3),
            "unsafe_auto_action": self.unsafe_auto_action,
            "billing_escape": self.billing_escape,
            "unsupported_rate": round(self.unsupported_rate, 3),
            "category_match_rate": round(self.category_match_rate, 3),
            "manual_minutes_saved": self.manual_minutes_saved,
            "top_failure_categories": self.top_failure_categories(),
        }


def run_batch(
    tickets: list[dict[str, Any]],
    classifier_name: str = "nb_calibrated",
    store: Any | None = None,
    assume_customer: Optional[str] = None,
    source: Optional[str] = None,
) -> BatchResult:
    """Process every ticket end-to-end. If ``store`` is given (an AuditStore),
    each turn's audit record is also persisted durably.

    ``assume_customer`` supplies a sandbox auth identity for tickets whose own
    ``customer_id`` doesn't resolve to a token (i.e. imported public data). This
    lets routing/safety/handoff run on real language without faking that the
    ticket came from a verified account — it stays labeled as public-data
    validation, not production traffic."""

    classifier = get_classifier(classifier_name)
    assume_token = token_for(assume_customer) if assume_customer else None
    result = BatchResult(source=source)

    for t in tickets:
        ledger = AuditLedger()
        response = handle_turn(
            t["message"],
            auth_token=token_for(t.get("customer_id")) or assume_token,
            device_id=t.get("device_id"),
            classifier=classifier,
            classifier_name=classifier_name,
            audit=ledger,
        )
        record = ledger.records[-1]
        if store is not None:
            store.write(record, response)

        outcome = classify_outcome(record, response)
        result.total += 1
        result.records.append(record)
        if outcome == "auto_resolved":
            result.auto_resolved += 1
        elif outcome == "blocked_unsafe":
            result.blocked_unsafe += 1
        else:
            result.human_handoff += 1

        # work the v1 slice can't action, only escalate
        if record.intent == "unknown":
            result.unsupported += 1
        # why tickets didn't auto-resolve (top failure categories)
        if outcome != "auto_resolved":
            result.failure_reasons[record.handoff_reason or outcome] += 1

        # safety counters (must stay zero)
        if record.route == "auto_action" and record.blast_radius == "high":
            result.unsafe_auto_action += 1
        if record.action_class in ("billing_refund", "plan_change") and record.route != "human_escalation":
            result.billing_escape += 1

        # classifier category accuracy (only where the label is a real intent)
        expected = t.get("expected_category")
        if expected:
            result.category_scored += 1
            if record.intent == expected:
                result.category_matches += 1

        result.rows.append(
            {
                "ticket_id": t.get("ticket_id"),
                "customer_id": t.get("customer_id"),
                "message": t["message"],
                "expected_category": expected,
                "intent": record.intent,
                "route": record.route,
                "action_class": record.action_class,
                "outcome": outcome,
                "handoff_reason": record.handoff_reason or "",
                "owner": (response.handoff_context or {}).get("owner", ""),
            }
        )

    return result


def _print_summary(result: BatchResult) -> None:
    s = result.summary()
    if s["source"]:
        print(f"dataset source            : {s['source']}")
    print(f"tickets processed         : {s['tickets_processed']}")
    print(f"audit records written     : {s['audit_records_written']}")
    print(f"auto-resolved             : {s['auto_resolved']}")
    print(f"human handoff             : {s['human_handoff']}")
    print(f"blocked unsafe            : {s['blocked_unsafe']}")
    print(f"auto-resolution rate      : {s['auto_resolution_rate']:.3f}")
    print(f"human-escalation rate     : {s['human_escalation_rate']:.3f}")
    print(f"safe-block rate           : {s['safe_block_rate']:.3f}")
    print(f"unsafe auto-action        : {s['unsafe_auto_action']}")
    print(f"billing escape            : {s['billing_escape']}")
    print(f"unsupported rate          : {s['unsupported_rate']:.3f}")
    print(f"classifier category match : {s['category_match_rate']:.3f}")
    print(
        f"manual time saved (est.)  : {s['auto_resolved']} x {MINUTES_PER_TICKET} min"
        f" = {s['manual_minutes_saved']} min  [illustrative]"
    )
    if s["top_failure_categories"]:
        print("top failure categories    :")
        for reason, count in s["top_failure_categories"]:
            print(f"    {count:>4}  {reason}")


def render_markdown_report(result: BatchResult) -> str:
    """Partner-ready markdown summary of a batch run — the deliverable the README
    promises a design partner (metrics + safety + failure categories)."""
    s = result.summary()
    src = s["source"] or "support-ticket batch"
    lines = [
        f"# RelayOps batch report — {src}",
        "",
        "_Automated run of the RelayOps support agent. Safety counters and handoff "
        "quality are the load-bearing results; auto-resolution is an illustration of "
        "capacity, not a domain benchmark._",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tickets processed | {s['tickets_processed']} |",
        f"| Audit records written | {s['audit_records_written']} |",
        f"| Auto-resolution rate | {s['auto_resolution_rate']:.3f} |",
        f"| Human-escalation rate | {s['human_escalation_rate']:.3f} |",
        f"| Safe-block rate | {s['safe_block_rate']:.3f} |",
        f"| Unsupported rate | {s['unsupported_rate']:.3f} |",
        f"| **Unsafe auto-action** | **{s['unsafe_auto_action']}** |",
        f"| **Billing escape** | **{s['billing_escape']}** |",
        f"| Manual time saved (est.) | {s['manual_minutes_saved']} min _(illustrative)_ |",
        "",
        "## Outcome breakdown",
        "",
        f"- auto-resolved: {result.auto_resolved}",
        f"- human handoff: {result.human_handoff}",
        f"- blocked unsafe: {result.blocked_unsafe}",
        "",
        "## Top failure categories",
        "",
    ]
    if s["top_failure_categories"]:
        lines += [f"- {reason} — {count}" for reason, count in s["top_failure_categories"]]
    else:
        lines.append("- none (every ticket auto-resolved)")
    lines += [
        "",
        "## What got audited",
        "",
        "Every ticket produced a per-turn decision trace (access gate, route, action "
        "class, blast radius, guardrail verdict, and — when escalated — an "
        "owner-routed handoff with reason, evidence, and next step). Export the full "
        "evidence with `--export-csv` or the durable audit store.",
        "",
    ]
    return "\n".join(lines)


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RelayOps support-ticket batch runner")
    parser.add_argument("--input", default=str(DEFAULT_TICKETS), help="tickets JSONL")
    parser.add_argument("--classifier", default="nb_calibrated", help="intent classifier")
    parser.add_argument("--no-store", action="store_true", help="do not persist audit records")
    parser.add_argument("--db", default=None, help="audit store path (overrides default)")
    parser.add_argument("--export-csv", default=None, help="write per-ticket results CSV")
    parser.add_argument(
        "--assume-customer",
        default=None,
        help="sandbox customer_id (e.g. cust_alice) to authenticate imported public tickets",
    )
    parser.add_argument("--source", default=None, help="dataset label for the report")
    parser.add_argument("--report-md", default=None, help="write a partner-ready markdown report")
    args = parser.parse_args()

    store = None
    if not args.no_store:
        from ..observability.audit_store import AuditStore

        store = AuditStore(args.db) if args.db else AuditStore()

    tickets = load_tickets(args.input)
    print(f"processing {len(tickets)} tickets with classifier={args.classifier}\n")
    result = run_batch(
        tickets,
        classifier_name=args.classifier,
        store=store,
        assume_customer=args.assume_customer,
        source=args.source,
    )
    _print_summary(result)

    if args.export_csv:
        import csv

        out = Path(args.export_csv)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(result.rows[0].keys()))
            writer.writeheader()
            writer.writerows(result.rows)
        print(f"\nper-ticket results -> {out}")

    if args.report_md:
        out = Path(args.report_md)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown_report(result))
        print(f"partner report -> {out}")

    if store is not None:
        store.close()


if __name__ == "__main__":
    _main()
