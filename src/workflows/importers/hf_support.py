"""Importer for Hugging Face customer-support ticket datasets.

Targets the common helpdesk shape (e.g. ``Tobi-Bueck/customer-support-tickets``):
``subject``, ``body``/``text``, ``type``, ``queue``, ``priority``, ``answer``.
The agent's reference ``answer`` is kept in metadata (useful later for
handoff/evidence comparison) but is never fed to the agent — RelayOps must decide
from the customer message alone.

    python3 -m src.workflows.importers.hf_support --input tickets.jsonl --output var/imported_public_tickets.jsonl
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..normalize_ticket import iter_raw_rows, normalize, pick
from . import LoadResult, cli_main

SOURCE = "hf_customer_support"

_ID = ("id", "ticket_id", "uuid")
_SUBJECT = ("subject", "title")
_BODY = ("body", "text", "email_text", "message", "content")
_TYPE = ("type", "category")
_QUEUE = ("queue", "department")
_PRIORITY = ("priority",)


def _message(row: dict[str, Any]) -> Optional[str]:
    parts = [p for p in (pick(row, _SUBJECT), pick(row, _BODY)) if p]
    return ". ".join(parts) if parts else None


def load(path: Path | str, limit: Optional[int] = None) -> LoadResult:
    tickets: list[dict[str, Any]] = []
    unmapped = 0
    for idx, row in enumerate(iter_raw_rows(path)):
        if limit is not None and len(tickets) >= limit:
            break
        ticket = normalize(
            ticket_id=pick(row, _ID) or f"hf_{idx}",
            message=_message(row),
            source=SOURCE,
            category=pick(row, _TYPE) or pick(row, _QUEUE),
            priority=pick(row, _PRIORITY),
            status=None,
            original_fields=row,
        )
        if ticket is None:
            unmapped += 1
        else:
            tickets.append(ticket)
    return tickets, unmapped


if __name__ == "__main__":
    cli_main(SOURCE, load)
