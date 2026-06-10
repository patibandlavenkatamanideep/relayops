# RelayOps PR Safety Evidence Gate Policy

Status: **v1.1 CI-only evidence gate**

RelayOps v1.1 adds a PR Safety Evidence Gate for repository changes. It is a
GitHub/CI workflow only: it detects risky changes, runs deterministic
tests/evals, and posts an advisory evidence checklist. It is not part of the
customer-support runtime, does not decide customer policy, and does not mutate
code. The v1.1 gate is fully deterministic and makes no LLM call — the name is
kept honest rather than labeling it "AI".

The design principle is the same as the rest of RelayOps:

> Deterministic tests and evals are the source of truth and are enforced (the
> workflow fails when required checks fail). The posted checklist is advisory and
> helps humans notice risk, missing evidence, and overclaimed model metrics.

Related reference project: [ayush488-glitch/ai-pr-review-agent](https://github.com/ayush488-glitch/ai-pr-review-agent).
That project is a full webhook service with specialist LLM review agents. RelayOps
keeps v1.1 narrower: a deterministic, repo-local policy gate that can later be
wired to an optional LLM diff reviewer without changing the customer-support
pipeline.

## Scope

The gate focuses on changes to:

- Access gate logic.
- Scoped MCP-style tool bodies.
- Router thresholds and graph/pipeline routing.
- Guardrails.
- Agent evals and classifier evals.
- Intent datasets and classifier metrics.
- README/docs claims about model quality, safety, cost, latency, and deployment.

## Non-Goals

The gate must not:

- Run inside the customer-support runtime.
- Decide customer policy.
- Change routing rules automatically.
- Modify guardrails without human review.
- Access customer data.
- Approve or merge PRs.
- Override deterministic test or eval failures.

## Workflow Security Notes

- The workflow uses the `pull_request` event, not `pull_request_target`, so PR
  code does not run with elevated repository secrets.
- The CI job clears `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, and
  `RELAYOPS_INTENT_MODEL` so default review runs stay offline and deterministic.
- PR comments are advisory. They may be skipped on forked PRs if GitHub limits
  token write permissions, but deterministic test and eval results still stand.

## Required Evidence Matrix

| Changed Surface | Required Evidence |
|---|---|
| `src/access/**` | Unit tests covering authenticated, unauthenticated, and cross-customer behavior. |
| `src/mcp/**` | Scope-violation test or eval proving server-side tool refusal still works. |
| `src/guardrails/**` | Tests/evals for invented offers, prices, PII, and unsafe tone. |
| `src/router/**` or `src/graph/**` | Deterministic agent eval output from `python3 -m src.eval.run_agent_eval`. |
| `src/eval/**` or `tests/**` | Updated deterministic expectations and proof that the eval suite still passes. |
| Classifier dataset/model changes | Output from `python3 -m src.eval.run_intent_eval`. |
| Calibration, threshold, or route-safety changes | Output from `python3 -m src.eval.eval_calibration`. |
| Billing/account abuse eval changes | Output from `python3 -m src.eval.eval_billing_abuse`. |
| README/model metric changes | The exact command and output that support the claim. |
| Hugging Face/model artifact claims | Link to artifact plus eval status: complete, pending, or intentionally skipped. |

## Commands

Core unit tests:

```bash
python3 -m unittest
```

Deterministic agent eval:

```bash
python3 -m src.eval.run_agent_eval
```

Intent classifier eval:

```bash
python3 -m src.eval.run_intent_eval
```

Calibration and route-safety eval:

```bash
python3 -m src.eval.eval_calibration
```

Billing/account abuse eval:

```bash
python3 -m src.eval.eval_billing_abuse
```

Optional LLM-as-judge eval:

```bash
GEMINI_API_KEY=... python3 -m src.eval.run_agent_eval
ANTHROPIC_API_KEY=... python3 -m src.eval.run_agent_eval
```

## Reviewer Checklist

A human reviewer (or a future optional LLM diff reviewer) should ask:

- Did the access gate move later in the pipeline or become model-dependent?
- Can a prompt injection widen customer scope?
- Can any tool act on a device/account outside the authenticated customer?
- Did guardrail changes weaken invented-offer, price, PII, or tone blocking?
- Did router threshold changes increase action-taking without new eval proof?
- Did calibration changes improve route correctness without increasing unsafe
  auto-actions, billing escapes, or unsupported escapes?
- Did billing/account abuse cases still escalate unauthorized credit, social
  engineering, and verification-bypass attempts?
- Did README metrics change without a matching eval command/output?
- Did docs overclaim Qwen LoRA, Gemini judge, cost, latency, deployment, or safety?
- Did the PR add runtime dependencies to support reviewer automation? It should not.

## Roadmap Placement

- v1.0: RelayOps vertical slice shipped.
- v1.1: deterministic PR Safety Evidence Gate for repo changes.
- v1.2: NB confidence calibration and larger adversarial set.
- v2.0: Hermes-style RelayOps Operator Agent.

Hermes remains out of v1.1. It should eventually operate as a repo/operator agent
for eval runs, failure summaries, issue creation, release notes, and regression
tracking. It should not enter the customer-support pipeline.
