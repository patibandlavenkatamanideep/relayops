"""Redacted ticket importer (v2.6) — deterministic, local, read-only.

Loads a small *redacted* sample of support tickets from CSV or JSONL, validates
and normalizes each row, and returns the accepted ``TicketRecord`` set plus a
per-row skip summary. It is intentionally boring and safe:

  * fully deterministic — same input, same output, no model and no clock;
  * fully local — it reads a file and parses it, nothing else. No network, no
    credentials, no external service, no side effects;
  * whitelist-only — only the known schema fields are copied off a row, so a
    stray secret column in a partner export is never imported into RelayOps.

It never runs the agent, executes an action, or writes to the audit store — it
only turns a file into validated records for the design-partner report.

CLI:
    python3 -m src.importers.ticket_importer examples/redacted_tickets.csv
    python3 -m src.importers.ticket_importer examples/redacted_tickets.jsonl --json
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .schemas import (
    KNOWN_FIELDS,
    REQUIRED_FIELDS,
    ImportResult,
    SkippedRow,
    TicketRecord,
    normalize_sensitivity,
)


def _clean(value: Any) -> str:
    """Coerce a cell/field to a stripped string ("" for None/missing)."""
    if value is None:
        return ""
    return str(value).strip()


def _validated_record(
    index: int, row: dict[str, Any]
) -> tuple[TicketRecord | None, SkippedRow | None]:
    """Validate + normalize one row. Returns (record, None) or (None, skip)."""
    # Whitelist: only known fields are ever read off the row. Anything else
    # (an accidental api_key / password / ssn column) is dropped here.
    fields = {name: _clean(row.get(name)) for name in KNOWN_FIELDS}
    ticket_id = fields["ticket_id"]

    # Required fields must be present and non-empty. An empty message gets its own
    # reason because it is the single most common redaction/export mistake.
    for name in REQUIRED_FIELDS:
        if fields[name]:
            continue
        reason = "empty_message" if name == "message" else f"missing_required_field:{name}"
        return None, SkippedRow(row_index=index, ticket_id=ticket_id, reason=reason)

    # Sensitivity must resolve to a canonical level; unknown values are skipped
    # deterministically rather than guessed.
    level = normalize_sensitivity(fields["sensitivity_level"])
    if level is None:
        return None, SkippedRow(
            row_index=index,
            ticket_id=ticket_id,
            reason=f"invalid_sensitivity_level:{fields['sensitivity_level']}",
        )

    record = TicketRecord(
        ticket_id=ticket_id,
        customer_id=fields["customer_id"],
        message=fields["message"],
        category=fields["category"],  # raw label kept; report normalizes for analysis
        expected_resolution=fields["expected_resolution"],
        sensitivity_level=level,
        created_at=fields["created_at"],
        channel=fields["channel"],
        priority=fields["priority"],
        historical_outcome=fields["historical_outcome"],
    )
    return record, None


def import_rows(rows: list[dict[str, Any]], source: str | None = None) -> ImportResult:
    """Validate + normalize already-parsed rows into an ``ImportResult``.

    Pure and deterministic; does not mutate the input ``rows`` or their dicts.
    """
    result = ImportResult(total_received=len(rows), source=source)
    for index, row in enumerate(rows):
        record, skip = _validated_record(index, row if isinstance(row, dict) else {})
        if record is not None:
            result.records.append(record)
        else:
            # A non-dict row (malformed line) still gets a deterministic skip.
            result.skipped.append(
                skip
                if skip is not None
                else SkippedRow(row_index=index, ticket_id="", reason="not_an_object")
            )
    return result


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    if not text.strip():
        return []
    reader = csv.DictReader(text.splitlines())
    return [dict(row) for row in reader]


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            # A malformed JSON line becomes a non-dict row -> deterministic skip.
            rows.append({"__malformed__": line})
            continue
        rows.append(parsed if isinstance(parsed, dict) else {"__malformed__": line})
    return rows


def import_csv(path: Path | str, source: str | None = None) -> ImportResult:
    """Import redacted tickets from a CSV file."""
    p = Path(path)
    return import_rows(_read_csv_rows(p), source=source or p.name)


def import_jsonl(path: Path | str, source: str | None = None) -> ImportResult:
    """Import redacted tickets from a JSONL file."""
    p = Path(path)
    return import_rows(_read_jsonl_rows(p), source=source or p.name)


def import_file(path: Path | str, source: str | None = None) -> ImportResult:
    """Import from a file, dispatching on suffix (``.csv`` vs ``.jsonl``/``.json``)."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        return import_csv(p, source=source)
    if suffix in (".jsonl", ".json", ".ndjson"):
        return import_jsonl(p, source=source)
    raise ValueError(f"unsupported ticket file type: {p.suffix!r} (use .csv or .jsonl)")


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RelayOps redacted ticket importer (read-only)")
    parser.add_argument("path", help="redacted tickets file (.csv or .jsonl)")
    parser.add_argument("--json", action="store_true", help="print the ImportResult as JSON")
    args = parser.parse_args()

    result = import_file(args.path)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return

    print(f"Ticket import — {result.source}")
    print(f"  received : {result.total_received}")
    print(f"  imported : {result.imported_count}")
    print(f"  skipped  : {result.skipped_count}")
    if result.skipped:
        print("\nSkipped rows:")
        for s in result.skipped:
            label = s.ticket_id or f"row {s.row_index}"
            print(f"  - {label}: {s.reason}")
    print("\n(Read-only, local, deterministic — no agent run, no external calls.)")


if __name__ == "__main__":
    _main()
