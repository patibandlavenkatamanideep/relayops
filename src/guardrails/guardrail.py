"""Independent guardrail layer — a separate gate, not inline logic.

Runs on the *candidate* reply after the model/composer writes it and before it
ships. It can **block** (force a human handoff) or **redact**. v1 rules:

  1. Truthfulness / allowed-offers  -> BLOCK. Any price/discount/paid-offer claim
     not backed by the approved catalog (DATA) is treated as a hallucination.
  2. PII leakage                    -> REDACT. Card numbers / SSNs never go out.
  3. Tone                           -> BLOCK. A minimal abusive-language screen.

Deferred (designed, not built): citation-grounded fact verification against RAG,
brand-voice scoring, a larger PII taxonomy. The interface stays the same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import catalog

# --- detectors -----------------------------------------------------------------

# Currency amounts: $9, $9.99, $1,000.00
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
# Percentage discounts: "20% off", "50 % discount"
_PERCENT_OFF = re.compile(r"\b\d{1,3}\s?%\s*(?:off|discount)\b", re.IGNORECASE)
# Recurring pricing phrasing: "$5/month", "9.99 per month"
_RECURRING = re.compile(r"(?:\$?\d[\d,]*(?:\.\d{2})?)\s*(?:/|per)\s*(?:mo|month|yr|year)", re.IGNORECASE)

# PII to redact from outbound text.
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_ABUSIVE = ("idiot", "stupid", "shut up")  # minimal tone screen


@dataclass
class GuardrailResult:
    action: str = "pass"            # pass | redact | block
    text: str = ""                  # possibly-redacted reply (when not blocked)
    violations: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action == "block"


def _normalise_amount(token: str) -> str:
    return token.replace(" ", "").lower()


def check(candidate: str) -> GuardrailResult:
    """Vet a candidate reply. Blocking takes precedence over redaction."""
    violations: list[str] = []

    # --- 1. truthfulness / allowed-offers (block) ---
    for m in _MONEY.finditer(candidate):
        if _normalise_amount(m.group()) not in catalog.APPROVED_AMOUNTS:
            violations.append(f"unapproved_amount:{m.group().strip()}")
    if _PERCENT_OFF.search(candidate):
        violations.append("unapproved_discount")
    if _RECURRING.search(candidate):
        violations.append("unapproved_recurring_price")

    # --- 3. tone (block) ---
    low = candidate.lower()
    for word in _ABUSIVE:
        if word in low:
            violations.append(f"tone:{word}")

    if violations:
        return GuardrailResult(action="block", text="", violations=violations)

    # --- 2. PII (redact) ---
    redacted, n_card = _CARD.subn("[redacted-card]", candidate)
    redacted, n_ssn = _SSN.subn("[redacted-ssn]", redacted)
    if n_card or n_ssn:
        if n_card:
            violations.append("pii:card")
        if n_ssn:
            violations.append("pii:ssn")
        return GuardrailResult(action="redact", text=redacted, violations=violations)

    return GuardrailResult(action="pass", text=candidate, violations=[])
