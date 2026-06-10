# Decision Log — RelayOps

Append-only record of decisions. Newest at top. Each entry: ID, date, decision, why.

## Portfolio-scope deltas (P-series)

### P8 — Add decision trace and billing/account abuse eval
**Date:** June 2026
v1.5.1 adds structured `decision_steps`, `proposed_action`, `blocking_rule`,
`risk_signal`, `available_context`, and `unavailable_context` to audit records.
It also adds an adversarial billing/account ticket suite for unauthorized credit
attempts, social engineering, and verification-bypass requests.
**Why:** A real support buyer needs more than "escalated." The handoff must let
the human continue, compliance must reconstruct why the decision happened, and
bad-faith billing/account requests must not become automated actions.

### P7 — Rename "AI PR Review Agent" to "PR Safety Evidence Gate"
**Date:** June 2026
The v1.1 PR workflow is fully deterministic — it clears `GEMINI_API_KEY` /
`ANTHROPIC_API_KEY` and makes no LLM call — so the "AI" label was an overclaim.
Renamed the workflow, README, and policy doc to **PR Safety Evidence Gate** and
fixed the advisory/enforced wording: deterministic tests/evals are enforced (the
workflow fails when required checks fail); the posted checklist comment is
advisory and does not approve, merge, or decide policy.
**Why:** RelayOps's strongest quality is that it does not overclaim. A real
LLM-assisted diff reviewer may be added later as an optional layer (e.g. only when
`GEMINI_API_KEY` is present), but deterministic tests/evals remain the source of
truth.

### P6 — Calibrate NB routing and add route-safety metrics
**Date:** June 2026
v1.2 maps Complement NB's flat raw confidence to empirical validation precision,
adds narrow deterministic safety overrides for money/customer-data/status cues,
and expands the hand-written adversarial set from 24 to 100 cases. The eval now
reports safe-route rate, route-correct rate, over-escalation, unsafe auto-action,
billing escape, and scope-violation block rate.
**Why:** Classifier accuracy alone is not the production metric. A wrong
classification can still be safe if it escalates; the key failure is unsafe
auto-action or money-touching escape.
**Honest caveat (added):** the deterministic safety cues are a hand-authored
allowlist written with visibility of the adversarial cases, so the 100-case
result is **in-distribution safety coverage**, not independent generalization
(~half the cases are decided by a literal cue). `eval_calibration` now also
reports a stratified **held-out split** (≈60 dev / ≈40 held-out): safety holds on
held-out (safe-route 1.000, unsafe auto-action 0.000, billing escape 0.000;
route-correct 0.846 vs 0.918 dev). Follow-up: freeze cues against the held-out
slice (or author it independently) so that number is a true generalization
benchmark.

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
legacy 24-case set 0.958 acc / 0.804 macro-F1 vs NB's 0.667 / 0.562 — the real
generalization win, since the synthetic held-out set is easy for any decent
learner. The v1.2 100-case adversarial rerun is pending before upgrading the
Qwen adversarial claim. Recipe +
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
