# Deployment Architecture — RelayOps v3.0

How RelayOps is shaped to run: a safe local/demo mode today, and a clear path to a
production deployment that keeps the same control plane. Nothing here enables real
vendor calls, real execution, or real customer data.

---

## 1. Local demo mode (default)

Everything runs on one machine with no keys:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

# end-to-end lifecycle demo (no keys)
python -m src.scenarios.runner

# UI (Chat, Batch, Decision Console, Handoff Queue, Operator Review + Approval Console)
streamlit run src/ui/app.py

# HTTP service
uvicorn src.api.main:app --reload
```

- Reply composition is the **offline template**; the LLM arm is triple-gated off.
- Customer store defaults to **in-memory**, re-seeded with synthetic data.
- Audit store is local **SQLite** under `var/`.

## 2. Public demo posture (e.g. Railway)

The repo ships a `Dockerfile`, `railway.toml`, and `Procfile` for a container
deploy. The public image is deliberately **template-only**:

- Built with `pip install -c constraints/railway.txt .` — a pinned, reproducible
  core set with **no** `[dev]`, `[llm]`, `[embeddings]`, or `[judge]` extras, and
  **no** Anthropic SDK. (Keep `pyproject.toml`'s direct pins equal to the versions
  in `constraints/railway.txt`, or the build fails on a resolver conflict.)
- Required env for a shared deploy:
  - `RELAYOPS_JWT_SECRET` = a long random string (**always set this**).
  - `RELAYOPS_COMPOSER=template`, `RELAYOPS_ALLOW_LLM=false`, and **no** LLM key.
- Optional durability: point `RELAYOPS_AUDIT_DB` / `RELAYOPS_CUSTOMER_DB` at a
  persistent volume path if you want state to survive restarts.

**What must remain disabled in any public demo:** the LLM composer arm, any real
vendor/CRM/telecom/payment integration, real customer data, and real execution.

## 3. Safe environment variables (recap)

See [`.env.example`](../.env.example) and
[PILOT_READINESS.md §3](PILOT_READINESS.md#3-environment-variables). The safety-
critical ones for a hosted deploy:

| Variable | Set to | Why |
|---|---|---|
| `RELAYOPS_JWT_SECRET` | long random string | signs bearer tokens |
| `RELAYOPS_COMPOSER` | `template` | offline, deterministic replies |
| `RELAYOPS_ALLOW_LLM` | `false` | master off-switch for the model arm |
| `ANTHROPIC_API_KEY` | *(unset)* | no key ⇒ no spend, no LLM |
| `RELAYOPS_RATE_LIMIT` / `RELAYOPS_RATE_WINDOW` | tune to traffic | per-caller throttle |

## 4. Component topology

```
        ┌──────────────┐      ┌───────────────────────────┐
client ─▶│  API (FastAPI)│─────▶│  turn pipeline (src/graph) │
        │  auth + rate  │      │  gate→router→broker→tools  │
        └──────┬───────┘      │  →guardrail→respond/handoff │
               │              └──────────────┬────────────┘
               │                             │ writes
               ▼                             ▼
        ┌──────────────┐            ┌──────────────────┐
        │ customer store│            │  audit store      │
        │  (SQLite/mem) │            │  (SQLite, local)  │
        └──────────────┘            └────────┬─────────┘
                                             │ reads (read-only)
                                             ▼
                         Hermes review · operator metrics · replay
                         verification · approval console/export ·
                         scenario runner
```

The **operator side is strictly read-only** over the audit store: it produces
evidence and advice, never actions.

## 5. Production path (designed, not built)

Each step preserves the control plane; only the backing systems change:

| Area | Prototype | Production path |
|---|---|---|
| **Managed database** | Local SQLite / in-memory | Managed Postgres (or equivalent) for customers + audit; audit made append-only / hash-chained |
| **External auth** | Signed HS256 + opaque demo tokens | Real OAuth/OIDC provider; short-lived tokens; key rotation; secret manager |
| **Vendor integrations** | Local scoped tools on synthetic data | Real tools (CRM, billing, telecom) behind the *same* MCP scope checks and approval gate |
| **Execution** | No real side effects | Real execution — still wrapped in an action envelope and gated by human approval for high-risk |
| **Rate limiting** | In-process | Shared limiter (Redis/gateway) |
| **Rollout** | Single local env | Shadow → canary → full, comparing audit/replay/operator-metrics against a baseline |
| **Composer** | Deterministic template | Frontier model drafting, vetted by the *same* guardrail |

The invariant does not move: **the model proposes, the broker decides, the tool
server executes only allowed scoped requests, high-risk actions require human
approval, and the human/operator remains accountable** — whether the tools are
synthetic (today) or real (production).

## 6. Rollback / safety

- The public deploy is stateless-by-default (in-memory customer store); a bad
  release is rolled back by redeploying the previous image.
- Because the composer is template-only and no real execution exists, a public demo
  failure cannot cause customer-visible harm or spend.
- `constraints/railway.txt` pins the exact build set for reproducibility; regenerate
  it (per its header) whenever core deps change, and keep `pyproject.toml`'s direct
  pins consistent with it.
