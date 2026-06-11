"""Intent classifier — Tier 1.

v1 step 1 ships a transparent keyword baseline. This is deliberately the
*prompted/rule baseline to beat*: step 4 replaces it with a fine-tuned small
model and reports the accuracy lift + confusion matrix against the very same
``IntentClassifier`` interface and golden set.
"""

from __future__ import annotations

from typing import Protocol

from ..core.models import Classification, Intent

# intent -> keyword cues. Order matters: more specific intents are checked first.
# DEVICE_FAQ uses question phrases so informational queries ("how long does a
# reset take?") route to RAG instead of triggering the reset action.
_RULES: list[tuple[Intent, tuple[str, ...]]] = [
    (
        Intent.DEVICE_FAQ,
        (
            "how do",
            "how long",
            "how can",
            "how often",
            "why is",
            "why does",
            "why do",
            "what does",
            "what is",
            "what should",
            "steps to",
            "guide",
        ),
    ),
    (Intent.RESET_DEVICE, ("reset", "reboot", "restart", "power cycle", "not working", "offline")),
    (Intent.DEVICE_STATUS, ("status", "is my", "online", "connected", "working?")),
    (
        Intent.BILLING,
        ("bill", "charge", "invoice", "refund", "payment", "plan", "upgrade", "price"),
    ),
    (Intent.GREETING, ("hi", "hello", "hey", "good morning", "good evening")),
]


class IntentClassifier(Protocol):
    def classify(self, text: str) -> Classification: ...


class BaselineClassifier:
    """Keyword/rule classifier. Confidence reflects cue strength, not calibration."""

    def classify(self, text: str) -> Classification:
        t = text.lower()
        for intent, cues in _RULES:
            hits = sum(1 for c in cues if c in t)
            if hits:
                # crude confidence: saturates with more cue hits
                confidence = min(0.6 + 0.15 * hits, 0.95)
                return Classification(intent=intent, confidence=confidence)
        return Classification(intent=Intent.UNKNOWN, confidence=0.3)
