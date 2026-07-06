# Design Doc — Relay: A Telecom Customer-Service Agent

**Status:** Design + scoped build plan. This is the portfolio revision of the
original Relay design. The full system architecture is preserved as the target;
a deliberately scoped v1 vertical slice is what gets built first.

**Working codename:** Relay (productionized as **RelayOps**).

**Context:** Solo portfolio project. The goal is a system that (a) demonstrates
system-scale thinking, (b) actually runs end to end, and (c) shows realistic
scoping. All three matter; a half-wired system that doesn't run is the weakest
outcome.

**Inspiration:** Sierra-style telecom/subscription customer-service agent.

---

## 0. What Relay is, in one paragraph
Relay is an AI customer-service agent for a telecom/subscription company. It serves
customers over chat (voice is a designed-but-deferred extension). It can answer
questions and take low-risk, reversible actions on its own (device resets, sending
links, toggling settings) but escalates anything that touches billing, plans, or
payments to a human — and also escalates when it's unsure or when it detects
distress or abuse. A conversation turn flows through ingest → a deterministic
access gate (non-LLM) → a tiered model router (cheap first, frontier only when
needed) → a pre-action intent packet → a deterministic policy broker → scoped
tool/context execution only if allowed → a broker decision packet → final reply
composition → an independent guardrail layer → respond or hand off. Live account
data is reached only through scoped tools exposed over an MCP server — never via
RAG, prompt, or model weights. Knowledge is layered by how often it changes (RAG for
changing facts, config for small stable rules, fine-tuning only for the intent
classifier and tone). It is tested with simulations and a calibrated judge, and
measured by cost per successfully resolved task.

Core invariant: **the model proposes; the broker decides; the tool executes only
if allowed; the final reply is generated from the broker decision; the audit trail
records every state; Hermes reviews the audit trail; the human or organization
remains accountable.**

Hermes is a planned operator/review agent over audit evidence, not a
customer-support runtime decision-maker.

## 1. Portfolio scope — what gets built vs. what's designed-only
This is the most important section. The full design below is sound, but building all
of it solo is a trap. v1 is one intent, working end to end, with the parts that make
the system distinctive (the access gate, the guardrail, MCP scoping, a fine-tuned
classifier, an eval harness). Everything else is documented as designed, not yet
built — which is itself a strength to show a reviewer.

| Area | v1 — built | Deferred (designed, not built) |
|---|---|---|
| Channels | Chat only | Voice: ASR, barge-in/interruption, TTS, S2S |
| Intents | One ("reset my device") + a small classified set | Full intent taxonomy |
| Orchestration | Synchronous pipeline | Event-driven bus, parallel fan-out, speculative retrieval |
| Tenancy | Single brand | Multi-brand config isolation |
| Tool layer | MCP server, scoped tools | (same pattern, more tools) |
| Knowledge | Hybrid RAG, cited, small KB | Large per-brand KBs, re-embedding pipelines |
| Models | Tier 1 small + Tier 2 frontier routing | (same) |
| Fine-tuning | Intent classifier (with prompted baseline to beat) | Tone/brand-voice fine-tune |
| Guardrail | Built — truthfulness, allowed-offers, PII, can block | (same, more rules) |
| Evaluation | Adversarial cases + LLM judge | 5–15× sims, 3-agent, voice variations |
| Observability | Per-turn token/cost/latency + cost-per-resolved-task | Per-span trace tree, alerting |
| Deploy | (local / single env) | Shadow → canary → full |
| Learning | (out of scope) | Suggest-only draft-article loop |

The framing for the README: *"I designed the full production system and built
a vertical slice that proves the load-bearing ideas."*

## 2. Five terms before we go further
- **Agent** — not one LLM call. It observes, reasons, acts, observes again, in a loop.
- **Tiered routing** — a cheap, fast model (with a fine-tuned classifier) handles the
  easy majority; hard or low-confidence cases escalate to a frontier reasoning model.
  Cost follows difficulty.
