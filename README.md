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

| Classifier | Cost | 5-seed held-out accuracy | Role |
|---|---|---:|---|
| Keyword baseline | ~$0 | 0.492 | Brittle baseline |
| Complement NB | ~$0 | **0.932** | Strong offline learned baseline |
| Qwen2.5-1.5B LoRA | Low | eval pending | Intended Tier-1 neural classifier |
| Claude Haiku prompt | Higher | optional | Tier-2/reference baseline |

The dataset is 2,400 examples, balanced at 400 examples per intent, with
group-aware splits so related synthetic paraphrase families do not leak across
train/test. On the adversarial/paraphrase set, keyword baseline scores `0.250`
and Complement NB scores `0.667`.

### Why NB Is Not The Final Answer

Complement NB is kept as a strong offline baseline. The Qwen LoRA path exists to
test whether a small neural classifier handles paraphrase, slang, and mixed-intent
messages better than bag-of-words models, especially on adversarial examples.
The README will only claim Qwen is better after the same held-out and adversarial
evals produce that result.

Fine-tuned adapter: LoRA upload/public release pending final eval and model card.
RelayOps fine-tunes a **small open-source model**, not Claude.

## Architecture

```text
customer message
  -> access gate
  -> intent classifier
  -> router
  -> scoped tool or RAG
  -> composer
  -> independent guardrail
  -> response or human handoff
```

See [DESIGN.md](DESIGN.md) for the full production design and deferred systems
plan.

## Repository Layout

```text
src/access/         deterministic access gate
src/mcp/            scoped tool bodies
src/router/         tiered router + intent classifiers
src/rag/            hybrid retrieval + citations
src/guardrails/     independent guardrail layer
src/graph/          synchronous graph-shaped pipeline
src/eval/           datasets, finetune export, classifier eval
src/observability/  planned token/cost dashboards
src/ui/             planned Streamlit chat
knowledge_base/     small cited KB
```
