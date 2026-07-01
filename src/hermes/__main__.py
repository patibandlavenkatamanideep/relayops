"""Hermes operator CLI — review the durable audit store.

Reads decision traces from the SQLite audit store, runs the read-only Hermes
reviewer, and prints an operator report (failure summary, suggested tests,
policy gaps, draft issues, release notes). Advisory only — it changes nothing.

Usage:
    python3 -m src.hermes                 # summary over recent turns
    python3 -m src.hermes --limit 500     # widen the window
    python3 -m src.hermes --json          # full report as JSON
    python3 -m src.hermes --strict        # exit non-zero on a critical alert (CI gate)
    python3 -m src.hermes --db path.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sys

from ..observability.audit_store import DEFAULT_DB_PATH, AuditStore
from .alerting import build_alert_report
from .report import build_report


def _main() -> None:
    parser = argparse.ArgumentParser(description="Hermes operator review (read-only)")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite audit store path")
    parser.add_argument("--limit", type=int, default=200, help="how many recent turns to review")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any critical alert fires (operator CI gate)",
    )
    args = parser.parse_args()

    store = AuditStore(args.db)
    rows = store.list(limit=args.limit)
    report = build_report(rows)
    alerts = build_alert_report(rows)
    store.close()

    if args.json:
        out = report.to_dict()
        out["alerts"] = alerts.to_dict()
        print(json.dumps(out, indent=2))
        if args.strict and alerts.has_critical:
            sys.exit(1)
        return

    print(f"Hermes review — {len(rows)} turn(s) from {store.db_path}")
    print(f"\n{report.failure_summary}")

    m = report.metrics
    if m:
        print("\nSafety metrics:")
        print(
            f"  unsafe_escape_rate       : {m['unsafe_escape_rate']}  ({m['unsafe_escape_count']})"
        )
        print(f"  over_block_rate          : {m['over_block_rate']}  ({m['over_block_count']})")
        print(f"  fail_closed_rate         : {m['fail_closed_rate']}  ({m['fail_closed_count']})")
        print(f"  guardrail_block_count    : {m['guardrail_block_count']}")
        score = m["handoff_completeness_score"]
        print(f"  handoff_completeness     : {'n/a' if score is None else score}")

    om = report.operator_metrics
    if om:

        def _fmt(value: object) -> str:
            return "n/a" if value is None else str(value)

        print("\nOperator metrics:")
        print(f"  resolution_rate          : {om['resolution_rate']}  ({om['resolved_count']})")
        print(f"  handoff_rate             : {om['handoff_rate']}  ({om['handoff_count']})")
        print(
            f"  action_execution_rate    : {om['action_execution_rate']}  "
            f"({om['action_execution_count']})"
        )
        print(f"  replay_success_rate      : {_fmt(om['replay_success_rate'])}")
        print(f"  replay_mismatch_rate     : {_fmt(om['replay_mismatch_rate'])}")
        print(f"  avg_turns_to_resolution  : {om['avg_turns_to_resolution']}")
        print(f"  est_cost_per_resolved    : ${om['estimated_cost_per_resolved_ticket']}")

    print(f"\nOperator alerts: {alerts.summary}")
    for a in alerts.alerts:
        print(
            f"  [{a.severity}] {a.metric}: observed {a.observed}, "
            f"budget {a.comparison} {a.threshold}"
        )

    if report.suggested_tests:
        print("\nSuggested tests:")
        for t in report.suggested_tests:
            print(f"  - {t}")

    if report.suggested_policy_gaps:
        print("\nPolicy gaps to review:")
        for g in report.suggested_policy_gaps:
            print(f"  - {g}")

    if report.suggested_github_issues:
        print("\nDraft GitHub issues (urgent findings):")
        for issue in report.suggested_github_issues:
            print(f"  - {issue['title']}")

    print("\nRelease-notes draft:")
    print(report.release_notes_draft)
    print("\n(Advisory only — every finding requires human review.)")

    if args.strict and alerts.has_critical:
        sys.exit(1)


if __name__ == "__main__":
    _main()
