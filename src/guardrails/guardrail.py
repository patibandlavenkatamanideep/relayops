"""Independent guardrail layer — a separate gate, not inline logic.

Runs on the *candidate* reply after the model/composer writes it and before it
ships. It can **block** (force a human handoff) or **redact**. v1 rules:

  1. Truthfulness / allowed-offers  -> BLOCK. Any price/discount/paid-offer claim
     not backed by the approved catalog (DATA) is treated as a hallucination.
  2. PII leakage                    -> REDACT. Card numbers / SSNs never go out.
  3. Tone                           -> BLOCK. A minimal abusive-language screen.

On (1) the policy is asymmetric and deliberate: the catalog only approves *free*
/ $0, so any **nonzero** money claim must block regardless of phrasing. That
makes recall — not precision — the thing that matters, so the detector casts a
wide net across symbols ($/€/£), currency words ("20 dollars", "5 bucks"),
spelled-out amounts ("nine ninety-nine a month"), discounts ("half off",
"20 percent off"), and bare numbers next to a money cue ("a fee of 15"). A clean
operational reply ("reset takes 5 minutes") has no money cue and passes.

Layering, not regex-only: `check()` accepts an optional `semantic_backstop`
callable (e.g. an LLM judge). The cheap lexical net runs first and blocks the
obvious cases for free; the backstop is consulted only when lexical checks pass,
to catch novel phrasings the patterns miss. It is off by default so the pipeline
and tests stay deterministic and offline.

Deferred (designed, not built): citation-grounded fact verification against RAG,
brand-voice scoring, a larger PII taxonomy. The interface stays the same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import catalog

# --- money detectors -----------------------------------------------------------
# The approved set is only {free, $0, $0.00}, so the job is to *find* any money
# claim in any phrasing; a found amount that doesn't normalise to "$0"/"free"
# blocks. Each detector below appends a typed violation string.

# Currency symbols we recognise, before or after the number: $9, $9.99, €20, £5.
_CURRENCY_SYMBOL = r"[$€£]"
_AMOUNT_BODY = r"\d[\d,]*(?:\.\d{1,2})?"
_SYMBOL_AMOUNT = re.compile(
    rf"{_CURRENCY_SYMBOL}\s?{_AMOUNT_BODY}|{_AMOUNT_BODY}\s?{_CURRENCY_SYMBOL}"
)

# Currency words / codes after a number: "20 dollars", "5 bucks", "9.99 USD",
# "50 cents", "20 euros", "5 pounds", "2 grand".
_CURRENCY_WORD = r"(?:dollars?|bucks?|cents?|euros?|pounds?|quid|grand|usd|eur|gbp)"
_WORD_AMOUNT = re.compile(rf"\b{_AMOUNT_BODY}\s*{_CURRENCY_WORD}\b", re.IGNORECASE)
# ...and codes that precede the number: "USD 20", "EUR 9.99".
_CODE_AMOUNT = re.compile(rf"\b(?:usd|eur|gbp)\s?{_AMOUNT_BODY}\b", re.IGNORECASE)

# Spelled-out amounts. Kept simple on purpose: a run of number words ending in a
# currency word ("twenty dollars", "ninety nine cents") or a recurring phrase
# ("nine ninety-nine a month"). "zero"/"free" are handled by the approved set.
_NUM_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|grand)"
)
_NUM_WORDS = rf"(?:{_NUM_WORD}[\s-]+){{0,4}}{_NUM_WORD}"
_RECURRING_PHRASE = r"(?:/\s*(?:mo|yr)|(?:a|per)\s+(?:month|year|mo|yr))"
_SPELLED_CURRENCY = re.compile(rf"\b{_NUM_WORDS}\s+{_CURRENCY_WORD}\b", re.IGNORECASE)
_SPELLED_RECURRING = re.compile(rf"\b{_NUM_WORDS}\s+{_RECURRING_PHRASE}\b", re.IGNORECASE)

# Discounts: "20% off", "50 % discount", "20 percent off", "half off/price".
_PERCENT_OFF = re.compile(r"\b\d{1,3}\s?%\s*(?:off|discount)\b", re.IGNORECASE)
_PERCENT_WORD_OFF = re.compile(
    rf"\b(?:{_NUM_WORDS}|\d{{1,3}})\s*percent\s*(?:off|discount)\b", re.IGNORECASE
)
_HALF_OFF = re.compile(r"\bhalf\s*(?:off|price)\b", re.IGNORECASE)
_SAVE_AMOUNT = re.compile(
    rf"\b(?:save|discount of|off)\s*(?:{_CURRENCY_SYMBOL}\s?)?{_AMOUNT_BODY}", re.IGNORECASE
)

# Numeric recurring price with a symbol/word: "$5/month", "9.99 per month".
_RECURRING_NUM = re.compile(
    rf"(?:{_CURRENCY_SYMBOL}\s?)?{_AMOUNT_BODY}\s*{_RECURRING_PHRASE}", re.IGNORECASE
)

# A bare number sitting next to a money cue word: "a fee of 15", "it costs 20",
# "charged 9.99". The number and cue must be within a short window so plain
# operational numbers ("5 minutes", "ticket 4321") don't trip it.
_MONEY_CUE = r"(?:cost|costs|charge|charged|charges|fee|fees|pay|price|priced|pricing|bill|billed|billing|rate|subscription|surcharge|deposit|premium|upgrade fee)"
_CUE_THEN_NUM = re.compile(rf"\b{_MONEY_CUE}\b[^.\d]{{0,12}}({_AMOUNT_BODY})", re.IGNORECASE)
_NUM_THEN_CUE = re.compile(rf"({_AMOUNT_BODY})\s+(?:a|per)?\s*{_MONEY_CUE}\b", re.IGNORECASE)

# PII to redact from outbound text.
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_ABUSIVE = ("idiot", "stupid", "shut up")  # minimal tone screen

# Backstop signature: (candidate) -> True if it should be BLOCKED.
SemanticBackstop = Callable[[str], bool]


@dataclass
class GuardrailResult:
    action: str = "pass"  # pass | redact | block
    text: str = ""  # possibly-redacted reply (when not blocked)
    violations: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.action == "block"


def _normalise_amount(token: str) -> str:
    return re.sub(r"[$€£\s]", "", token).lower()


def _amount_is_zero(token: str) -> bool:
    """True for $0 / 0.00 / 0 — the only numeric amount the catalog approves."""
    digits = re.sub(r"[^\d.]", "", token)
    try:
        return float(digits) == 0.0
    except ValueError:
        return False


def _money_violations(candidate: str) -> list[str]:
    """Collect every nonzero / unapproved money claim, in any phrasing."""
    violations: list[str] = []

    # Symbol / word / code amounts: approved only if they normalise to $0.
    for rx in (_SYMBOL_AMOUNT, _WORD_AMOUNT, _CODE_AMOUNT):
        for m in rx.finditer(candidate):
            tok = m.group().strip()
            if _amount_is_zero(tok):
                continue
            if _normalise_amount(tok) not in catalog.APPROVED_AMOUNTS:
                violations.append(f"unapproved_amount:{tok}")

    # Bare numbers adjacent to a money cue ("a fee of 15", "charged 9.99").
    for rx in (_CUE_THEN_NUM, _NUM_THEN_CUE):
        for m in rx.finditer(candidate):
            num = m.group(1)
            if not _amount_is_zero(num):
                violations.append(f"unapproved_amount:{num.strip()}")

    # Spelled-out amounts and recurring phrasing — inherently nonzero here.
    if _SPELLED_CURRENCY.search(candidate) or _SPELLED_RECURRING.search(candidate):
        violations.append("unapproved_amount:spelled")
    if _RECURRING_NUM.search(candidate):
        violations.append("unapproved_recurring_price")

    # Discounts in any form.
    if (
        _PERCENT_OFF.search(candidate)
        or _PERCENT_WORD_OFF.search(candidate)
        or _HALF_OFF.search(candidate)
    ):
        violations.append("unapproved_discount")
    if _SAVE_AMOUNT.search(candidate):
        violations.append("unapproved_discount")

    # De-dupe while preserving order (multiple detectors can flag one claim).
    seen: set[str] = set()
    deduped: list[str] = []
    for v in violations:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def check(candidate: str, semantic_backstop: Optional[SemanticBackstop] = None) -> GuardrailResult:
    """Vet a candidate reply. Blocking takes precedence over redaction.

    `semantic_backstop`, if given, is consulted only after the lexical checks
    pass, to catch money/offer phrasings the patterns miss. It returns True to
    block. Kept optional so the default path is deterministic and offline.
    """
    violations: list[str] = []

    # --- 1. truthfulness / allowed-offers (block) ---
    violations.extend(_money_violations(candidate))

    # --- 3. tone (block) ---
    low = candidate.lower()
    for word in _ABUSIVE:
        if word in low:
            violations.append(f"tone:{word}")

    if violations:
        return GuardrailResult(action="block", text="", violations=violations)

    # --- 1b. semantic backstop (block) — only when lexical checks are clean ---
    if semantic_backstop is not None and semantic_backstop(candidate):
        return GuardrailResult(action="block", text="", violations=["semantic_backstop"])

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