- **Guardrail layer** — a separate gate after the model writes a reply: checks
  truthfulness, allowed offers, brand tone, PII. It can block. It is not inline logic.
- **Deterministic access gate** — a plain (non-LLM) permission check before any model
  runs: the agent only ever sees what the authenticated customer can see. No model
  can widen this.
- **Pre-action intent packet** — a structured model/router proposal before action:
  user request, interpreted intent, requested action, target resource, policy handle,
  evidence quote, confidence, ambiguity, and proposed safe response.
- **Policy broker / broker decision packet** — the deterministic allow/block/escalate
  decision: policy version, matched rule, reason code, missing evidence, owner,
  allowed next actions, and forbidden next actions. This packet is the source of
  truth for final response generation.
- **Policy handle / policy registry (v1.9)** — the stable identifier the broker
  stamps onto every decision (`policy_handle`). The registry (`src/policy`) is the
  single catalog of those handles — title, rationale, owner, headline disposition,
  blast radius, and the matched rules that resolve to each — and is *enforced*:
  tests drive the broker over representative turns and fail if it emits a handle
  the registry does not define, so no undocumented policy can ship.
- **Action envelope (v2.2)** — every side-effecting action (a device reset) runs
  inside an `ActionEnvelope` (`src/actions`): action id, target resource, owning
  policy handle, blast radius, reversibility, an idempotency key, and a lifecycle
  status (pending → succeeded / failed / refused, or replayed). An idempotency
  ledger replays a previously succeeded action instead of running it twice — the
  safe-retry boundary for actions that reach an external system. The envelope is
  recorded on the turn response and the audit trail.
- **Replay verification (v2.4)** — a deterministic, read-only check (`src/replay`)
  that compares a prior audited flow against its replay and reports mismatches:
  broker-decision drift, action-envelope drift, tool-response drift, missing
  audit records, customer/caller scope drift, and double-execution risk (a replay
  that re-ran an idempotent action instead of replaying it). Each mismatch carries
  a stable reason code and severity; scope and double-execution mismatches are
  safety-blocking. Replay metrics (`replay_success_rate`, mismatch / blocked /
  missing-audit counts) and Hermes findings make drift visible to a human. It
  never re-runs a tool or changes policy — the broker stays the authority.
- **Operator metrics dashboard with Hermes alerting (v2.5)** — two layers on top
  of the read-only audit/replay evidence. **Operator metrics**
  (`src/operator_metrics/`) reduce the records into the deterministic scoreboard a
  human runs the system by: `resolution_rate`, `handoff_rate`, `fail_closed_rate`,
  `unsafe_escape_rate`, `over_block_rate`, `replay_success_rate` /
  `replay_mismatch_rate`, `action_execution_rate`, `avg_turns_to_resolution`, and
  an illustrative `estimated_cost_per_resolved_ticket`. Each is a pure function of
  the records; computing a metric never replies, executes a tool, or changes
  policy. **Hermes alerting** (`src/hermes/alerting.py`) compares that combined
  metrics snapshot against named operator thresholds (`AlertThresholds`) and raises
  a deterministic `AlertPacket` for every breach: `unsafe_escape_rate` /
  `replay_blocked_count` above 0 are critical; replay drift, fail-closed,
  handoff-rate, and handoff-completeness shortfalls are high; over-block is medium.
  High/critical breaches draft a GitHub issue and the operator CLI's `--strict`
  flag exits non-zero on a critical breach (a CI gate over audit evidence). Both
  layers are read-only and NON-authoritative: every alert carries
  `human_review_required=True`; Hermes never throttles, pages, changes policy, or acts.
