"""Design-partner report (v2.6) — deterministic evaluation of a redacted sample.

Turns an ``ImportResult`` (from ``src.importers``) into the deliverable the README
promises a design partner: a read-only summary of which cases look safe to
automate, which need a human handoff, which are sensitive, and where RelayOps has
no policy or tool coverage yet. It renders to Markdown and to a JSON-compatible
dict.

Everything here is a pure function of the imported records plus the static policy
registry. It runs no model, executes no action, makes no external call, and does
not mutate the records it is given. The classifications are deterministic
heuristics over the ticket's *category* and *sensitivity* — an estimate to guide
a human conversation, explicitly not the live broker decision.

Estimation rules (deterministic):

  * automation candidate — an automatable category (device reset/status/FAQ/
    greeting) whose sensitivity is not high/restricted;
  * handoff candidate    — a money/identity/unknown category, OR any ticket whose
    sensitivity is high/restricted (safety-forward: sensitive => human);
  * unsafe/sensitive     — sensitivity high/restricted, or a billing/account case;
  * missing policy       — a category that maps to no known policy handle;
  * missing tool         — an unmapped category whose message reads like an action
    request (a capability RelayOps has no scoped tool for).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ..hermes.models import HermesReviewPacket
from ..importers.schemas import ImportResult, TicketRecord, normalize_category
from ..policy import registry as policy_registry

# Canonical buckets RelayOps can, in principle, automate (low-blast, reversible).
_AUTOMATABLE = frozenset({"reset_device", "device_status", "device_faq", "greeting"})
# Sensitivity levels that force a human review regardless of category.
_SENSITIVE_LEVELS = frozenset({"high", "restricted"})
# Buckets that touch money/identity — always a handoff, never automated.
_HANDOFF_BUCKETS = frozenset({"billing", "account", "unknown"})

# Canonical bucket -> the policy handle that governs it (registry ids).
_CATEGORY_POLICY = {
    "reset_device": policy_registry.DEVICE_RESET,
    "device_status": policy_registry.ACCOUNT_STATUS,
    "device_faq": policy_registry.FAQ_ANSWER,
    "greeting": policy_registry.GREETING,
    "billing": policy_registry.BILLING_REFUND,
    "account": policy_registry.ACCOUNT_CHANGE,
}
# Canonical bucket -> the scoped tool that serves it (FAQ/greeting need none).
_CATEGORY_TOOL = {
    "reset_device": "device_reset",
    "device_status": "account_lookup",
}

# Deterministic action-verb screen: an unmapped category whose message contains
# one of these reads like a capability we have no tool for (a missing-tool gap).
_ACTION_VERBS = (
    "reset",
    "restart",
    "reboot",
    "cancel",
    "change",
    "upgrade",
    "downgrade",
    "refund",
    "activate",
    "deactivate",
    "enable",
    "disable",
    "update",
    "transfer",
    "swap",
    "replace",
    "renew",
    "unlock",
)


def _slug(text: str) -> str:
    """A stable, lowercase, underscore slug for a proposed policy handle."""
    cleaned = [c if c.isalnum() else "_" for c in text.strip().lower()]
    slug = "".join(cleaned).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "uncategorized"


def _looks_like_action(message: str) -> bool:
    lowered = message.lower()
    return any(verb in lowered for verb in _ACTION_VERBS)


@dataclass
class DesignPartnerReport:
    """The read-only design-partner summary. All fields are plain data."""

    source: Any
    total_received: int
    total_imported: int
    skipped: list[dict[str, Any]]
    skipped_reason_counts: dict[str, int]
    category_breakdown: dict[str, int]
    automation_candidates: int
    handoff_candidates: int
    unsafe_sensitive_cases: int
    automation_ticket_ids: list[str]
    handoff_ticket_ids: list[str]
    unsafe_ticket_ids: list[str]
    missing_policy_candidates: list[str]
    missing_tool_candidates: list[str]
    suggested_automations: list[str]
    suggested_policy_handles: list[str]
    suggested_human_review_cases: list[str]
    replay_readiness_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        return _render_markdown(self)


def _analyze(record: TicketRecord) -> dict[str, Any]:
    """Deterministic per-ticket classification (no model, no side effects)."""
    bucket = normalize_category(record.category)
    sensitive = record.sensitivity_level in _SENSITIVE_LEVELS
    is_unsafe = sensitive or bucket in ("billing", "account")
    is_automation = bucket in _AUTOMATABLE and not sensitive
    is_handoff = bucket in _HANDOFF_BUCKETS or sensitive
    return {
        "bucket": bucket,
        "sensitive": sensitive,
        "is_unsafe": is_unsafe,
        "is_automation": is_automation,
        "is_handoff": is_handoff,
    }


def build_design_partner_report(result: ImportResult) -> DesignPartnerReport:
    """Assemble the design-partner report from an import result. Read-only."""
    records = result.records

    category_breakdown: Counter[str] = Counter()
    automation_ids: list[str] = []
    handoff_ids: list[str] = []
    unsafe_ids: list[str] = []
    missing_policy: dict[str, None] = {}  # ordered set of raw category labels
    missing_tool: dict[str, None] = {}
    automation_by_bucket: Counter[str] = Counter()
    suggested_handles: dict[str, None] = {}  # ordered set

    for record in records:
        info = _analyze(record)
        bucket = info["bucket"]
        category_breakdown[bucket] += 1

        if info["is_automation"]:
            automation_ids.append(record.ticket_id)
            automation_by_bucket[bucket] += 1
        if info["is_handoff"]:
            handoff_ids.append(record.ticket_id)
        if info["is_unsafe"]:
            unsafe_ids.append(record.ticket_id)

        handle = _CATEGORY_POLICY.get(bucket)
        if handle is not None:
            suggested_handles[handle] = None
        else:
            # Unmapped category: a policy coverage gap, and a tool gap when the
            # message reads like an action we cannot perform.
            raw = record.category.strip() or "(blank)"
            missing_policy[raw] = None
            suggested_handles[f"support.{_slug(raw)}.requires_definition"] = None
            if _looks_like_action(record.message):
                missing_tool[raw] = None

    suggested_automations = [
        f"{bucket}: {count} ticket(s) could auto-resolve"
        + (f" via the `{_CATEGORY_TOOL[bucket]}` tool" if bucket in _CATEGORY_TOOL else "")
        + " (low-blast, reversible)"
        for bucket, count in sorted(automation_by_bucket.items())
    ]

    skipped_reason_counts = Counter(s.reason.split(":", 1)[0] for s in result.skipped)

    return DesignPartnerReport(
        source=result.source,
        total_received=result.total_received,
        total_imported=result.imported_count,
        skipped=[s.to_dict() for s in result.skipped],
        skipped_reason_counts=dict(skipped_reason_counts),
        category_breakdown=dict(category_breakdown),
        automation_candidates=len(automation_ids),
        handoff_candidates=len(handoff_ids),
        unsafe_sensitive_cases=len(unsafe_ids),
        automation_ticket_ids=automation_ids,
        handoff_ticket_ids=handoff_ids,
        unsafe_ticket_ids=unsafe_ids,
        missing_policy_candidates=list(missing_policy),
        missing_tool_candidates=list(missing_tool),
        suggested_automations=suggested_automations,
        suggested_policy_handles=sorted(suggested_handles),
        suggested_human_review_cases=unsafe_ids,
        replay_readiness_notes=_replay_notes(records, len(automation_ids)),
    )


def _replay_notes(records: list[TicketRecord], automation_count: int) -> list[str]:
    scoped = sum(1 for r in records if r.customer_id)
    total = len(records)
    return [
        f"{scoped}/{total} imported ticket(s) carry a customer_id, so their turns "
        "would run under a checkable customer scope.",
        f"{automation_count} automation candidate(s) would produce action envelopes "
        "eligible for replay verification.",
        "Billing/account cases are advisory handoffs here — no external execution, "
        "so there is nothing to replay for them.",
        "This import performs no execution: replay readiness is an estimate over "
        "ticket metadata, not a live agent run.",
    ]


def to_hermes_findings(report: DesignPartnerReport) -> list[HermesReviewPacket]:
    """Bridge report gaps into advisory Hermes findings (read-only).

    This lets the operator layer *reference* the report as evidence without Hermes
    importing any file or acting. Every packet is advisory
    (``human_review_required=True``); this function reads the report and returns
    data, it changes nothing.
    """
    findings: list[HermesReviewPacket] = []
    for raw in report.missing_policy_candidates:
        findings.append(
            HermesReviewPacket(
                turn_id=f"design_partner:{_slug(raw)}",
                severity="medium",
                finding_type="missing_policy_candidate",
                summary=f"Imported tickets use category '{raw}' with no matching policy handle.",
                suggested_test="Add a broker test covering this category once a policy handle exists.",
                suggested_policy_gap=f"support.{_slug(raw)}.requires_definition",
            )
        )
    for raw in report.missing_tool_candidates:
        findings.append(
            HermesReviewPacket(
                turn_id=f"design_partner:{_slug(raw)}",
                severity="medium",
                finding_type="missing_tool_candidate",
                summary=f"Category '{raw}' reads like an action RelayOps has no scoped tool for.",
                suggested_test="Add a scoped-tool contract test once the capability is defined.",
                suggested_policy_gap=f"support.{_slug(raw)}.requires_definition",
            )
        )
    return findings


def _render_markdown(report: DesignPartnerReport) -> str:
    src = report.source or "redacted ticket sample"
    lines = [
        f"# RelayOps design-partner report — {src}",
        "",
        "_Read-only evaluation of a small **redacted / synthetic** ticket sample. "
        "Automation vs. handoff is a deterministic estimate over category and "
        "sensitivity, not a live broker decision. No agent was run and no external "
        "call was made._",
        "",
        "## Intake",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tickets received | {report.total_received} |",
        f"| Tickets imported | {report.total_imported} |",
        f"| Rows skipped | {len(report.skipped)} |",
        f"| Automation candidates | {report.automation_candidates} |",
        f"| Handoff candidates | {report.handoff_candidates} |",
        f"| **Unsafe / sensitive cases** | **{report.unsafe_sensitive_cases}** |",
        "",
    ]

    if report.skipped_reason_counts:
        lines += ["## Skipped rows", ""]
        for reason, count in sorted(report.skipped_reason_counts.items()):
            lines.append(f"- {reason} — {count}")
        lines.append("")

    lines += ["## Category breakdown", ""]
    if report.category_breakdown:
        for bucket, count in sorted(report.category_breakdown.items()):
            lines.append(f"- {bucket} — {count}")
    else:
        lines.append("- none (no tickets imported)")
    lines.append("")

    lines += ["## Suggested automations", ""]
    lines += (
        [f"- {item}" for item in report.suggested_automations]
        if report.suggested_automations
        else ["- none (no low-risk automatable categories in this sample)"]
    )
    lines.append("")

    lines += ["## Coverage gaps", ""]
    lines.append(
        "- Missing policy candidates: " + (", ".join(report.missing_policy_candidates) or "none")
    )
    lines.append(
        "- Missing tool candidates: " + (", ".join(report.missing_tool_candidates) or "none")
    )
    lines.append(
        "- Suggested policy handles: " + (", ".join(report.suggested_policy_handles) or "none")
    )
    lines.append("")

    lines += ["## Suggested human-review cases", ""]
    lines += (
        [f"- {tid}" for tid in report.suggested_human_review_cases]
        if report.suggested_human_review_cases
        else ["- none"]
    )
    lines.append("")

    lines += ["## Replay-readiness notes", ""]
    lines += [f"- {note}" for note in report.replay_readiness_notes]
    lines += [
        "",
        "---",
        "",
        "_Hermes may reference these findings as advisory evidence. It does not "
        "import files, execute actions, or mutate imported records; a human remains "
        "accountable for every decision._",
    ]
    return "\n".join(lines)


def _main() -> None:
    import argparse
    import json

    from ..importers.ticket_importer import import_file

    parser = argparse.ArgumentParser(
        description="RelayOps design-partner report over a redacted ticket sample (read-only)"
    )
    parser.add_argument("path", help="redacted tickets file (.csv or .jsonl)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    report = build_design_partner_report(import_file(args.path))
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.to_markdown())


if __name__ == "__main__":
    _main()
