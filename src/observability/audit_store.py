"""Durable audit store — persist the per-turn decision trail to SQLite.

v1.3 produced audit *records* in memory. v1.4 makes them durable: every turn is
written to a SQLite table so the decision trail survives the process and can be
exported as evidence (JSONL / CSV) for a reviewer or a regulator.

The table is intentionally flat (one row per turn) and the columns are exactly
the fields a telecom auditor asks for: who, when, what intent, what route, what
action class + blast radius, whether a tool fired, the guardrail verdict, the
handoff owner + deadline, the decision trace, context known/unavailable at
decision time, the evidence quote, and the final response.

CLI:
    python3 -m src.observability.audit_store --list
    python3 -m src.observability.audit_store --stats
    python3 -m src.observability.audit_store --export-jsonl [path]
    python3 -m src.observability.audit_store --export-csv  [path]
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from ..core.models import AgentResponse
from ..router.action_policy import handoff_completeness
from .audit_ledger import AuditRecord

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DIR = _REPO_ROOT / "var"
DEFAULT_DB_PATH = Path(os.environ.get("RELAYOPS_AUDIT_DB", _DEFAULT_DIR / "relayops_audit.sqlite3"))

# Column order is the canonical export order (JSONL/CSV follow it).
COLUMNS: tuple[str, ...] = (
    "turn_id",
    "timestamp",
    "customer_id",
    "authenticated",
    "intent",
    "classifier",
    "confidence",
    "route",
    "action_class",
    "blast_radius",
    "decision_steps",
    "proposed_action",
    "blocking_rule",
    "risk_signal",
    "available_context",
    "unavailable_context",
    "tool_call",
    "guardrail_verdict",
    "handoff_owner",
    "handoff_deadline",
    "handoff_reason",
    "handoff_complete",
    "evidence_quote",
    "final_response",
)

_CREATE = f"""
CREATE TABLE IF NOT EXISTS audit_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    {', '.join(f'{c} TEXT' for c in COLUMNS)}
)
"""


def _ensure_parent(path: Path | str) -> Path:
    out = Path(path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _tool_call_summary(record: AuditRecord) -> Optional[str]:
    return json.dumps(record.tool_call) if record.tool_call else None


def _json_summary(value: Any) -> str:
    return json.dumps(value)


def row_from(record: AuditRecord, response: AgentResponse) -> dict[str, Any]:
    """Flatten an AuditRecord + the turn's response into one storable row."""
    handoff = response.handoff_context or {}
    complete, _ = handoff_completeness(handoff)
    evidence_quote = record.evidence[0] if record.evidence else ""
    return {
        "turn_id": record.turn_id,
        "timestamp": record.timestamp,
        "customer_id": record.customer_id,
        "authenticated": int(record.authenticated),
        "intent": record.intent,
        "classifier": record.classifier,
        "confidence": record.confidence,
        "route": record.route,
        "action_class": record.action_class,
        "blast_radius": record.blast_radius,
        "decision_steps": _json_summary(record.decision_steps),
        "proposed_action": record.proposed_action,
        "blocking_rule": record.blocking_rule or "",
        "risk_signal": record.risk_signal,
        "available_context": _json_summary(record.available_context),
        "unavailable_context": _json_summary(record.unavailable_context),
        "tool_call": _tool_call_summary(record),
        "guardrail_verdict": record.guardrail.get("verdict"),
        "handoff_owner": handoff.get("owner", ""),
        "handoff_deadline": handoff.get("deadline", ""),
        "handoff_reason": record.handoff_reason or "",
        "handoff_complete": int(complete) if record.route == "human_escalation" else "",
        "evidence_quote": evidence_quote,
        "final_response": response.text,
    }


