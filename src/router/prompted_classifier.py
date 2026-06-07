"""Prompted few-shot intent classifier — the baseline the trained model must beat.

This is the "prompted (few-shot) classifier" from DESIGN.md §5: a Tier-1 cheap
model (Claude Haiku) given a handful of labeled examples and asked to label the
message. Establishing this baseline first is what turns "I fine-tuned a model"
into "I improved routing accuracy from X to Y."

Requires the ``anthropic`` package and ``ANTHROPIC_API_KEY``; the eval harness
skips it gracefully when unavailable so the rest of the slice runs offline.
"""

from __future__ import annotations

from typing import Sequence

from ..core.models import Classification, Intent

# Tier-1 cheap, fast model — the right tier for a classification call.
_MODEL = "claude-haiku-4-5"
_LABELS = [i.value for i in Intent]


class PromptedClassifier:
    """Few-shot classifier over the Anthropic Messages API."""

    def __init__(self, max_shots_per_class: int = 3) -> None:
        import anthropic  # raises offline / without the package

        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self._max_shots = max_shots_per_class
        self._shots: list[tuple[str, Intent]] = []

    def fit(self, examples: Sequence[tuple[str, Intent]]) -> "PromptedClassifier":
        """'Training' a prompted classifier = selecting few-shot exemplars."""
        per: dict[Intent, int] = {}
        shots: list[tuple[str, Intent]] = []
        for text, intent in examples:
            if per.get(intent, 0) < self._max_shots:
                shots.append((text, intent))
                per[intent] = per.get(intent, 0) + 1
        self._shots = shots
        return self

    def _system(self) -> str:
        examples = "\n".join(f'- "{t}" -> {i.value}' for t, i in self._shots)
        return (
            "You are an intent classifier for a telecom support agent. "
            "Classify the user's message into exactly one of these intents:\n"
            f"{', '.join(_LABELS)}\n\n"
            "Examples:\n"
            f"{examples}\n\n"
            "Respond with ONLY the intent label, nothing else."
        )

    def classify(self, text: str) -> Classification:
        resp = self._client.messages.create(
            model=_MODEL,
            max_tokens=16,
            system=self._system(),
            messages=[{"role": "user", "content": text}],
        )
        raw = next((b.text for b in resp.content if b.type == "text"), "").strip().lower()
        # tolerate minor formatting noise from the model
        for label in _LABELS:
            if label in raw:
                return Classification(intent=Intent(label), confidence=0.9)
        return Classification(intent=Intent.UNKNOWN, confidence=0.3)
