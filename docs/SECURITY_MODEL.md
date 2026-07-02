# Security Model — RelayOps v3.0

RelayOps is a **safety/control plane** for AI support agents. Its security posture
is the product: the point is that a model — the least-trusted component — can never
widen its own permissions, invent an action, or reach a customer with an unvetted
reply. This document states the guarantees and how the code holds them.

> Scope note: this is a **prototype with production-shaped controls**. It uses
> synthetic data and no real execution. Where a control is demo-grade, that is
> called out explicitly.

---

## 1. Threat model (what we defend against)

| Threat | Defense |
|---|---|
| Prompt injection widening access | Deterministic access gate resolves scope **before** any model runs; nothing downstream re-derives permissions |
| Model inventing an action or offer | Broker decides from a policy table; guardrail vets the reply; the model only *proposes* |
| Cross-customer data access | Scoped tool server checks resource ownership **server-side** |
| High-risk action auto-executing | Approval queue holds high-risk actions for a human |
| A safety layer failing silently | Fail-closed: a layer that can't render a verdict forces a safe handoff |
| Undetected drift between runs | Replay verification compares audited flows and flags inconsistency |
| No evidence after the fact | Deterministic per-turn audit ledger + exportable approval evidence |
| Operator tooling taking actions | Hermes is structurally read-only/advisory |

Out of scope for the prototype: real network attackers, real secret management,
DDoS, and supply-chain hardening — these belong to a production deployment.

---

## 2. No production credentials in the public demo

- The default reply composer is the **offline deterministic template**. The LLM arm
  is **triple-gated**: `RELAYOPS_COMPOSER=llm` **and** `RELAYOPS_ALLOW_LLM=true`
  **and** an `ANTHROPIC_API_KEY` present. All default off, so a public deploy can
  never spend a key by accident (see [`src/composer`](../src/composer)).
- No vendor API keys are required to run the demo end to end.
- `.env` is gitignored; [`.env.example`](../.env.example) ships only blank keys and
  documentation. **No secret is committed to the repo.**

## 3. No real customer PII

- The customer/device/token store is seeded with **synthetic** records
  (`cust_alice`, `cust_bob`, demo devices) — [`src/core/data.py`](../src/core/data.py).
- Design-partner intake accepts only **redacted / synthetic** tickets and reads
  **only known schema fields**, so a stray column in an export is never imported
  ([`src/importers`](../src/importers)). See [DATA_RETENTION.md](DATA_RETENTION.md).

## 4. Scoped customer access (the access gate)

The first stage is a plain, **non-LLM** permission check
([`src/access/gate.py`](../src/access/gate.py)). It authenticates the caller and
resolves exactly what that customer may do into an `AccessContext`. Every later
stage — router, broker, tools — only *reads* that context; none re-derives
permissions. A prompt-injected model therefore cannot widen scope, because there is
no downstream code path that would let it.

## 5. Bearer-token API protection

The HTTP boundary ([`src/api`](../src/api)) uses a signed **HS256 bearer** envelope:
`POST /v1/auth/login` exchanges an opaque access token for a short-lived signed
token, and `POST /v1/turn` requires it ([`src/api/auth.py`](../src/api/auth.py)).

- Set a real `RELAYOPS_JWT_SECRET` in any shared environment — the default is
  insecure **on purpose** so local dev is frictionless.
- Token lifetime is `RELAYOPS_JWT_TTL` seconds.

## 6. Per-caller rate limiting

`/v1/turn` is rate-limited per caller ([`src/api/ratelimit.py`](../src/api/ratelimit.py)):
`RELAYOPS_RATE_LIMIT` requests per `RELAYOPS_RATE_WINDOW` seconds (default 60/60).
This is an in-process limiter suitable for the prototype; production would use a
shared store.

## 7. Fail-closed behavior

If a safety-critical layer raises (policy lookup, tool, RAG, composer, guardrail),
the turn **fails closed**: it forces a safe human handoff, builds the reply from
the broker packet (never raw model output), and records the reason in the audit
trail ([`src/graph/pipeline.py`](../src/graph/pipeline.py), `fail_closed`). A
failure never becomes an unvetted action or reply.

## 8. Action-envelope control

Every side-effecting action is wrapped in an `ActionEnvelope`
([`src/actions`](../src/actions)): action id, target resource, owning policy handle,
blast radius, reversibility, an idempotency key, and a lifecycle status. An
idempotency ledger replays a prior success instead of running it twice — the
safe-retry / exactly-once boundary. The envelope is recorded on the response and
the audit trail.

## 9. Tool-boundary execution (scoped, server-side)

Tools run behind an **MCP-style server** ([`src/tools`](../src/tools),
[`src/mcp`](../src/mcp)) that enforces scope **server-side**: it rejects a request
whose caller does not own the target resource (`scope_violation` /
`customer_scope_mismatch`) and validates the envelope before dispatch. The agent is
a client; it cannot bypass the check. This is what makes the cross-customer block in
the [scenario runner](SCENARIO_RUNNER_GUIDE.md) a *server-enforced* refusal, not a
prompt convention.

## 10. Approval requirement for high-risk actions

High/critical actions (refund/credit, billing adjustment, plan change, cancellation,
contract/account change, cross-customer) require **human approval before execution**
([`src/approval`](../src/approval)):

- `pending`, `rejected`, and `expired` holds can **never** execute.
- An approved hold executes **at most once** (single-use).
- Every approve/reject requires an explicit **reviewer identity and reason**.
- The action executor exposes an optional `approval_gate`; when it denies, the tool
  is not run and the envelope is `REFUSED` with `approval_required`.

## 11. Audit and replay evidence

- **Audit ledger** ([`src/observability`](../src/observability)) writes a
  deterministic per-turn record: gate, scope, route, tool call, guardrail verdict,
  handoff reason, evidence, decision packets, and action envelopes. Built purely
  from turn state — it cannot drift from what actually happened.
- **Replay verification** ([`src/replay`](../src/replay)) re-checks an audited flow
  against a replay and flags broker/envelope/tool/scope drift and double-execution
  risk. Scope and double-execution mismatches are safety-blocking. It never re-runs
  a tool or changes policy.

## 12. Hermes is read-only / advisory

Hermes ([`src/hermes`](../src/hermes)) sits on the operator side, after the audit
store. It reads traces and emits findings, alerts, suggested tests, and policy-gap
flags — every finding carries `human_review_required=True`. It **cannot** send a
reply, execute a tool, approve/reject an action, override the broker, or mutate a
record; the modules expose no such surface. See
[OPERATOR_REVIEW_GUIDE.md](OPERATOR_REVIEW_GUIDE.md).

---

## 13. What a production deployment must add

The prototype demonstrates the control logic; production hardening is deferred:

- Real IdP / OAuth-OIDC auth and key rotation (replacing demo tokens + dev secret).
- A shared rate-limit / session store (replacing the in-process limiter).
- Append-only, tamper-evident audit storage (the current SQLite store is not
  hash-chained).
- Real tool integrations behind the *same* scope checks and approval gate.
- Standard transport/network hardening (TLS termination, WAF, secret manager).

The security *design* — gate → broker → envelope → scoped tools → approval →
audit/replay, with Hermes advisory-only — is built and tested today.
