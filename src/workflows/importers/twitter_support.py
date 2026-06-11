"""Importer for the "Customer Support on Twitter" dataset.

Columns: ``tweet_id``, ``author_id``, ``inbound`` (True for customer messages),
``created_at``, ``text``, ``response_tweet_id``, ``in_response_to_tweet_id``.
Only **inbound** (customer-authored) tweets become tickets — outbound brand
replies are not support requests. This is the messiest source on purpose: real
public language, @mentions, and slang stress-test the classifier and guardrail.

    python3 -m src.workflows.importers.twitter_support --input twcs.csv --output var/imported_public_tickets.jsonl
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..normalize_ticket import iter_raw_rows, normalize, pick
from . import LoadResult, cli_main

SOURCE = "twitter_customer_support"

_ID = ("tweet_id", "id")
_TEXT = ("text", "message", "body")
_INBOUND = ("inbound",)
_AUTHOR = ("author_id", "author")

_TRUTHY = {"true", "1", "yes", "t"}


def _is_inbound(row: dict[str, Any]) -> bool:
    """Customer-authored tweets only. If the column is absent, keep the row."""
    v = pick(row, _INBOUND)
    return True if v is None else v.strip().lower() in _TRUTHY


def load(path: Path | str, limit: Optional[int] = None) -> LoadResult:
    tickets: list[dict[str, Any]] = []
    unmapped = 0
    for idx, row in enumerate(iter_raw_rows(path)):
        if limit is not None and len(tickets) >= limit:
            break
        if not _is_inbound(row):
            continue  # outbound brand reply — not a support request
        ticket = normalize(
            ticket_id=pick(row, _ID) or f"twitter_{idx}",
            message=pick(row, _TEXT),
            source=SOURCE,
            category=None,
            priority=None,
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
