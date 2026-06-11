"""Importer for the Kaggle "Customer Support Ticket Dataset" family.

Typical columns: ``Ticket ID``, ``Ticket Subject``, ``Ticket Description``,
``Ticket Type``, ``Ticket Priority``, ``Ticket Status``. We stitch subject +
description into the message and keep the rest as metadata. Column matching is
case-insensitive and tolerant of the common variants across the several Kaggle
support-ticket dumps.

    python3 -m src.workflows.importers.kaggle_support --input tickets.csv --output var/imported_public_tickets.jsonl
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..normalize_ticket import iter_raw_rows, normalize, pick
from . import LoadResult, cli_main

SOURCE = "kaggle_customer_support"

_ID = ("Ticket ID", "ticket_id", "id")
_SUBJECT = ("Ticket Subject", "subject", "title")
_BODY = ("Ticket Description", "description", "body", "text", "message")
_TYPE = ("Ticket Type", "type", "category", "ticket_type")
_PRIORITY = ("Ticket Priority", "priority")
_STATUS = ("Ticket Status", "status")


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
            ticket_id=pick(row, _ID) or f"kaggle_{idx}",
            message=_message(row),
            source=SOURCE,
            category=pick(row, _TYPE),
            priority=pick(row, _PRIORITY),
            status=pick(row, _STATUS),
            original_fields=row,
        )
        if ticket is None:
            unmapped += 1
        else:
            tickets.append(ticket)
    return tickets, unmapped


if __name__ == "__main__":
    cli_main(SOURCE, load)
