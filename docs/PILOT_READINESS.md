# Pilot Readiness — RelayOps v3.0

RelayOps is a **control plane for AI customer-support agents**: the deterministic
safety layer that decides what an agent is allowed to do, executes only scoped
actions, holds high-risk actions for a human, and records auditable evidence of
every decision. v3.0 packages the project as a **production-shaped pilot** so a
founder, hiring manager, AI team, or design partner can evaluate it quickly.

This is a **working prototype with production-shaped architecture** — not a
production deployment. It runs end to end locally, with **no real customers, no
real production traffic, no real vendor execution, and no real payment/refund
execution**. See [What is prototype vs. production](#7-prototype-vs-production).

---

## 1. The control plane at a glance

A single customer turn flows through the full plane. Every box is real code in
this repo; the [scenario runner](SCENARIO_RUNNER_GUIDE.md) drives one ticket
through all of it and prints the per-stage evidence.

```
                         ┌─────────────────────────── operator side (read-only) ──────────────────────────┐
CUSTOMER / TICKET        │  Hermes review + alerting · operator metrics · replay verification · approval    │
      │                  │  console + audit export · scenario runner · design-partner report                │
      ▼                  └────────────────────────────────────────────────────────────────────────────────┘
  API boundary  ── auth (signed bearer) + per-caller rate limit
      │
      ▼
  Deterministic access gate ── authenticate caller, resolve customer scope (NOT an LLM)
      │
      ▼
  Router / classifier ── tiered; cheap classifier first, frontier only when needed
      │
      ▼
  RAG / FAQ evidence path ── grounded answers only; no grounding ⇒ escalate
      │
      ▼
  Pre-action intent packet ── structured model proposal (the model PROPOSES)
      │
      ▼
  Policy broker ── deterministic allow / block / escalate (the broker DECIDES)
      │
      ▼
  Action envelope ── wraps a side-effecting action (id, policy handle, idempotency)
      │
      ▼
  MCP-style tool server ── executes ONLY allowed, scoped requests (server-side scope check)
      │
      ▼
  Human approval queue ── high-risk actions HELD for a human before execution
      │
      ▼
  Guardrail ── independent check on the candidate reply (offers/price/PII/tone)
      │
      ▼
  Respond OR hand off ── final reply built from the broker decision, never raw model output
      │
      ▼
  Audit ledger ── deterministic per-turn decision record (the evidence trail)
```

| Layer | Module | One-line role |
|---|---|---|
| API boundary | [`src/api`](../src/api) | FastAPI service: `/v1/turn`, `/v1/auth/login`, `/v1/audit/{id}`, `/v1/operator/review`, … |
| Auth + rate limit | [`src/api/auth.py`](../src/api/auth.py), [`src/api/ratelimit.py`](../src/api/ratelimit.py) | Signed HS256 bearer envelope + per-caller turn-rate limit |
| Customer/auth datastore | [`src/core/customer_store.py`](../src/core/customer_store.py) | SQLite customer/device/token store (synthetic data) |
| Access gate | [`src/access/gate.py`](../src/access/gate.py) | Deterministic, non-LLM permission/scope resolution |
| Router / classifier | [`src/router`](../src/router) | Tiered routing; keyword baseline + fine-tuned classifier |
| RAG / FAQ evidence | [`src/rag`](../src/rag) | Hybrid retrieval with a similarity threshold; cited answers |
| Guardrail | [`src/guardrails`](../src/guardrails) | Independent block/redact on the candidate reply |
| Policy broker | [`src/router/policy_broker.py`](../src/router/policy_broker.py) | Deterministic allow/block/escalate + decision packet |
| Action envelope | [`src/actions`](../src/actions) | Wraps side-effecting actions; idempotency ledger |
| Tool server boundary | [`src/tools`](../src/tools), [`src/mcp`](../src/mcp) | MCP-style scoped tool execution, server-side scope check |
| Replay verification | [`src/replay`](../src/replay) | Deterministic re-check of an audited flow for drift |
| Operator metrics | [`src/operator_metrics`](../src/operator_metrics) | Safety/usefulness scoreboard over audit records |
| Hermes review + alerts | [`src/hermes`](../src/hermes) | Read-only advisory findings + threshold alerts |
| Ticket import/report | [`src/importers`](../src/importers), [`src/reports`](../src/reports) | Redacted-sample intake + design-partner report |
| Human approval queue | [`src/approval`](../src/approval) | Holds high-risk actions for human approval |
| Approval console/export | [`src/ui/app.py`](../src/ui/app.py), [`src/approval/export.py`](../src/approval/export.py) | Operator review UI + JSON/Markdown audit export |
| Scenario runner | [`src/scenarios`](../src/scenarios) | Drives one ticket through the whole lifecycle |
| Human accountability | — | A human owns every escalated or held case |

**Core invariant.** The model proposes. The broker decides. The action envelope
wraps. The tool server executes only allowed scoped requests. High-risk actions
require human approval before execution. The audit ledger records every state.
Replay verification checks consistency. Operator metrics summarize
safety/usefulness. Hermes reviews traces and alerts. The scenario runner
demonstrates the lifecycle. **The human/operator remains accountable.**

---

## 2. Safe demo mode (the default)

The public/default posture is deliberately safe and needs **no keys**:

- **Reply composer = `template`** (offline, deterministic). The optional LLM arm
  is double-gated: it runs only if `RELAYOPS_COMPOSER=llm` **and**
  `RELAYOPS_ALLOW_LLM=true` **and** an `ANTHROPIC_API_KEY` is set. All three are
  off by default, so a public deploy cannot spend a key by accident.
- **No real vendor calls, no real customer data, no real execution.** Tools mutate
  only the local synthetic datastore.
- **Customer store defaults to in-memory**, re-seeded per process with synthetic
  customers (`cust_alice`, `cust_bob`) and devices.

---

## 3. Environment variables

All optional. Defaults are safe; unset means "offline / synthetic". Full list in
[`.env.example`](../.env.example).

| Variable | Purpose | Safe default |
|---|---|---|
| `RELAYOPS_COMPOSER` | `template` (offline) or `llm` | `template` |
| `RELAYOPS_ALLOW_LLM` | Master switch for the LLM arm | `false` |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | LLM compose / eval judge | unset |
| `RELAYOPS_INTENT_MODEL` | Fine-tuned classifier dir/HF id | unset (keyword baseline) |
| `VOYAGE_API_KEY` | Dense RAG arm | unset (offline TF-IDF arm) |
| `RELAYOPS_AUDIT_DB` | Durable audit SQLite path | `var/relayops_audit.sqlite3` |
| `RELAYOPS_CUSTOMER_DB` | Customer/auth store path | `:memory:` |
| `RELAYOPS_JWT_SECRET` | Bearer-token signing secret | insecure dev default — **set in any shared env** |
| `RELAYOPS_JWT_TTL` | Bearer-token lifetime (s) | `3600` |
| `RELAYOPS_RATE_LIMIT` / `RELAYOPS_RATE_WINDOW` | Per-caller `/v1/turn` limit | `60` / `60` |

> In any shared environment, set a real `RELAYOPS_JWT_SECRET`. Keep hosted public
> demos on `RELAYOPS_COMPOSER=template`, `RELAYOPS_ALLOW_LLM=false`, and do **not**
> set an LLM key.

---

## 4. Local setup & validation

```bash
# 1. environment
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # or: pip install -r requirements if present

# 2. run one scenario end to end (no keys needed)
python -m src.scenarios.runner examples/scenarios/high_risk_refund.json
python -m src.scenarios.runner            # all five samples

# 3. try the demo UI (optional)
streamlit run src/ui/app.py

# 4. the API service (optional)
uvicorn src.api.main:app --reload         # GET /healthz, POST /v1/turn, ...
```

### Release verification (run before shipping any change)

```bash
.venv/bin/python -m unittest             # full test suite
.venv/bin/python -m ruff check .         # lint
.venv/bin/python -m ruff format --check . # formatting
```

All three must pass. The suite covers the access gate, broker, guardrail,
envelope, tool boundary, replay, operator metrics, approval queue/export, Hermes,
importer/report, and the scenario runner.

---

## 5. What requires human approval

Approval risk is a small deterministic table
([`src/approval/policy.py`](../src/approval/policy.py)):

| Risk | Examples | Requires approval? |
|---|---|---|
| `low` | device reset, send troubleshooting link, account read | no — scoped & reversible |
| `medium` | uncategorized actions | configurable (default: no) |
| `high` | refund / credit, billing adjustment, plan change, outbound sensitive message | **yes** |
| `critical` | account cancellation, contract/account modification, cross-customer or scope-sensitive action | **yes** |

A `pending`, `rejected`, or `expired` hold can never execute; an approved hold
executes at most once. Every approve/reject requires a **reviewer identity and
reason**. See the [Operator Review Guide](OPERATOR_REVIEW_GUIDE.md).

---

## 6. Data policy — allowed vs. forbidden

| ✅ Allowed | ❌ Forbidden |
|---|---|
| Synthetic customers/devices in the datastore | Real customer PII of any kind |
| Redacted / synthetic design-partner tickets | Real production support logs |
| Local audit + approval evidence (SQLite/JSON) | Real secrets/keys committed to the repo |
| Deterministic template replies | Real payment/refund/cancellation execution |
| Example scenario files | Real vendor API calls |

Details: [DATA_RETENTION.md](DATA_RETENTION.md) and
[SECURITY_MODEL.md](SECURITY_MODEL.md).

---

## 7. Prototype vs. production

| Concern | This prototype | Production would add |
|---|---|---|
| Customers | Synthetic seed store | Real IdP / customer system of record |
| Auth | Signed HS256 bearer + opaque demo tokens | Real OAuth/OIDC provider, key rotation |
| Tools | Local scoped tools on synthetic data | Real vendor tools behind the same scope checks |
| Actions | No real side effects | Real execution, still gated by envelope + approval |
| Datastore | SQLite (audit), in-memory customers | Managed DB, append-only/tamper-evident audit |
| Composer | Deterministic template (LLM optional, gated) | Frontier model drafting, same guardrail |
| Deploy | Local / single env | Shadow → canary → full ([DEPLOYMENT_ARCHITECTURE.md](DEPLOYMENT_ARCHITECTURE.md)) |

The **load-bearing safety ideas** — access gate, broker, envelope, scoped tool
boundary, approval queue, audit/replay, Hermes — are built and tested. What is
deferred is real integrations, not the control plane.

---

## 8. Intentionally NOT enabled in the public demo

- Real vendor / payment / CRM / telecom integrations.
- Real payment, refund, cancellation, or any external execution.
- Real customer PII or production traffic.
- The LLM composer arm (unless a private operator explicitly triple-enables it).

---

## 9. Where to go next

- [SECURITY_MODEL.md](SECURITY_MODEL.md) — the safety guarantees and how they hold.
- [DEPLOYMENT_ARCHITECTURE.md](DEPLOYMENT_ARCHITECTURE.md) — demo vs. production shape.
- [DATA_RETENTION.md](DATA_RETENTION.md) — what data is kept, where, and for how long.
- [DESIGN_PARTNER_GUIDE.md](DESIGN_PARTNER_GUIDE.md) — evaluate with 20–100 redacted tickets.
- [OPERATOR_REVIEW_GUIDE.md](OPERATOR_REVIEW_GUIDE.md) — inspect findings, metrics, approvals.
- [SCENARIO_RUNNER_GUIDE.md](SCENARIO_RUNNER_GUIDE.md) — the one-command end-to-end demo.
- [../DESIGN.md](../DESIGN.md) — full architecture narrative and deferred scope.
