"""Redacted ticket schema + deterministic normalization (v2.6).

The design-partner import workflow accepts a small, *redacted* sample of support
tickets and reduces it to a stable in-memory shape. This module defines that
shape and the pure normalization rules the importer applies. Nothing here reads a
file, calls a network, or executes an action — it is data plus deterministic
functions.

A ticket row is expected to carry:

  * ``ticket_id``           — partner-side identifier (required)
  * ``customer_id``         — redacted/pseudonymous customer handle (required)
  * ``message``             — the (redacted) customer message (required)
  * ``category``            — partner's own category label, free text (required)
  * ``expected_resolution`` — what the partner expected to happen (required)
  * ``sensitivity_level``   — data-sensitivity tag (required, normalized below)

Optional fields (carried through when present, never required):
``created_at``, ``channel``, ``priority``, ``historical_outcome``.

Only these known fields are ever read into a ``TicketRecord`` — any other column
(an accidental ``api_key``, ``password``, ``ssn`` …) is dropped on import, so a
stray secret in a partner's export never lands in RelayOps state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Required and optional field names. Required fields must be present and non-empty
# for a row to import; optional fields are carried through verbatim when present.
REQUIRED_FIELDS: tuple[str, ...] = (
    "ticket_id",
    "customer_id",
    "message",
    "category",
    "expected_resolution",
    "sensitivity_level",
)
OPTIONAL_FIELDS: tuple[str, ...] = (
    "created_at",
    "channel",
    "priority",
    "historical_outcome",
)
# The whitelist: the only keys ever copied off an input row. Everything else is
# dropped so a stray credential column cannot be imported.
KNOWN_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + OPTIONAL_FIELDS

# Canonical data-sensitivity levels, least -> most sensitive.
SENSITIVITY_LEVELS: tuple[str, ...] = ("low", "medium", "high", "restricted")

# Deterministic synonym map for sensitivity. Keys are already lower/stripped. A
# value not resolvable through here (after the identity check) is rejected — the
# importer skips such a row rather than guessing.
_SENSITIVITY_ALIASES: dict[str, str] = {
    "low": "low",
    "none": "low",
    "normal": "low",
    "public": "low",
    "medium": "medium",
    "med": "medium",
    "moderate": "medium",
    "high": "high",
    "sensitive": "high",
    "critical": "high",
    "restricted": "restricted",
    "confidential": "restricted",
    "pii": "restricted",
    "secret": "restricted",
}

# Canonical category buckets the report reasons over. Partner categories are free
# text and normalized into one of these; anything unrecognized falls to "unknown"
# (which the report flags as a policy/tool coverage gap — it is NOT a skip).
CANONICAL_CATEGORIES: tuple[str, ...] = (
    "reset_device",
    "device_status",
    "device_faq",
    "billing",
    "account",
    "greeting",
    "unknown",
)

_CATEGORY_ALIASES: dict[str, str] = {
    # device reset (low-blast, reversible)
    "reset_device": "reset_device",
    "device_reset": "reset_device",
    "router_reset": "reset_device",
    "reset": "reset_device",
    "reboot": "reset_device",
    "restart": "reset_device",
    "restart_device": "reset_device",
    # device / connection status
    "device_status": "device_status",
    "status": "device_status",
    "connection": "device_status",
    "connection_status": "device_status",
    "outage": "device_status",
    # informational / FAQ
    "device_faq": "device_faq",
    "faq": "device_faq",
    "question": "device_faq",
    "how_to": "device_faq",
    "howto": "device_faq",
    "info": "device_faq",
    # billing / money-touching
    "billing": "billing",
    "refund": "billing",
    "payment": "billing",
    "invoice": "billing",
    "charge": "billing",
    "discount": "billing",
    "plan": "billing",
    "plan_change": "billing",
    # account / identity
    "account": "account",
    "account_change": "account",
    "password": "account",
    "password_reset": "account",
    "login": "account",
    "identity": "account",
    # greeting
    "greeting": "greeting",
    "hello": "greeting",
}


@dataclass(frozen=True)
class TicketRecord:
    """One imported, validated redacted ticket.

    ``category`` keeps the partner's *raw* label (stripped) so the report can flag
    unmapped categories by name; ``sensitivity_level`` is stored normalized to one
    of ``SENSITIVITY_LEVELS``. Optional fields default to "" when absent.
    """

    ticket_id: str
    customer_id: str
    message: str
    category: str
    expected_resolution: str
    sensitivity_level: str
    created_at: str = ""
    channel: str = ""
    priority: str = ""
    historical_outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkippedRow:
    """A row the importer refused, with a deterministic machine-readable reason."""

    row_index: int  # 0-based index within the input rows
    ticket_id: str  # best-effort id if present, else ""
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImportResult:
    """Outcome of an import: the accepted records plus the skipped-row summary."""

    records: list[TicketRecord] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    total_received: int = 0
    source: Optional[str] = None

    @property
    def imported_count(self) -> int:
        return len(self.records)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "total_received": self.total_received,
            "imported_count": self.imported_count,
            "skipped_count": self.skipped_count,
            "records": [r.to_dict() for r in self.records],
            "skipped": [s.to_dict() for s in self.skipped],
        }


def normalize_sensitivity(raw: Any) -> Optional[str]:
    """Map a raw sensitivity value to a canonical level, or ``None`` if unknown.

    Deterministic: lowercased/stripped, then resolved through the alias table. A
    ``None`` return tells the importer to skip the row (invalid sensitivity).
    """
    if raw is None:
        return None
    key = str(raw).strip().lower()
    if not key:
        return None
    return _SENSITIVITY_ALIASES.get(key)


def normalize_category(raw: Any) -> str:
    """Map a raw partner category to a canonical bucket (``"unknown"`` fallback).

    Never skips — an unmapped category is a *coverage signal* for the report, not
    a validation failure.
    """
    if raw is None:
        return "unknown"
    key = str(raw).strip().lower()
    if not key:
        return "unknown"
    return _CATEGORY_ALIASES.get(key, "unknown")