- **Redacted ticket import & design-partner report (v2.6)** — a deterministic,
  local, read-only intake for evaluating a small **redacted / synthetic** sample of
  a design partner's support tickets, with no vendor integration, credential, real
  PII, or external execution. **Import** (`src/importers/`) parses CSV/JSONL,
  validates required fields, normalizes sensitivity to `low/medium/high/restricted`,
  drops any non-schema column (so a stray secret is never loaded), and returns
  accepted `TicketRecord`s plus a per-row skip summary with deterministic reasons.
  **Report** (`src/reports/`) reduces the import into a design-partner summary
  (intake totals, category breakdown, automation vs. handoff estimates,
  unsafe/sensitive cases, missing policy/tool candidates, suggested automations and
  policy handles, human-review cases, replay-readiness notes) rendered to Markdown
  and a JSON dict. Classifications are a deterministic estimate over category +
  sensitivity, explicitly not the live broker decision. Hermes may *reference* the
  report's gaps as advisory findings but never imports a file, executes an action,
  or mutates a record — the human/operator stays accountable.
- **Human approval queue (v2.7)** — a deterministic, local hold for high-risk
  actions so a sensitive operation is reviewed by a human *before* it executes,
  without weakening the broker/envelope/tool-server/audit/Hermes control plane. A
  small policy table (`src/approval/policy.py`) maps a risk level to an approval
  requirement: `low` (scoped, reversible — device reset, account read) proceeds
  without approval; `medium` is configurable; `high` (refund/credit, billing
  adjustment, plan change, outbound sensitive message) and `critical` (account
  cancellation, contract/account modification, cross-customer or scope-sensitive
  action) require a human. The `ApprovalQueue` (`src/approval/queue.py`) holds each
  action as an `ApprovalRequest` and gates execution: `authorize_execution`
  succeeds only for an approved (single-use) or not-required action; `pending`,
  `rejected`, and `expired` actions are blocked and audited, so a rejected or
  expired action can never execute and an approved one executes at most once. Every
  approve/reject carries a reviewer identity and reason (an anonymous decision is
  refused), and every transition is recorded as an `ApprovalAuditEvent` on a
  queue-local trail that never mutates an unrelated audit or action record. The
  action executor (`src/actions/executor.py`) takes an optional `approval_gate`;
  when it denies, the tool is not run and the envelope is `REFUSED` with
  `approval_required`, while omitting it preserves prior behaviour exactly (the
  public demo path is unchanged). Hermes (`src/hermes/approval_review.py`) surfaces
  pending/rejected/expired high-risk approvals as read-only advisory findings; it
  structurally cannot approve, reject, or execute — the human/operator stays
  accountable. Deterministic and local throughout: no vendor calls, credentials,
  real PII, or payment/refund execution.
- **Operator approval console & audit export (v2.8)** — makes the v2.7 queue usable
  from an operator workflow by making high-risk action review visible and
  exportable, without changing the control plane or the public demo's safety
  posture. **Export** (`src/approval/export.py`) is a pure read over an
  `ApprovalQueue`: it groups holds by status (pending/approved/rejected/expired/
  not-required) and, per record, reports the approval id, linked action id, risk
  level, status, requester/caller id, customer id, reviewer id + reason (once a
  human decides), created/updated timestamps, the per-request audit-event history,
  and whether execution is `allowed`, `blocked` (pending/rejected/expired), or
  `consumed` (an approved single-use action already run). It renders to a JSON dict
  and Markdown and computes an *effective* status (a pending hold past its expiry
  reads as expired) without transitioning a request, recording an event, or
  executing anything. **Console** (`src/ui/app.py`, Operator Review tab) shows the
  grouped holds with risk level and per-record audit trail, JSON/Markdown export
  buttons, and optional approve/reject controls that require an operator identity
  and reason (anonymous/unexplained decisions are refused); approving only makes an
  action *eligible* to run once — the console never auto-executes and has no
  vendor/payment path, and demo records are labelled synthetic. Hermes references
  the same pending/rejected/expired holds as read-only advisory findings; it cannot
  approve, reject, execute, or mutate any approval record.
