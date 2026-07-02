# Operator Review Guide — RelayOps v3.0

RelayOps is run by a **human operator** who reviews what the agent did and decides
the cases the agent is not allowed to decide alone. This guide explains what to
inspect and where. Everything on the operator side is **read-only over the audit
evidence** — except the one place a human is *supposed* to act: approving or
rejecting a held high-risk action.

Open the demo UI with `streamlit run src/ui/app.py`; the **Operator Review** tab
hosts Hermes, the operator metrics, alerts, and the Approval Console. The API
mirrors the read-only views under `/v1/operator/review`, `/v1/handoffs`, and
`/v1/audit/{turn_id}`.

---

## 1. Hermes findings

Hermes ([`src/hermes`](../src/hermes)) reads the audit trail and drafts advisory
findings for a human developer: unsafe-escape, fail-closed, guardrail-block,
over-block, replay mismatches, and pending/rejected/expired approvals. Each finding
carries a severity (`low`→`critical`), a summary, a suggested test, a suggested
policy gap, and `human_review_required=True`.

**How to read them:** worst severity first. A `critical` unsafe-escape (a high-blast
action that auto-executed) should never appear — if it does, it is the headline. A
`medium` guardrail-block means the system correctly stopped an unsafe model reply.

Hermes **cannot** approve, reject, execute, reply, or change policy — it only flags.

## 2. Operator metrics

The operator scoreboard ([`src/operator_metrics`](../src/operator_metrics)) reduces
the audit records into the numbers you run the system by:

- `resolution_rate`, `handoff_rate`
- `fail_closed_rate`, `unsafe_escape_rate`, `over_block_rate`
- `replay_success_rate` / `replay_mismatch_rate`
- `action_execution_rate`, `avg_turns_to_resolution`
- an illustrative `estimated_cost_per_resolved_ticket`

Each is a pure function of the records — computing a metric never replies, executes,
or changes anything. Watch `unsafe_escape_rate` (should be 0) and `fail_closed_rate`
(spikes mean a safety layer is failing).

## 3. Alerts

Hermes alerting ([`src/hermes/alerting.py`](../src/hermes/alerting.py)) compares the
metrics snapshot against named operator thresholds and raises a deterministic
`AlertPacket` per breach:

- `unsafe_escape_rate` > 0 or `replay_blocked_count` > 0 → **critical**
- replay drift, fail-closed, handoff-rate/completeness shortfalls → **high**
- over-block → **medium**

Critical/high breaches draft a GitHub issue; the operator CLI's `--strict` flag
exits non-zero on a critical breach (a CI gate over audit evidence). Alerts are
advisory: Hermes never throttles, pages, or acts.

## 4. Replay mismatches

Replay verification ([`src/replay`](../src/replay)) compares an audited flow against
its replay and flags drift with stable reason codes: broker-decision,
action-envelope, tool-response, missing-audit, scope, and double-execution. **Scope
and double-execution mismatches are safety-blocking.** Hermes surfaces each mismatch
as a finding with a suggested test. Investigate any `blocked` replay before shipping.

## 5. Approval queue

The human approval queue ([`src/approval`](../src/approval)) holds high-risk actions
for review. In the queue, each request has a status:

- `pending` — waiting for you; **cannot execute**.
- `approved` — you approved it; eligible to execute **once**.
- `rejected` — you rejected it; **can never execute**.
- `expired` — the window lapsed; **can never execute**.
- `not_required` — low-risk; may proceed without you.

## 6. Approval Console (acting on a hold)

The **Approval Console** (Operator Review tab, [`src/ui/app.py`](../src/ui/app.py))
shows holds grouped by status with risk level and a per-record audit trail, and
provides approve/reject controls. This is the one operator action surface:

- Approving/rejecting **requires an operator identity and a reason** — an anonymous
  or unexplained decision is refused.
- Approving only makes an action **eligible** to run once; the console **never
  auto-executes** and has no vendor/payment path.
- Demo records are clearly labelled **synthetic**.

## 7. Audit export

The approval/audit export ([`src/approval/export.py`](../src/approval/export.py))
produces a read-only JSON or Markdown snapshot: for each hold, the approval id,
linked action id, risk level, status, requester/customer id, reviewer + reason,
timestamps, the audit-event history, and whether execution is `allowed`, `blocked`,
or `consumed`. It **mutates nothing**. Use it to hand a review off or keep evidence.
Download buttons live in the Approval Console; boundaries are in
[DATA_RETENTION.md](DATA_RETENTION.md).

## 8. Scenario runner outputs

To see all of the above produced from one ticket, run the
[scenario runner](SCENARIO_RUNNER_GUIDE.md):

```bash
python -m src.scenarios.runner examples/scenarios/high_risk_refund.json
```

Its per-stage report shows the broker decision, tool boundary, approval hold, audit
record, replay result, operator metrics, Hermes findings, and the approval export —
the same signals you inspect in the UI, for a single case.

## 9. Human-review cases (what you own)

You are accountable for every case the agent is not allowed to decide:

- any **escalated / handoff** turn (money, identity, low-confidence, unverifiable);
- any **pending high-risk approval** (approve or reject with a reason);
- any **critical/high Hermes finding or alert**;
- any **blocked replay** (a safety-critical inconsistency).

The system's job is to make each of these **visible, evidenced, and safe to hand
off**. The decision is yours — RelayOps keeps the human/operator accountable.
