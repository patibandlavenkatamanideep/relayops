# Data Retention & Handling — RelayOps v3.0

What data RelayOps touches, where it lives, and the hard rules that keep the
project safe to run in public. Short version: **synthetic and redacted only, no
real PII, no secrets in the repo, and no real external execution.**

---

## 1. Data classes

| Class | Example | Where it lives | Retention |
|---|---|---|---|
| Synthetic customers/devices | `cust_alice`, `dev_a1` | Customer store (in-memory by default; SQLite if configured) | Ephemeral (re-seeded per process) unless a durable path is set |
| Redacted design-partner tickets | `examples/redacted_tickets.*` | Files provided by the operator | Local only; not sent anywhere |
| Audit records | per-turn decision trail | SQLite under `var/` (`RELAYOPS_AUDIT_DB`) | Local evidence; retained until you delete the DB file |
| Approval records + audit events | holds & decisions | In the approval queue / exported JSON/MD | Local; export is a point-in-time snapshot |
| Scenario outputs | lifecycle reports | stdout / a file you redirect to | Not persisted by the tool |

Nothing in these classes is transmitted to any external service in the default
(template-only, no-key) configuration.

## 2. Hard rules

**Allowed**
- Synthetic customer/device/token data.
- Redacted or fully synthetic support tickets for the design-partner workflow.
- Local audit and approval evidence (SQLite / JSON / Markdown).
- Deterministic template replies.

**Forbidden**
- Real customer PII of any kind, anywhere in the repo or examples.
- Real production support logs or transcripts.
- Real secrets, API keys, or tokens committed to the repo.
- Real payment / refund / cancellation / external execution.
- Real vendor API calls.

## 3. No real PII in the repo

- The seed store data is fictional ([`src/core/data.py`](../src/core/data.py)).
- Example ticket files under [`examples/`](../examples) are **synthetic / redacted**
  and labelled as such.
- The importer reads **only known schema fields**
  ([`src/importers/schemas.py`](../src/importers/schemas.py)); any extra column in a
  provided file is dropped, so a stray identifier or secret in an export is never
  loaded.

## 4. No secrets in examples

- `.env` is gitignored; [`.env.example`](../.env.example) contains blank keys and
  documentation only.
- The default `RELAYOPS_JWT_SECRET` is an obvious insecure placeholder for local
  dev; a real secret is set via the environment in any shared deploy and never
  committed.

## 5. Audit records as local evidence

The audit ledger is the project's evidence trail — a deterministic per-turn record
of gate/scope/route/tool/guardrail/handoff and the decision packets
([`src/observability`](../src/observability)). In the prototype it is a local SQLite
store; it is **local evidence for the operator**, not a shared or exported customer
dataset. Delete the DB file to clear it. A production deployment would replace this
with an append-only, tamper-evident store — see
[DEPLOYMENT_ARCHITECTURE.md](DEPLOYMENT_ARCHITECTURE.md).

## 6. Approval / audit export boundaries

The approval export ([`src/approval/export.py`](../src/approval/export.py)) is a
**read-only, point-in-time snapshot** of the approval queue (holds, decisions,
audit events, execution status). It:

- reads the queue and returns JSON/Markdown; it **mutates nothing**;
- contains only the fields the operator put into the queue (synthetic in the demo);
- is intended for operator review and hand-off, not for republishing customer data.

Treat an export as you would any evidence artifact: it inherits the sensitivity of
whatever went into the queue. In the demo that is synthetic, so exports are safe to
share.

## 7. Redaction expectations for design partners

If a design partner brings real tickets, they must **redact before import**:

- Remove names, addresses, phone numbers, emails, account/card numbers, and any
  free-text PII from the `message` field.
- Keep only the schema fields RelayOps uses (see
  [DESIGN_PARTNER_GUIDE.md](DESIGN_PARTNER_GUIDE.md)); drop everything else.
- Prefer a small sample (20–100 tickets) of representative, redacted cases.

RelayOps performs **no execution** on imported tickets — the design-partner report
is a deterministic, read-only estimate over ticket metadata, explicitly not a live
agent run and not a vendor action.
