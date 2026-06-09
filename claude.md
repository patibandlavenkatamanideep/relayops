# claude.md

RelayOps — Portfolio Execution Plan (Claude Review + Grok Refinement)

Project name: **RelayOps**
Last updated: June 2026

## Why this file exists
Claude gave the single best scoping advice on this project.
This file is our single source of truth for what we are actually building (and what we deliberately deferred).

## Final Project Name Decision
**RelayOps** (chosen from the shortlist: RelayOps, RelayIQ, RelayGuard, TelcoRelay, SignalRelay, etc.)

Reason: Keeps the original "Relay" concept while adding the "Ops" signal for the production-shaped operational concerns this project explores (routing safety, audit trail, handoffs).

## Claude's Original Review
See [DESIGN.md](DESIGN.md) for the full design doc and review.

## Scoped v1 Scope (locked in for portfolio)
- **Single intent vertical slice**: "Reset my device" (full end-to-end flow)
- Chat-only (voice = Phase 2)
- MCP server for all tools (deterministic scoping)
- Fine-tuned intent classifier (prompted baseline + confusion matrix)
- Hybrid RAG with citations
- Separate guardrail layer (blocks hallucinations demonstrably)
- Observability (token/cost/latency + cost-per-resolved-task)
- Synchronous LangGraph pipeline (future event-bus documented)

Everything else from the original DESIGN.md stays as "Designed, not yet built."

## Tech Stack
- Orchestration: LangGraph
- Tools: MCP server
- Models: Tier 1 (cheap) + Tier 2 (frontier)
- Vector DB: Chroma
- UI: Streamlit
- Eval: Golden set + LLM-as-judge

## Folder Structure

```
RelayOps/
├── claude.md                  ← this file
├── DESIGN.md                  ← original full design doc
├── README.md                  ← one-page portfolio showcase
├── docs/
│   └── decision-log.md
├── src/
│   ├── core/
│   ├── access/                # deterministic access gate
│   ├── mcp/                   # MCP server + tools
│   ├── router/                # tiered router + classifier
│   ├── rag/
│   ├── guardrails/
│   ├── graph/
│   ├── eval/
│   ├── observability/
│   └── ui/
├── knowledge_base/
├── tests/
└── pyproject.toml
```
