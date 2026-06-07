# RelayOps

**Production-shaped AI customer-service agent for telecom/subscription support.**

Status: **v1 vertical slice working; Qwen LoRA eval pending.**

RelayOps handles customer chat turns through a deterministic access gate, a tiered
intent router, scoped tools, hybrid RAG, and an independent guardrail. The built
slice focuses on one end-to-end action - **reset my device** - while billing,
unsafe, ungrounded, or low-confidence turns are handed to a human.

## Demo Proof

Run:

```bash
python3 demo.py
```

Demo scenarios:

| Scenario | Proof |
|---|---|
| Reset device | Authenticated customer triggers the scoped reset tool. |
| Billing request | Money-touching intent escalates to a human. |
| Hallucinated offer | Guardrail blocks invented discounts/prices before response. |
| FAQ question | Hybrid RAG answers with citations from the local KB. |
| Prompt injection / cross-customer device | MCP tool layer refuses scope violation server-side. |

Sample trace - allowed reset:

```text
User: "my router isn't working, can you reset it?"
Access gate: authenticated as cust_alice
Intent classifier: reset_device
Router: tier2 + device_reset
Tool: device_reset(customer_scope=cust_alice, device=dev_a1) -> ok
Guardrail: pass
Final: "Done - I reset your Alice's Router and it's back online..."
Latency: ~1ms in the local deterministic demo
```

Sample trace - billing / offer safety:

```text
User: "I want a refund on my last bill"
Access gate: authenticated
Intent classifier: billing
Router: human_escalation
Tool: none
Final: "I'm connecting you with a specialist..."
Reason: billing/plan/payment
```

Sample trace - hallucinated offer blocked:

```text
Candidate reply: "I can give you 50% off for just $9.99/month."
Guardrail: block
Violations: unapproved_amount, unapproved_discount, unapproved_recurring_price
Final: human handoff; made-up offer never reaches the customer
```

## Built vs Designed

| Component | Status |
|---|---|
| Deterministic access gate | Built |
| Scoped account/device tool bodies | Built |
| Server-side customer-scope enforcement | Built |
| Intent classifier registry | Built |
| Keyword baseline | Built/evaluated |
| Complement NB classifier | Built/evaluated |
| 2,400-example grouped intent dataset | Built/evaluated |
| Qwen2.5-1.5B LoRA training path | Built; final eval pending |
| Hybrid RAG with citations | Built |
| Guardrail for offers/prices, PII, tone | Built |
| Synchronous graph-shaped pipeline | Built |
| Latency on responses | Built |
| Streamlit interactive demo UI | Built |
| MCP transport wrapper | Designed; tool bodies already scoped |
| Token/cost dashboards, voice, event bus, canary | Designed only |

## Runbook

Install local dev dependencies:

```bash
python3 -m pip install -e ".[dev]"
```

Run the vertical-slice demo:

```bash
python3 demo.py
```

Run the interactive Streamlit demo:

```bash
streamlit run src/ui/app.py
```

Deploy the interactive demo to Railway:

```bash
railway login
railway init
railway up
railway open
```

Railway reads [railway.toml](railway.toml), installs the Python app, and starts
Streamlit on `0.0.0.0:$PORT`.

Run tests:

```bash
python3 -m unittest
```

Rebuild the large classifier dataset and LoRA JSONL splits:

```bash
python3 -m src.eval.build_intent_dataset
python3 -m src.eval.export_finetune_data
```

Evaluate keyword vs Complement NB:

```bash
python3 -m src.eval.run_intent_eval
```

Train the Qwen LoRA adapter on a GPU machine:

```bash
python3 -m src.router.finetune_train
```

Evaluate a local or uploaded LoRA adapter:

```bash
RELAYOPS_INTENT_MODEL=/path/to/intent-lora.zip python3 -m src.eval.run_intent_eval
```

## Intent Classifier

The classifier story is model selection, not "I used an LLM." Every classifier
implements the same `IntentClassifier` interface, so the router can swap them
without changing pipeline logic.

| Classifier | Cost | 5-seed acc | 5-seed macro-F1 | Role |
|---|---|---:|---:|---|
| Keyword baseline | ~$0 | 0.492 | 0.506 | Brittle baseline |
| Complement NB | ~$0 | **0.932** | **0.931** | Strong offline learned baseline |
| Qwen2.5-1.5B LoRA | Low | eval pending | eval pending | Trained adapter; eval wrapper debugging |
| Claude Haiku prompt | Higher | optional | optional | Tier-2/reference baseline |

Macro-F1 (every intent weighted equally) is reported alongside accuracy so a model
can't look good by leaning on easy/over-represented classes.

The dataset is 2,400 examples, balanced at 400 examples per intent, with
group-aware splits so related synthetic paraphrase families do not leak across
train/test. On the harder adversarial/paraphrase set the gap stays clear but both
drop: keyword baseline `0.250` acc / `0.242` macro-F1, Complement NB `0.667` acc /
`0.562` macro-F1 — the headroom the neural fine-tune is meant to close.

### Why NB Is Not The Final Answer

Complement NB is kept as a strong offline baseline. The Qwen LoRA path exists to
test whether a small neural classifier handles paraphrase, slang, and mixed-intent
messages better than bag-of-words models, especially on adversarial examples.
The README will only claim Qwen is better after the same held-out and adversarial
evals produce that result.

Fine-tuned adapter: LoRA upload/public release pending final eval and model card.
RelayOps fine-tunes a **small open-source model**, not Claude.

## Architecture

v1 is a synchronous pipeline. Two properties are load-bearing and both are built:
the access gate is **deterministic and runs before any model**, and per-customer
data is reached **only** through the scoped tool layer — never via prompt, RAG, or
model weights — so a prompt-injected model still cannot widen scope.

```text
customer chat turn
      │
      ▼
 ingest ─► DETERMINISTIC ACCESS GATE        non-LLM: authn → per-customer scope
      │
      ▼
 INTENT CLASSIFIER (Tier 1)                 keyword · Complement NB · Qwen LoRA
      │
      ▼
 ROUTER ── confident + low-risk ─► Tier 1 answer
      │     action / low-conf / hard ─► Tier 2, which pulls:
      │        ├─ SCOPED TOOLS (MCP-shaped): account_lookup · device_reset · send_link
      │        │     scope enforced SERVER-SIDE against the gate's customer
      │        └─ HYBRID RAG (BM25 + dense, RRF): cited; escalates if nothing grounds
      │
      ▼
 composer ─► INDEPENDENT GUARDRAIL          truthfulness/offers · PII · tone — can BLOCK
      │
      ├─► RESPOND (chat)
      └─► HUMAN HANDOFF + full context blob
            triggers: billing/plan/payment · low confidence · guardrail block ·
                      unverifiable RAG · scope violation · unauthenticated
      │
      ▼
 OBSERVABILITY (planned, step 6)            per-turn token/cost/latency
```

Deferred (designed, not built): MCP transport wrapper, event bus, token/cost
dashboards, voice, shadow→canary. See [DESIGN.md](DESIGN.md) for the full
production design and the v1-vs-deferred split.

## Repository Layout

```text
src/access/         deterministic access gate
src/mcp/            scoped tool bodies
src/router/         tiered router + intent classifiers
src/rag/            hybrid retrieval + citations
src/guardrails/     independent guardrail layer
src/graph/          synchronous graph-shaped pipeline
src/eval/           datasets, finetune export, classifier eval
src/observability/  planned token/cost dashboards (step 6)
src/ui/             Streamlit interactive demo UI (built)
knowledge_base/     small cited KB
```