- **End-to-end scenario runner (v2.9)** — the layer that makes the whole project
  demoable as one flow. `src/scenarios` runs a single synthetic, redacted ticket
  through the entire control plane and records a deterministic, per-stage lifecycle:
  ingest → auth/scope → broker decision → action envelope → tool boundary → approval
  requirement → audit record → replay verification → operator metrics → Hermes
  review/alerting → approval/audit export → final report. It *composes the real
  modules* (pipeline, audit ledger, replay verifier, operator metrics, Hermes,
  approval queue/export) rather than re-implementing them, so the runner is
  evidence that the layers actually interlock — not a mock. Five samples each prove
  one property: `device_status` (safe scoped automation), `high_risk_refund`
  (approval required before execution), `cross_customer_block` (scope violation
  refused at the tool boundary), `missing_evidence_faq` (fail-closed handoff when
  grounding is missing), and `replay_mismatch` (the verifier catching an injected
  inconsistency). Output renders to a JSON dict or Markdown. It is deterministic and
  local — no vendor calls, no credentials, no real customer data, no real external
  execution; the only synthetic touch is the replay-drift injection, which mutates a
  *copy* of the replayed audit record so the live turn is unaffected. A
  human/operator remains accountable for every escalated or held case.
- **Production pilot readiness pack (v3.0)** — packaging, not new runtime surface.
  v3.0 makes the whole control plane easy to *evaluate* — by a founder, hiring
  manager, AI team, or design partner — without adding real credentials, vendor
  calls, customer data, or execution. It adds a documentation pack under `docs/`
  ([PILOT_READINESS](../docs/PILOT_READINESS.md), [SECURITY_MODEL](../docs/SECURITY_MODEL.md),
  [DEPLOYMENT_ARCHITECTURE](../docs/DEPLOYMENT_ARCHITECTURE.md),
  [DATA_RETENTION](../docs/DATA_RETENTION.md), [DESIGN_PARTNER_GUIDE](../docs/DESIGN_PARTNER_GUIDE.md),
  [OPERATOR_REVIEW_GUIDE](../docs/OPERATOR_REVIEW_GUIDE.md),
  [SCENARIO_RUNNER_GUIDE](../docs/SCENARIO_RUNNER_GUIDE.md)) that states the control-plane
  overview, the safe-demo checklist, the security model, the demo-vs-production
  boundary, the data policy, and how design partners and operators use the system.
  No runtime behavior, UI, or safety posture changes in v3.0; Hermes stays
  read-only/advisory and no real external execution is added.
- **Voice channel adapter (v3.2)** — voice as an input/output *channel*, not a new
  autonomous phone agent. `src/channels/voice` normalizes a **synthetic** call
  transcript into the exact request shape the pipeline already consumes (text +
  optional passthrough token/device) and carries `VoiceChannelMetadata` (channel,
  call_id, caller_id, customer_id, language, confidence, redaction notes) plus a
  `voice_<call_id>` turn id so the audit record ties back to the call. The adapter
  is a pure normalizer: it validates required fields, rejects an empty transcript,
  reads only known fields (a stray audio URL/secret is dropped), mints no
  authority (`customer_id`/`caller_id` are advisory; only a passed-through demo
  token authenticates, via the unchanged access gate), and — critically —
  *executes nothing, decides no risk, and connects to no voice provider* (no
  Twilio, phone number, audio, STT/TTS, or outbound call). Downstream is unchanged:
  a refund voice call still escalates, a cross-customer voice call is still refused
  at the scoped tool boundary, and the broker/envelope/approval/audit/replay/Hermes
  all behave exactly as for typed chat. See [VOICE_CHANNEL](../docs/VOICE_CHANNEL.md).
- **MCP (Model Context Protocol)** — the standard client/server boundary for agent
  tool access. Relay's tools (account lookup, device reset, send-link) live behind an
  MCP server that enforces per-customer scoping; the agent is an MCP client.

### The v3.0 control plane, end to end (how the v2.x layers fit)

