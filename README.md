# RelayOps

**An AI customer-service agent for telecom/subscription — built as a production-shaped vertical slice.**

> I designed the full production system and built a vertical slice that proves the load-bearing ideas.

RelayOps answers customer questions over chat and takes low-risk, reversible actions
on its own (device resets, sending links) while escalating anything touching billing,
plans, or payments — or anything it's unsure about — to a human. Every turn flows
through a deterministic access gate, a tiered model router, scoped tools over MCP, and
an independent guardrail that can block a bad reply.

## The load-bearing ideas (what this slice proves)
- **Deterministic access gate** — a non-LLM permission check runs *before* any model. No prompt injection can widen what a customer can see.
- **Scoped tools over MCP** — live account data is reached only through an MCP server that enforces per-customer scope server-side. The agent is just a client.
- **Tiered routing** — a fine-tuned cheap classifier handles the easy majority; the frontier model is reserved for hard/low-confidence/action cases. Cost follows difficulty.
- **Independent guardrail** — a separate gate checks truthfulness, allowed offers (data, not model-invented), tone, and PII — and can block.
- **Eval + observability from day one** — adversarial cases with an LLM judge; per-turn token/cost/latency and **cost-per-resolved-task**.

## v1 scope (this repo)
- **One intent end to end:** "reset my device"
- Chat only · single brand · synchronous LangGraph pipeline
- Fine-tuned intent classifier with a prompted baseline to beat (accuracy + confusion matrix)
- Hybrid RAG with citations over a small KB

Everything else (voice, event bus, multi-brand, shadow→canary, learning loop) is
**designed, not built** — see [DESIGN.md](DESIGN.md).

## Architecture
See [DESIGN.md](DESIGN.md) §3 for the v1 diagram and §1 for the built-vs-deferred split.

## Tech stack
LangGraph · MCP · Chroma (vector DB) · Streamlit (UI) · Tier 1 + Tier 2 models · LLM-as-judge eval

## Intent classifier (Tier 1) — model selection, not "I used an LLM"
The cheap Tier-1 classifier is what lets the router skip the frontier model on the
easy majority, so it's evaluated as a model-selection decision across four options
behind one `IntentClassifier` interface (`router.registry.get_classifier`):

| Classifier | Cost | Held-out (5-seed CV) | Notes |
|---|---|---|---|
| keyword baseline | ~0 | 0.492 | brittle on paraphrases/slang |
| Complement NB (offline learned) | ~0 | **0.932** | beats keyword by +44.0 pts |
| fine-tuned Qwen2.5-1.5B (Unsloth/LoRA) | low | target | best paraphrase/adversarial handling |
| prompted frontier (Claude Haiku) | high | — | strong zero-shot Tier-2 reference |

Reported on a 2,400-example paraphrase-rich intent set (400 examples per class)
with group-aware splits, a confusion matrix, and an **adversarial/paraphrase** set
(keyword 0.25 → NB 0.667). The fine-tune is a
**small open-source model**, not Claude; see [docs/finetuning.md](docs/finetuning.md).
The model emits intent only (routing/risk stay in the deterministic gate+router),
and confidence comes from token probabilities — not fabricated labels.

## Repository layout
```
src/access/         deterministic access gate
src/mcp/            MCP server + scoped tools
src/router/         tiered router + intent classifier
src/rag/            hybrid retrieval + citations
src/guardrails/     independent guardrail layer
src/graph/          LangGraph pipeline wiring
src/eval/           golden set + LLM judge
src/observability/  per-turn cost/latency, cost-per-resolved-task
src/ui/             Streamlit chat
knowledge_base/     small cited KB
```

## Status
🚧 v1 in progress. See [claude.md](claude.md) for the locked scope and [docs/decision-log.md](docs/decision-log.md) for decisions.
