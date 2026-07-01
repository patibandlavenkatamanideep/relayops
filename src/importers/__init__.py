"""Redacted ticket import (v2.6).

A deterministic, local, read-only importer for a small redacted sample of support
tickets (CSV or JSONL). It validates and normalizes rows into ``TicketRecord``s
and a skip summary, so a design partner can be evaluated without any vendor
integration, credential, or real customer PII. It never runs the agent, executes
an action, or touches the audit store.

    redacted CSV/JSONL -> import_file() -> ImportResult (records + skips)
"""

from __future__ import annotations

from .schemas import (
    ImportResult,
    SkippedRow,
    TicketRecord,
    normalize_category,
    normalize_sensitivity,
)
from .ticket_importer import (
    import_csv,
    import_file,
    import_jsonl,
    import_rows,
)

__all__ = [
    "TicketRecord",
    "SkippedRow",
    "ImportResult",
    "normalize_category",
    "normalize_sensitivity",
    "import_rows",
    "import_csv",
    "import_jsonl",
    "import_file",
]
