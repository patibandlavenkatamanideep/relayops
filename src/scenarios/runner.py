"""End-to-end scenario runner (v2.9).

Runs one synthetic, redacted ticket through the *entire* RelayOps control plane
and records a readable, deterministic lifecycle — so the project can be demoed
with "run one scenario and watch every layer act" instead of explaining each
module separately.

For one ``Scenario`` the runner walks:

    ingest → auth/scope → broker decision → action envelope → tool boundary →
    approval requirement (if high-risk) → audit record → replay verification →
    operator metrics → Hermes review/alerting → approval/audit export → final report

It composes the real modules (`graph.pipeline`, `observability.audit_ledger`,
`replay`, `operator_metrics`, `hermes`, `approval`) — it does not re-implement
them. It is deterministic and local: no vendor calls, no credentials, no real
customer data, and no real external execution. The one deliberately synthetic
touch is the optional replay-drift injection, which mutates a *copy* of the
replayed audit record to demonstrate the verifier catching an inconsistency.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Optional

from ..approval import ApprovalQueue, build_approval_export
from ..approval import policy as approval_policy
from ..hermes import build_alert_report, review, review_approval_requests
from ..observability.audit_ledger import AuditLedger
from ..operator_metrics import operator_metrics
from ..replay import replay_metrics, verify
from ..router.classifier import BaselineClassifier, IntentClassifier
from .models import (
    BLOCKED,
    ESCALATED,
    INFO,
    OK,
    Scenario,
    ScenarioResult,
    StageResult,
)

# Fixed clock so approval records in a scenario export are reproducible.
_T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)

# Audit action_class -> (approval action name, ...). The approval policy then maps
# the name to a risk level (refund/high, account_access_change/critical, etc.).
_APPROVAL_ACTION = {
    "billing_refund": "refund",
    "plan_change": "plan_change",
    "account_access_change": "account_access_change",
    "device_reset": "device_reset",
    "send_troubleshooting_link": "send_troubleshooting_link",
    "account_read": "account_read",
    "unknown": "unknown_action",
}

_HANDOFF_ROUTE = "human_escalation"
_ACTION_ROUTE = "auto_action"


def _lazy_handle_turn():
    # Local import: pipeline imports many heavy modules; keep import cost off the
    # module load path so `import src.scenarios` stays cheap.
    from ..graph.pipeline import handle_turn

    return handle_turn


def _run_turn(scenario: Scenario, classifier: IntentClassifier, turn_id: str):
    handle_turn = _lazy_handle_turn()
    ledger = AuditLedger()
    response = handle_turn(
        scenario.message,
        auth_token=scenario.auth_token,
        device_id=scenario.device_id,
        classifier=classifier,
        classifier_name=type(classifier).__name__,
        audit=ledger,
        turn_id=turn_id,
    )
    return response, ledger.records[-1].to_dict()


def _first_envelope(rec: dict) -> dict:
    envelopes = rec.get("action_envelopes") or []
    return envelopes[0] if envelopes else {}


def _inject_drift(rec: dict, kind: str) -> dict:
    """Return a drifted COPY of an audit record to demonstrate replay catching an
    inconsistency. Never mutates the original; purely illustrative."""
    drifted = copy.deepcopy(rec)
    if kind == "broker":
        packet = drifted.setdefault("broker_decision_packet", {})
        packet["reason_code"] = (packet.get("reason_code") or "") + "_DRIFTED"
    elif kind == "scope":
        drifted["customer_id"] = (drifted.get("customer_id") or "cust") + "_other"
    return drifted


def run_scenario(
    scenario: Scenario,
    *,
    classifier: Optional[IntentClassifier] = None,
) -> ScenarioResult:
    """Run one scenario through the full control plane and return its lifecycle."""
    classifier = classifier or BaselineClassifier()
    result = ScenarioResult(scenario_id=scenario.id, title=scenario.title)

    response, rec = _run_turn(scenario, classifier, turn_id=f"{scenario.id}_orig")
    route = rec.get("route", "")
    result.route = route
    result.escalated = bool(response.escalated)
    result.final_disposition = response.disposition.value

    # 1. ingest -----------------------------------------------------------------
    result.stages.append(
        StageResult(
            "ingest",
            OK,
            f"Redacted ticket accepted: “{scenario.message}”",
            {"scenario": scenario.id, "description": scenario.description},
        )
    )

    # 2. auth / scope -----------------------------------------------------------
    authed = bool(rec.get("authenticated"))
    gate = rec.get("access_gate") or {}
    scope_ok = bool(gate.get("allowed"))
    if not authed:
        auth_stage = StageResult(
            "auth_scope", BLOCKED, "Unauthenticated caller — no customer scope granted.", gate
        )
    elif not scope_ok:
        auth_stage = StageResult(
            "auth_scope",
            BLOCKED,
            f"Authenticated as {rec.get('customer_id')} but request left its scope "
            "(cross-customer / scope violation).",
            gate,
        )
    else:
        auth_stage = StageResult(
            "auth_scope",
            OK,
            f"Authenticated and scoped to {rec.get('customer_id')}.",
            gate,
        )
    result.stages.append(auth_stage)

    # 3. broker decision --------------------------------------------------------
    broker = rec.get("broker_decision_packet") or {}
    decision = broker.get("decision", "n/a")
    result.stages.append(
        StageResult(
            "broker_decision",
            OK if decision == "allow" else ESCALATED,
            f"Broker {decision} · rule={broker.get('matched_rule', '—')} · "
            f"reason={broker.get('reason_code', '—')}",
            {
                "decision": decision,
                "policy_handle": broker.get("policy_handle", ""),
                "reason_code": broker.get("reason_code", ""),
            },
        )
    )

    # 4. action envelope --------------------------------------------------------
    env = _first_envelope(rec)
    if env:
        result.stages.append(
            StageResult(
                "action_envelope",
                OK if env.get("status") == "succeeded" else INFO,
                f"Envelope {env.get('action')} → {env.get('target_resource')} "
                f"[{env.get('status')}] (idempotency={env.get('idempotency_key')})",
                {k: env.get(k) for k in ("action", "target_resource", "status", "policy_handle")},
            )
        )
    else:
        result.stages.append(
            StageResult(
                "action_envelope",
                INFO,
                "No side-effecting action wrapped (read-only, escalated, or blocked "
                "before any action).",
                {},
            )
        )

    # 5. tool boundary ----------------------------------------------------------
    tool = rec.get("tool_call")
    if tool is None:
        result.stages.append(
            StageResult("tool_boundary", INFO, "No scoped tool was invoked this turn.", {})
        )
    elif tool.get("ok"):
        result.stages.append(
            StageResult(
                "tool_boundary",
                OK,
                f"Scoped tool '{tool.get('name')}' executed within customer scope.",
                tool,
            )
        )
    else:
        result.stages.append(
            StageResult(
                "tool_boundary",
                BLOCKED,
                f"Scoped tool '{tool.get('name')}' refused: {tool.get('error')}.",
                tool,
            )
        )

    # 6. approval requirement (v2.7/v2.8) ---------------------------------------
    queue = ApprovalQueue()
    approval_stage, approval_required, blocked = _approval_stage(scenario, rec, queue)
    result.approval_required = approval_required
    result.execution_blocked = blocked
    result.stages.append(approval_stage)

    # 7. audit record -----------------------------------------------------------
    result.stages.append(
        StageResult(
            "audit_record",
            OK,
            f"Deterministic audit record written (schema {rec.get('schema_version')}, "
            f"route={route}).",
            {"turn_id": rec.get("turn_id"), "action_class": rec.get("action_class")},
        )
    )

    # 8. replay verification ----------------------------------------------------
    _, replay_rec = _run_turn(scenario, classifier, turn_id=f"{scenario.id}_replay")
    if scenario.inject_replay_drift:
        replay_rec = _inject_drift(replay_rec, scenario.inject_replay_drift)
    verification = verify(rec, replay_rec)
    result.replay_status = verification.status
    reason_codes = verification.reason_codes()
    result.stages.append(
        StageResult(
            "replay_verification",
            OK if verification.passed() else BLOCKED,
            (
                "Replay matches the original audited flow."
                if verification.passed()
                else f"Replay verifier caught drift: {', '.join(reason_codes) or verification.status}."
            ),
            {"status": verification.status, "reason_codes": reason_codes},
        )
    )

    # 9. operator metrics -------------------------------------------------------
    metrics = operator_metrics([rec], replay_metrics([verification]))
    metrics_dict = metrics.to_dict()
    result.stages.append(
        StageResult(
            "operator_metrics",
            INFO,
            f"resolution_rate={metrics_dict.get('resolution_rate')} · "
            f"handoff_rate={metrics_dict.get('handoff_rate')} · "
            f"unsafe_escape_rate={metrics_dict.get('unsafe_escape_rate')}",
            metrics_dict,
        )
    )

    # 10. Hermes review / alerting (read-only, advisory) ------------------------
    findings = review([rec])
    approval_findings = review_approval_requests(list(queue.requests.values()))
    all_findings = findings + approval_findings
    alerts = build_alert_report([rec])
    result.stages.append(
        StageResult(
            "hermes_review",
            INFO,
            f"Hermes: {len(all_findings)} advisory finding(s), {len(alerts.alerts)} alert(s) "
            "— read-only, never approves/executes.",
            {
                "findings": [f.to_dict() for f in all_findings],
                "alerts": [a.to_dict() for a in alerts.alerts],
            },
        )
    )

    # 11. approval / audit export -----------------------------------------------
    export = build_approval_export(queue, now=_T0)
    result.stages.append(
        StageResult(
            "approval_export",
            INFO,
            f"Approval export: {export.total} hold(s), "
            f"{export.blocked_count} blocked from executing.",
            export.to_dict(),
        )
    )

    # 12. final report ----------------------------------------------------------
    result.stages.append(
        StageResult(
            "final_report",
            OK,
            f"Disposition: {response.disposition.value}"
            + (" (escalated to a human)" if response.escalated else "")
            + ". A human/operator remains accountable.",
            {"reply": response.text},
        )
    )

    return result


def _approval_stage(
    scenario: Scenario, rec: dict, queue: ApprovalQueue
) -> tuple[StageResult, bool, bool]:
    """Evaluate the human-approval requirement for the turn's action and register a
    hold in ``queue`` when approval is required. Returns (stage, required, blocked)."""
    action_class = rec.get("action_class", "unknown")
    name = _APPROVAL_ACTION.get(action_class, "unknown_action")
    policy_result = approval_policy.evaluate_action(name)
    route = rec.get("route", "")

    if not policy_result.approval_required:
        return (
            StageResult(
                "approval",
                OK,
                f"No human approval required ({policy_result.risk_level}-risk action).",
                {"risk_level": policy_result.risk_level, "approval_required": False},
            ),
            False,
            False,
        )

    env = _first_envelope(rec)
    broker = rec.get("broker_decision_packet") or {}
    request = queue.request_approval(
        action=name,
        target_resource=env.get("target_resource") or scenario.device_id or "n/a",
        customer_id=rec.get("customer_id") or "",
        requester_id=rec.get("customer_id") or "caller",
        action_id=env.get("action_id", ""),
        policy_handle=broker.get("policy_handle", ""),
        now=_T0,
    )
    # High-risk actions cannot auto-execute: the hold is pending a human decision.
    blocked = not queue.authorize_execution(request.request_id, now=_T0)
    note = (
        f"{policy_result.risk_level.title()}-risk action → human approval required; "
        "held pending review (cannot auto-execute)."
    )
    if route == _HANDOFF_ROUTE:
        note += " The pipeline also escalated this turn to a human."
    return (
        StageResult(
            "approval",
            BLOCKED,
            note,
            {
                "risk_level": policy_result.risk_level,
                "approval_required": True,
                "approval_id": request.request_id,
                "status": request.status,
            },
        ),
        True,
        blocked,
    )


def run_scenarios(
    scenarios: list[Scenario], *, classifier: Optional[IntentClassifier] = None
) -> list[ScenarioResult]:
    """Run many scenarios; each is independent and deterministic."""
    return [run_scenario(s, classifier=classifier) for s in scenarios]


def render_markdown(results: list[ScenarioResult]) -> str:
    """Render a batch of scenario results as a single Markdown demo report."""
    lines = [
        "# RelayOps end-to-end scenario report",
        "",
        "_Deterministic, local walk of the full control plane over synthetic, "
        "redacted tickets. No vendor calls, no credentials, no real customer data, "
        "no real external execution. A human/operator remains accountable._",
        "",
    ]
    for result in results:
        lines.append(result.to_markdown())
    return "\n".join(lines)


def _main() -> None:  # pragma: no cover - thin CLI wrapper
    import argparse
    import json

    from .sample_scenarios import SAMPLE_SCENARIOS, load_scenario

    parser = argparse.ArgumentParser(
        description="Run RelayOps end-to-end scenarios (deterministic, local, read-only demo)."
    )
    parser.add_argument("path", nargs="?", help="a scenario .json file; omit to run all samples")
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args()

    if args.path:
        results = [run_scenario(load_scenario(args.path))]
    else:
        results = run_scenarios(SAMPLE_SCENARIOS)

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        print(render_markdown(results))


if __name__ == "__main__":  # pragma: no cover
    _main()