By v3.0 the layers compose into one plane, each stage a checkable boundary. A single
turn (and the [scenario runner](../docs/SCENARIO_RUNNER_GUIDE.md) walks exactly this):

```
API boundary (auth + rate limit)
  → deterministic access gate (scope, non-LLM)
  → router / classifier (tiered)
  → RAG/FAQ evidence (grounded only; else escalate)
  → pre-action intent packet          [the model PROPOSES]
  → policy broker                     [the broker DECIDES]
  → action envelope (idempotent)      [the envelope WRAPS]
  → MCP-style scoped tool server      [executes only ALLOWED scoped requests]
  → human approval queue              [high-risk HELD for a human]
  → guardrail (independent)           [vets the candidate reply]
  → respond OR hand off               [reply built from the broker decision]
  → audit ledger                      [records every state]
     ↑ operator side (read-only): replay verification · operator metrics ·
       Hermes review/alerting · redacted ticket import/report · approval
       console/audit export · scenario runner
```

**The core invariant, stated once:** The model proposes. The broker decides. The
action envelope wraps. The tool server executes only allowed scoped requests.
High-risk actions require human approval before execution. The audit ledger records
every state. Replay verification checks consistency. Operator metrics summarize
safety/usefulness. Hermes reviews traces and alerts. The scenario runner demonstrates
the lifecycle. **The human/operator remains accountable.**

**Prototype vs. production boundary.** The load-bearing *control logic* is built and
tested: gate, broker, envelope, scoped tool boundary, approval queue, audit/replay,
Hermes (advisory), and the scenario runner. What is deliberately deferred is
*integration*, not the control plane: real IdP/OAuth auth, a managed and
tamper-evident datastore, real vendor tools (behind the same scope + approval gates),
real execution, a shared rate limiter, and a shadow→canary→full rollout. The
prototype uses synthetic data, offline template composition, and no real execution —
so it is safe to run in public while still demonstrating the production-shaped
architecture.

## 3. The v1.8.1 system, drawn once (synchronous, chat-only)
```
CUSTOMER REQUEST
      |
      v
ACCESS GATE
authn -> customer scope
      |
      v
MODEL / ROUTER
intent + confidence; the model proposes, it does not decide
      |
      v
PRE-ACTION INTENT PACKET
user request, interpreted intent, requested action, resource, policy handle,
evidence quote, confidence, ambiguity, proposed safe response
      |
      v
POLICY BROKER
deterministic allow / block / escalate / ask-clarification decision
      |
      +---- escalate/block ----> HUMAN HANDOFF
      |
      +---- ask clarification -> CLARIFICATION REPLY
      |
      +---- allow -----------> SCOPED TOOL / CONTEXT BROKER
                              tools + RAG; scope enforced server-side
                                    |
                                    v
                              TOOL / CONTEXT RESULT PACKET
                                    |
                                    v
BROKER DECISION PACKET <-----------+
policy version, matched rule, reason code, missing evidence, owner,
allowed next actions, forbidden next actions
      |
      v
FINAL REPLY COMPOSER
generated from the broker decision packet, not raw model proposal
      |
      v
GUARDRAIL LAYER
truthfulness · allowed offers · tone · PII
      |
      +---- fail -------------> HUMAN HANDOFF
      |
      +---- pass -------------> CUSTOMER RESPONSE
      |
      v
AUDIT TRAIL / DECISION CONSOLE / EXPORT
all packets + final response
      |
      v
HERMES OPERATOR AGENT (planned)
failure summaries, suggested tests, GitHub issues, release notes, policy gaps
      |
      v
HUMAN DEVELOPER REVIEW / ORGANIZATION ACCOUNTABILITY
```

Two load-bearing facts (unchanged from the target design):
- The access gate is **deterministic and runs before any model**. Security policy
  before mechanism. Per-customer data only ever enters via a tool call through the
  MCP server — never RAG, prompt, or weights. A prompt-injected agent still cannot
  widen access, because the server, not the model, enforces scope.
