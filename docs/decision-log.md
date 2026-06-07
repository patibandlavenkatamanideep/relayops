# Decision Log — RelayOps

Append-only record of decisions. Newest at top. Each entry: ID, date, decision, why.

## Portfolio-scope deltas (P-series)

### P1 — Scope v1 to a single-intent, chat-only vertical slice
**Date:** June 2026
Build "reset my device" end to end; document the rest as designed-not-built.
**Why:** Working beats broad for a solo portfolio project. A half-wired system that
doesn't run is the weakest outcome.

### P2 — Tool layer is an MCP server; agent is the client
**Date:** June 2026
Per-customer scope is enforced server-side, not by the model.
**Why:** Demonstrable security property — a prompt-injection attempt to read another
customer's data is refused by the server, not by hoping the model behaves.

### P3 — Fine-tune the intent classifier with a prompted baseline to beat
**Date:** June 2026
Report held-out accuracy + confusion matrix. Tone fine-tune deferred; facts never fine-tuned.
**Why:** Turns "I fine-tuned a model" into "I improved routing accuracy from X to Y."
The cheap classifier is load-bearing in the cost story (skips frontier on the easy 80%).

### P5 — Fine-tune target = small open-source LM (Qwen2.5-1.5B) via Unsloth/LoRA
**Date:** June 2026
The Tier-1 classifier is a fine-tuned small open model, not Claude. Offline, a
Complement-NB model stands in (beats keyword 0.492→0.932 CV on the 2,400-example
group-aware paraphrase dataset). The Qwen2.5-1.5B LoRA fine-tune is now trained +
evaluated (Colab/Unsloth): held-out 0.999 acc, and on the hand-written adversarial
set 0.958 acc / 0.804 macro-F1 vs NB's 0.667 / 0.562 — the real generalization win,
since the synthetic held-out set is easy for any decent learner. Recipe +
chat-JSONL exporter + `FineTunedIntentClassifier` (same interface) ship for GPU
runs; public adapter upload pending. Classifiers are switchable via
`router.registry.get_classifier`.
**Why:** Anthropic has no customer fine-tuning of Claude — the honest, stronger
claim is "fine-tuned a small open model, kept Claude for Tier-2". Refinements on
the external review: model emits **intent only** (risk/route stay in the
deterministic router — policy out of weights), and **confidence comes from token
probabilities**, not fabricated labels. Evaluated on a held-out + adversarial set.

### P4 — Defer voice, multi-brand isolation, event bus, shadow→canary, learning loop
**Date:** June 2026
Kept in the design as targets; not built in v1.
**Why:** Scope discipline. Breadth is documented, not half-built.

## Tech-stack decisions

### T1 — Orchestration: LangGraph
Synchronous pipeline now; event-bus version documented as the deferred target.

### T2 — Vector DB: Chroma · UI: Streamlit
Lightweight, local-first, fast to demo.

### T3 — Models: Tier 1 (cheap) + Tier 2 (frontier) routing
Cost follows difficulty.

---

## Original design decisions (D1–D14)
Preserved from the original Relay design doc. (Paste/migrate the full D-series here
when available; the P-series above layers on top of them.)
