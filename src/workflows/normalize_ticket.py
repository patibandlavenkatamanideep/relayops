"""Canonical ticket schema + a tolerant CSV/JSONL reader.

Every public dataset (Kaggle, Hugging Face, Twitter, ...) has a different column
layout. The importers in ``importers/`` map each one onto the single canonical
schema below, so the batch runner, audit ledger, and metrics never have to know
where a ticket came from:

    {
      "ticket_id": "...",
      "customer_id": "public_dataset_customer",   # public data has no real auth
      "message": "...",
      "source": "kaggle_customer_support",
      "metadata": {
        "category": "...",
        "priority": "...",
        "status": "...",
        "original_fields": { ... }                 # raw row, for traceability
      }
    }

Honesty note: a ``customer_id`` of ``public_dataset_customer`` means there is no
authenticated identity behind the ticket. The runner authenticates these under a
synthetic sandbox customer (``--assume-customer``) only so the *routing / safety /
handoff* logic can be exercised on real language — it is **public-dataset
validation, not design-partner data and not production traffic**.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterator, Optional

# Public tickets carry no real authenticated customer.
PUBLIC_CUSTOMER_ID = "public_dataset_customer"


def iter_raw_rows(path: Path | str) -> Iterator[dict[str, Any]]:
    """Yield raw rows from a ``.csv`` or ``.jsonl`` file as dicts.

    Kept dependency-free (stdlib ``csv`` / ``json``) so importing a dataset never
    requires pandas. A ``.json`` array file is also accepted."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        with p.open(newline="", encoding="utf-8") as f:
            yield from csv.DictReader(f)
    elif suffix in (".jsonl", ".ndjson"):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)
    elif suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or []
        for row in data:
            if isinstance(row, dict):
                yield row
    else:
        raise ValueError(f"unsupported dataset format: {p.suffix!r} (use .csv/.jsonl/.json)")


def pick(row: dict[str, Any], candidates: tuple[str, ...]) -> Optional[str]:
    """First non-empty value among candidate column names (case-insensitive)."""
    lower = {k.lower(): v for k, v in row.items()}
    for c in candidates:
        v = lower.get(c.lower())
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def normalize(
    *,
    ticket_id: Any,
    message: Optional[str],
    source: str,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    original_fields: Optional[dict[str, Any]] = None,
    customer_id: str = PUBLIC_CUSTOMER_ID,
) -> Optional[dict[str, Any]]:
    """Build one canonical ticket. Returns ``None`` when there is no usable
    message — the caller counts that as an *unmapped* row."""
    msg = (message or "").strip()
    if not msg:
        return None
    return {
        "ticket_id": str(ticket_id),
        "customer_id": customer_id,
        "message": msg,
        "source": source,
        "metadata": {
            "category": category,
            "priority": priority,
            "status": status,
            "original_fields": original_fields or {},
        },
    }


def write_jsonl(tickets: list[dict[str, Any]], path: Path | str) -> int:
    out = Path(path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(t) for t in tickets) + ("\n" if tickets else ""))
    return len(tickets)
