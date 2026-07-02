# Design-Partner Guide — RelayOps v3.0

This guide is for a team that runs or buys customer support and wants to see what
RelayOps would do with **their** work — using a small, **redacted** sample of
tickets, with no integration, no credentials, and no execution.

You bring **20–100 redacted or synthetic tickets**. RelayOps produces a
deterministic, read-only report: which cases look safe to automate, which need a
human, which are sensitive, and where it has no policy or tool coverage yet. It is a
conversation starter backed by evidence — not a live agent run.

---

## 1. What you provide: accepted CSV / JSONL fields

One row/object per ticket. Schema in
[`src/importers/schemas.py`](../src/importers/schemas.py).

**Required**

| Field | Meaning |
|---|---|
| `ticket_id` | your identifier for the ticket |
| `customer_id` | a **redacted / synthetic** customer handle (not a real account id) |
| `message` | the customer's request, **PII removed** |
| `category` | your category label (free text; unknown labels are surfaced as gaps) |
| `expected_resolution` | what a correct outcome looks like |
| `sensitivity_level` | `low` / `medium` / `high` / `restricted` (synonyms normalized) |

**Optional** (used if present, ignored if not): `created_at`, `channel`,
`priority`, `historical_outcome`.

Any column **not** in this schema is dropped on import — a stray identifier or
secret in your export is never read.

## 2. Redaction requirements (do this before sending)

- Strip names, addresses, phone numbers, emails, account/card numbers, and any
  free-text PII from `message`.
- Use synthetic/opaque `customer_id` values.
- Keep only the fields above; delete everything else.
- A row with a missing required field, an empty message, or an unknown sensitivity
  is **skipped with a deterministic reason** (it will appear in the report's skip
  summary) — nothing partial is silently accepted.

See [DATA_RETENTION.md](DATA_RETENTION.md) for the full data policy.

## 3. How to run the import / report workflow

```bash
# validate + normalize the sample (prints accepted records + skip summary)
python3 -m src.importers.ticket_importer path/to/redacted_tickets.csv

# produce the design-partner report (Markdown, or JSON with --json)
python3 -m src.reports.design_partner_report path/to/redacted_tickets.jsonl
python3 -m src.reports.design_partner_report path/to/redacted_tickets.csv --json
```

Both accept `.csv` or `.jsonl`. Sample synthetic files live in
[`examples/`](../examples).

## 4. What the report means

The report ([`src/reports/design_partner_report.py`](../src/reports/design_partner_report.py))
is a **deterministic estimate** over each ticket's category + sensitivity —
explicitly **not** the live broker decision. It includes:

- **Intake totals** — received, imported, skipped (with reasons).
- **Category breakdown** — normalized category counts.
- **Automation vs. handoff estimates** — counts + the ticket ids in each bucket.
- **Unsafe / sensitive cases** — the ones a human should own.
- **Missing policy / tool candidates** — coverage gaps (below).
- **Suggested automations & policy handles** — where a scoped tool/policy could go.
- **Suggested human-review cases** — the sensitive/handoff set.
- **Replay-readiness notes** — how much of the sample would produce checkable
  audit/replay evidence.

### Interpreting automation candidates

A ticket is an **automation candidate** when its category is one RelayOps can, in
principle, handle with a low-blast, reversible action (device reset/status/FAQ/
greeting) **and** its sensitivity is not `high`/`restricted`. These are where an
agent could safely resolve the ticket on its own, under the audit trail.

### Interpreting handoff candidates

A ticket is a **handoff candidate** when it touches money/identity/unknown work, or
when its sensitivity is `high`/`restricted`. Safety-forward default: **sensitive ⇒
human**. These map to the escalation path, not automation.

### Interpreting missing policy / tool gaps

- **Missing policy candidate** — a category with no matching policy handle yet.
  RelayOps has no *rule* for it, so it would escalate. The report suggests a handle
  name to define.
- **Missing tool candidate** — an unmapped category whose message reads like an
  action (contains an action verb) that RelayOps has no *scoped tool* to perform.
  This is a capability gap, not just a policy gap.

Both are surfaced so a human can decide whether to add coverage — RelayOps never
invents a policy or tool on its own.

## 5. What RelayOps does **not** do here

- It does **not** call any vendor, CRM, billing, or telecom system.
- It does **not** execute any action, refund, or change — the report is read-only.
- It does **not** run the live agent or make broker decisions on your tickets.
- It does **not** store or transmit your tickets anywhere; processing is local.

## 6. From report to pilot

The gaps the report surfaces are exactly the roadmap for a pilot: define the missing
policy handles, add the scoped tools (behind the same access/approval controls),
and then the same tickets can run through the live control plane with full audit and
replay evidence. To see that live control plane end-to-end on synthetic tickets, run
the [scenario runner](SCENARIO_RUNNER_GUIDE.md).

Capture what you learn from each partner conversation in
[design-partner-notes.md](design-partner-notes.md).