- **The broker is the decision boundary.** The model/router can propose an action,
  but the broker decides whether it is allowed, blocked, escalated, or clarified.
  The final reply is generated from the broker decision packet, so an unsafe model
  proposal cannot leak into the user-facing response after it is blocked.
- **Tiering is the cost and latency strategy.** The fine-tuned Tier 1 classifier is
  what lets Relay skip the frontier model on the easy majority — so the fine-tune
  earns its place in the cost story rather than being a side quest.

> **Target architecture (deferred):** the same stages become event-driven — each
> emits an event on a bus, stages scale and fail independently, and intent
> classification / retrieval / abuse screening fan out in parallel with speculative
> retrieval warming the cache. The v1 synchronous pipeline is a deliberate
> simplification, not an oversight.

## 4. MCP integration (the tool layer)
MCP replaces a hand-rolled tool-calling layer with a clean, standard boundary that
maps directly onto the design's "live data via tool only" rule.

- **Relay (agent) = MCP client.** The frontier model requests a tool; the client
  routes it to the server.
- **Account/device service = MCP server.** Exposes a small, explicit tool registry:
  - `account_lookup(customer_id)` — read-only, scoped to the authenticated customer
  - `device_reset(device_id)` — reversible, idempotent
  - `send_link(link_type)` — reversible
- **Scope is enforced server-side.** The server only ever returns what the
  authenticated customer may see, regardless of what the model asks for. This is the
  demonstrable security property: a prompt-injection attempt that tries to read
  another customer's data is refused by the server, not by hoping the model behaves.

Keep the server small and tightly scoped — three well-permissioned tools read far
better than a broad surface. The screenshot-able artifact for the portfolio: an
injection attempt, and the MCP server refusing it.

- **Datastore (v2.0).** The account/device data behind the tools lives in a SQLite
  customer/auth store (`src/core/customer_store.py`): `customers`, `devices`, and
  `auth_tokens` tables behind the same scoped accessors. The schema is the
  contract — a real deployment points the accessors at managed Postgres. The
  default store is in-memory and re-seeded per process; `RELAYOPS_CUSTOMER_DB`
  makes it durable. The scope boundary is unchanged: a device is owned by exactly
  one customer, enforced by the query, not the model.

## 5. Fine-tuning (intent classifier first)
Strictly inside the volatility split: fine-tuning is for the intent classifier and
(optionally) tone — never facts. "Facts in weights" stays an avoided anti-pattern;
telecom plans/policies live in RAG and config.

**v1 deliverable — fine-tune the intent classifier:**
- A small model mapping an incoming message → one of the supported intents.
- Establish a prompted (few-shot) classifier as the baseline first, then fine-tune
  and show the improvement. This turns "I fine-tuned a model" into "I improved
  routing accuracy from X to Y," which is the stronger portfolio claim.
- Report concrete numbers: held-out test set, accuracy, and a confusion matrix.
- Why it earns its place: a good cheap classifier is exactly what lets the router
  avoid the frontier model on the easy 80% — it's load-bearing in the cost story.

**Watch-out — the dataset is the real work.** Fine-tuning needs labeled
message→intent pairs; curating that set is often more effort than the training. Seed
it from the example phrasings of your first intent and grow outward. Budget for this.

**Deferred — tone / brand-voice fine-tune.** Legitimate per the design, but hard to
evaluate rigorously ("does this sound on-brand?" resists clean metrics), so it's
polish, not a v1 deliverable.

## 6. Suggested build order (the vertical slice)
1. One intent, chat-only, end to end. authenticate → classify → access gate →
   MCP tool call for live data → compose → respond. Working beats broad.
2. The guardrail blocking something demonstrable — e.g. a hallucinated offer or
   price. This is the differentiator most portfolio agents lack; show it stopping a
   bad reply.
