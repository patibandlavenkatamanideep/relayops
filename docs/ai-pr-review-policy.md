# RelayOps AI PR Review Policy

Status: **v1.1 CI-only advisory reviewer**

RelayOps v1.1 adds an AI-assisted pull-request safety reviewer for repository
changes. It is a GitHub/CI workflow only. It is not part of the customer-support
runtime, does not decide customer policy, and does not mutate code.

The design principle is the same as the rest of RelayOps:

> Deterministic tests and evals are the source of truth. The AI reviewer is
> advisory and helps humans notice risk, missing evidence, and overclaimed model
> metrics.

Related reference project: [ayush488-glitch/ai-pr-review-agent](https://github.com/ayush488-glitch/ai-pr-review-agent).
That project is a full webhook service with specialist review agents. RelayOps
keeps v1.1 narrower: a repo-local policy workflow that can later be wired to a
hosted PR review service without changing the customer-support pipeline.

## Scope

The reviewer focuses on changes to:

- Access gate logic.
- Scoped MCP-style tool bodies.
- Router thresholds and graph/pipeline routing.
- Guardrails.
- Agent evals and classifier evals.
- Intent datasets and classifier metrics.
- README/docs claims about model quality, safety, cost, latency, and deployment.

## Non-Goals

The reviewer must not:

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

Optional LLM-as-judge eval:

```bash
GEMINI_API_KEY=... python3 -m src.eval.run_agent_eval
ANTHROPIC_API_KEY=... python3 -m src.eval.run_agent_eval
```

## Reviewer Checklist

The AI reviewer should ask:

- Did the access gate move later in the pipeline or become model-dependent?
- Can a prompt injection widen customer scope?
- Can any tool act on a device/account outside the authenticated customer?
- Did guardrail changes weaken invented-offer, price, PII, or tone blocking?
- Did router threshold changes increase action-taking without new eval proof?
- Did calibration changes improve route correctness without increasing unsafe
  auto-actions, billing escapes, or unsupported escapes?
- Did README metrics change without a matching eval command/output?
- Did docs overclaim Qwen LoRA, Gemini judge, cost, latency, deployment, or safety?
- Did the PR add runtime dependencies to support reviewer automation? It should not.

## Roadmap Placement

- v1.0: RelayOps vertical slice shipped.
- v1.1: AI-assisted PR safety reviewer for repo changes.
- v1.2: NB confidence calibration and larger adversarial set.
- v2.0: Hermes-style RelayOps Operator Agent.

Hermes remains out of v1.1. It should eventually operate as a repo/operator agent
for eval runs, failure summaries, issue creation, release notes, and regression
tracking. It should not enter the customer-support pipeline.
