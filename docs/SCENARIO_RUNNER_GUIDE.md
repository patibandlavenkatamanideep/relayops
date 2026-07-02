# Scenario Runner Guide — RelayOps v3.0

The scenario runner ([`src/scenarios`](../src/scenarios)) is the **one-command
end-to-end demo**. It drives a single synthetic, redacted ticket through the entire
RelayOps control plane and prints a readable, per-stage lifecycle — so instead of
explaining every module, you can say: *"run one scenario and watch the whole thing
act."*

It is deterministic and local: **no vendor calls, no credentials, no real customer
data, no real external execution.** It composes the real modules (pipeline, audit
ledger, replay verifier, operator metrics, Hermes, approval queue/export) rather
than mocking them, so the output is evidence the layers actually interlock.

---

## 1. What scenarios exist

Five synthetic samples ([`src/scenarios/sample_scenarios.py`](../src/scenarios/sample_scenarios.py)),
each chosen to prove one property:

| Scenario | Proves |
|---|---|
| `device_status` | normal safe automation path (scoped read, no human, no approval) |
| `high_risk_refund` | approval required before execution (held + escalated) |
| `cross_customer_block` | scope violation refused at the tool boundary |
| `missing_evidence_faq` | fail-closed handoff when grounding is missing |
| `replay_mismatch` | replay verification catches an injected inconsistency |

Three are also shipped as editable JSON under
[`examples/scenarios/`](../examples/scenarios): `device_status.json`,
`high_risk_refund.json`, `cross_customer_block.json`.

## 2. How to run them

```bash
# all five built-in samples, as a Markdown report
python -m src.scenarios.runner

# one scenario from a JSON file
python -m src.scenarios.runner examples/scenarios/high_risk_refund.json

# JSON output instead of Markdown (for tooling / a demo panel)
python -m src.scenarios.runner --json
```

No keys or setup beyond the base install. A scenario JSON reads only known fields
(id, title, description, message, auth_token, device_id, and the `expect_*`
assertions); any extra key is ignored.

## 3. What each stage shows

Every run walks the same 12 stages, in order — this **is** the control-plane
lifecycle:

| Stage | What you see |
|---|---|
| `ingest` | the redacted ticket accepted |
| `auth_scope` | authentication + resolved customer scope (or a scope block) |
| `broker_decision` | allow / block / escalate, with matched rule + reason |
| `action_envelope` | the wrapped side-effecting action (or none, for a read/handoff) |
| `tool_boundary` | the scoped tool executed — or refused (e.g. `scope_violation`) |
| `approval` | approval requirement; a high-risk hold is `blocked` pending a human |
| `audit_record` | the deterministic audit record written |
| `replay_verification` | replay compared to the original — `pass` or the drift caught |
| `operator_metrics` | the scoreboard for this turn |
| `hermes_review` | advisory findings + alerts (read-only, never acts) |
| `approval_export` | the read-only JSON/Markdown evidence snapshot |
| `final_report` | disposition + the reminder that a human stays accountable |

Each stage has a status (`ok` / `blocked` / `escalated` / `info`), a human-readable
summary, and a `detail` payload with the underlying evidence.

## 4. How outputs map to the lifecycle

The runner is a thin orchestrator: it calls the **real** pipeline, then reads the
**real** audit record and derives each stage from it, then calls the **real** replay
verifier, operator metrics, Hermes, and approval export. So a green `device_status`
run and a `blocked` `high_risk_refund` approval stage are not scripted — they are
what the actual modules produced. The `replay_mismatch` scenario is the one
deliberately synthetic touch: it drifts a **copy** of the replayed audit record so
the verifier has an inconsistency to catch; the live turn is unaffected.

## 5. Using scenarios in a demo or interview

- **30-second version:** `python -m src.scenarios.runner examples/scenarios/high_risk_refund.json`
  — point at the `approval` stage (blocked, held for a human) and `final_report`
  (escalated, human accountable).
- **Full sweep:** `python -m src.scenarios.runner` — one table per scenario; walk
  top to bottom to show safe automation, approval gating, scope blocking,
  fail-closed handoff, and replay drift detection in one screen.
- **Pair with the UI:** run the same case, then open the **Operator Review** tab
  ([OPERATOR_REVIEW_GUIDE.md](OPERATOR_REVIEW_GUIDE.md)) to show the same signals in
  the console.

## 6. Extending

Add a `Scenario` in `sample_scenarios.py` (or a JSON file) with the caller context
and the `expect_*` outcome it should prove; the test suite
([`tests/test_scenario_runner.py`](../tests/test_scenario_runner.py)) asserts each
sample meets its expectation, so a regression in any layer surfaces as a failing
scenario. Keep new scenarios **synthetic** and within the demo's safety posture — no
real data, no real execution.