3. RAG with citations for the changing-facts layer, even with a tiny KB.
4. The fine-tuned classifier, with the prompted baseline to beat (Section 5).
5. Eval harness — a handful of adversarial cases + an LLM judge. Showing you
   test agents is rarer and more impressive than the agent itself.
6. Observability — per-turn token/cost/latency and the cost-per-resolved-task
   metric. Cheap to add, very legible to reviewers.

## 7. Failure-mode catalog (preserved; v1 defenses noted)
| # | Failure | At Relay | v1 Defense |
|---|---|---|---|
| 1 | Hallucination in critical path | Invents an offer/price/policy | Truthfulness guardrail + citation grounding; offers are DATA; escalate unverifiable |
| 2 | Model drift | Intent classifier decays on novel tail | Eval vs baseline; fallback to rules (continuous prod eval deferred) |
| 3 | Tool/API timeout | Account/device tool stalls | Per-call timeout + retry; graceful "let me get a specialist" |
| 4 | Feedback-loop poisoning | Learning over-fits a few handoffs | N/A in v1 — learning loop deferred; designed as suggest-only |
| 5 | Orchestration deadlock | A model hangs | Per-call timeouts; partial results (bus/dead-letter deferred) |
| 6 | Human bottleneck | Escalation queue outgrows reps | Monitor escalation rate; tune thresholds (capacity plan deferred) |
| 7 | "Almost right" | 90%-right reply misses the dangerous bit | Confidence flagging; adversarial cases in the eval set |

## 8. Human-in-the-loop placement (unchanged target; v1 in bold)
| Level | Relay intents |
|---|---|
| **Full automation (sampled review)** | **Status lookups, device resets, FAQ, send-link** |
| Human handles exceptions | Low-confidence intents, guardrail/compliance failures |
| Human decides, agent prepares | Billing/plan/payment changes, complaints — agent gathers context, human acts |
| Full human + AI assist | Novel situations the KB can't yet cover |
| Always escalate (emotion/abuse) | Detected distress/anger or abusive input |

Rule of thumb kept from the original: start with more human involvement than you
think you need.

## 9. Anti-patterns explicitly avoided
- **Phase skipping** — Phase 0 design done first.
- **Mechanism without policy** — deterministic access gate is policy-first; MCP server enforces scope.
- **Embedded compliance** — guardrails are a separate gate.
- **Evaluation debt** — adversarial cases + calibrated judge before "done."
- **Observability retrofit** — per-turn cost/latency from day one.
- **Infinite autonomy** — autonomy set per intent; high-stakes always gated.
- **Facts in weights** — fine-tuning is classifier + tone only.
- **Scope sprawl** — v1 is a vertical slice; breadth is documented, not half-built.

## 10. Decision deltas from the original design
| ID | Decision |
|---|---|
| P1 | Scope to a single-intent, chat-only vertical slice for v1; document the rest as designed-not-built. |
| P2 | Implement the tool layer as an MCP server (client = agent), enforcing per-customer scope server-side. |
| P3 | Fine-tune the intent classifier, with a prompted baseline to beat and reported before/after metrics. Tone fine-tune deferred; facts never fine-tuned. |
| P4 | Defer: voice, multi-brand isolation, event bus, shadow→canary, continuous-learning loop. Kept in the design as targets. |

(Original decisions D1–D14 remain in the decision log; these P-deltas layer on top.)

## 11. Scope and next step
**Delivered in this revision:** portfolio framing, an explicit v1-vs-deferred split,
a v1 (synchronous, chat-only) architecture diagram, the MCP tool-layer design, the
intent-classifier fine-tuning plan with a baseline-to-beat, an ordered build plan,
and the preserved failure-mode / HITL / anti-pattern thinking.

**Next step:** pick the one intent ("reset my device"), write its example
phrasings (this seeds the classifier dataset), and drive it through the build order
in Section 6 — MCP tool call, guardrail block, eval case, cost metric — before
generalizing to more intents.