class AuditStore:
    """Thin SQLite wrapper. One connection; safe to share across Streamlit reruns
    (``check_same_thread=False``). A real deployment would point this at a managed
    Postgres with the same schema — the row shape is the contract, not SQLite."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent and not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(_CREATE)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(audit_turns)").fetchall()
        }
        for column in COLUMNS:
            if column not in existing:
                self.conn.execute(f"ALTER TABLE audit_turns ADD COLUMN {column} TEXT")

    # --- writes ---------------------------------------------------------------

    def write(self, record: AuditRecord, response: AgentResponse) -> None:
        self.write_row(row_from(record, response))

    def write_row(self, row: dict[str, Any]) -> None:
        placeholders = ", ".join("?" for _ in COLUMNS)
        self.conn.execute(
            f"INSERT INTO audit_turns ({', '.join(COLUMNS)}) VALUES ({placeholders})",
            tuple(row.get(c) for c in COLUMNS),
        )
        self.conn.commit()

    # --- reads ----------------------------------------------------------------

    def all(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM audit_turns ORDER BY id ASC"
        )
        return [dict(r) for r in cur.fetchall()]

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        """Most recent records first (for a live console)."""
        cur = self.conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM audit_turns ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM audit_turns").fetchone()[0])

    # --- exports --------------------------------------------------------------

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(r) for r in self.all())

    def to_csv(self) -> str:
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=COLUMNS)
        writer.writeheader()
        for r in self.all():
            writer.writerow(r)
        return buf.getvalue()

    def export_jsonl(self, path: Path | str) -> int:
        rows = self.all()
        out = _ensure_parent(path)
        out.write_text(self.to_jsonl() + ("\n" if rows else ""))
        return len(rows)

    def export_csv(self, path: Path | str) -> int:
        rows = self.all()
        out = _ensure_parent(path)
        out.write_text(self.to_csv())
        return len(rows)

    # --- aggregates (for the Decision Console) --------------------------------

    def stats(self) -> dict[str, Any]:
        rows = self.all()
        routes = Counter(r["route"] for r in rows)
        escalations = [r for r in rows if r["route"] == "human_escalation"]
        owners = Counter(r["handoff_owner"] for r in escalations if r["handoff_owner"])

        complete = sum(1 for r in escalations if str(r["handoff_complete"]) == "1")
        completeness = (complete / len(escalations)) if escalations else 1.0

        # Safety counters: an auto-action on a high-blast class would be unsafe;
        # a money-touching class that did NOT escalate is a billing escape.
        unsafe_auto = sum(
            1 for r in rows if r["route"] == "auto_action" and r["blast_radius"] == "high"
        )
        billing_escape = sum(
            1
            for r in rows
            if r["action_class"] in ("billing_refund", "plan_change")
            and r["route"] != "human_escalation"
        )

        return {
            "audit_records": len(rows),
            "route_distribution": dict(routes),
            "escalations": len(escalations),
            "guardrail_blocks": sum(1 for r in rows if r["guardrail_verdict"] == "block"),
            "handoff_owners": dict(owners),
            "handoff_completeness": completeness,
            "unsafe_auto_action": unsafe_auto,
            "billing_escape": billing_escape,
        }

    def close(self) -> None:
        self.conn.close()


# --- CLI -----------------------------------------------------------------------


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RelayOps durable audit store")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument("--list", action="store_true", help="print recent records")
    parser.add_argument("--limit", type=int, default=20, help="rows for --list")
    parser.add_argument("--stats", action="store_true", help="print aggregate stats")
    parser.add_argument(
        "--export-jsonl", nargs="?", const=str(_DEFAULT_DIR / "audit_export.jsonl"),
        help="export all records as JSONL (optional path)",
    )
    parser.add_argument(
        "--export-csv", nargs="?", const=str(_DEFAULT_DIR / "audit_export.csv"),
        help="export all records as CSV (optional path)",
    )
    args = parser.parse_args()

    store = AuditStore(args.db)
    did_something = False

    if args.list:
        did_something = True
        rows = store.list(args.limit)
        if not rows:
            print(f"(no audit records in {store.db_path})")
        for r in rows:
            print(
                f"{r['timestamp']}  {r['turn_id']}  {r['customer_id'] or '-':10}  "
                f"{r['intent']:13}  {r['route']:16}  {r['action_class']:22}  "
                f"guardrail={r['guardrail_verdict']}  owner={r['handoff_owner'] or '-'}"
            )

    if args.stats:
        did_something = True
        print(json.dumps(store.stats(), indent=2))

    if args.export_jsonl is not None:
        did_something = True
        n = store.export_jsonl(args.export_jsonl)
        print(f"exported {n} records -> {args.export_jsonl}")

    if args.export_csv is not None:
        did_something = True
        n = store.export_csv(args.export_csv)
        print(f"exported {n} records -> {args.export_csv}")

    if not did_something:
        print(f"audit store: {store.db_path} ({store.count()} records)")
        print("use --list, --stats, --export-jsonl, or --export-csv")

    store.close()


if __name__ == "__main__":
    _main()
