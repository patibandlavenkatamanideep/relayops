# Design-Partner Notes

Purpose: answer "who is this for?" with evidence, not a guess. Each entry is one
conversation with someone who actually runs or buys telecom / subscription
support — what they would let an agent automate, what they would *never*, and the
audit + handoff requirements that would make them willing to test it.

These notes drive the roadmap: required audit fields here become columns in
[`audit_store.py`](../src/observability/audit_store.py); handoff requirements here
become fields in [`action_policy.build_handoff`](../src/router/action_policy.py).

> Status: template + seed hypotheses. Replace the seed rows with real
> conversations as they happen. Do **not** invent quotes — leave a field blank
> until you have a real answer.

## How to use

1. Copy the template block per person.
2. Capture verbatim where you can; mark inferences as `(inferred)`.
3. After each entry, note any **audit field** or **handoff requirement** that the
   current build is missing, and link it to a roadmap item.

## Template

```
### <name or anonymized handle>
- Role:
- Company type:            # MVNO / ISP / streaming / SaaS subscription / BPO ...
- Date:
- Pain (their words):
- Workflow they WOULD automate:
- Workflow they would NEVER automate:
- Required audit fields:   # what must be logged for them to trust it
- Handoff requirements:    # what the next human needs to continue
- Would they test it?      # yes / no / maybe + condition
- Gaps this surfaces:      # what RelayOps is missing -> roadmap item
```

## Entries

_(none yet — add real conversations here)_

## Seed hypotheses (to validate, not cite)

These are **assumptions to test**, explicitly not customer evidence:

- **MVNO support lead (inferred):** would automate device reset / status; would
  never automate refunds or plan changes without human review. Needs an
  immutable per-turn log with customer scope + guardrail verdict. → covered by
  v1.4 audit store; validate the exact required fields.
- **Subscription SaaS CX manager (inferred):** cares most about *handoff
  quality* — agents that escalate with no context waste the human. Needs owner +
  evidence quote + next step on every escalation. → covered by v1.3 handoff
  completeness; validate the deadline/SLA semantics.
- **Regulated telecom compliance (inferred):** wants exportable evidence (CSV/
  JSONL) and proof that money-touching intents never auto-resolve. → covered by
  v1.4 export + billing-escape counter; validate retention + tamper-evidence
  requirements (current store is not yet append-only/hash-chained).

## Open questions for the next conversation

- What is the single audit field whose absence would block a pilot?
- Where is the line between "auto-action" and "must escalate" for *their* risk
  tolerance — does it match the [action policy table](../README.md#action-policy-table)?
- Do they need real-time queue assignment, or is batch export enough for v1?
