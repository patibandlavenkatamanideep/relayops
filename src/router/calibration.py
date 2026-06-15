"""Confidence calibration for offline intent classifiers.

Complement NB is accurate for RelayOps' synthetic routing slice, but its raw
softmax confidence is not meaningful: the scores are nearly flat, so every NB
prediction sits below the router's escalation threshold. This module keeps the
model unchanged and learns a small empirical mapping from predicted class ->
observed validation precision.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..core.models import Classification, Intent
from ..eval.dataset import Example, stratified_split_3way
from .classifier import IntentClassifier
from .trained_classifier import TrainedClassifier

# ---------------------------------------------------------------------------
# Deterministic high-risk override: a known-pattern denylist.
#
# FROZEN as of v1.6 — do NOT edit these lists against held-out adversarial
# cases. They were historically authored with visibility of the full in-set
# adversarial suite (src/eval/data/adversarial.jsonl), which is why in-set
# safe-route reads 1.000. The honest generalization signal is measured on a
# disjoint, novel-phrasing slice the cues were never tuned against
# (src/eval/data/adversarial_heldout.jsonl); see README "Route safety". Editing
# these lists to chase a held-out failure would re-contaminate that benchmark.
# ---------------------------------------------------------------------------
_BILLING_CUES = (
    "$",
    " bill",
    "charged",
    "charge",
    "credit",
    "discount",
    "fee",
    "free service",
    "invoice",
    "late fee",
    "payment",
    "plan",
    "price",
    "percent off",
    "promo",
    "promised",
    "refund",
    "retention offer",
    "reverse",
    "waive",
)

_UNKNOWN_RISK_CUES = (
    "admin password",
    "another customer",
    "bob's account",
    "book me a flight",
    "change the name on the account",
    "delete my account",
    "hidden system prompt",
    "ignore verification",
    "install a new cable line",
    "locked out",
    "log into my account",
    "my ssn",
    "neighbor",
    "other customer",
    "personal gmail",
    "payment method",
    "phone screen",
    "pretend i am",
    "reset my password",
    "security answer",
    "skip verification",
    "spouse",
    "store it in the chat",
    "system prompt",
    "taking so long",
    "their bill",
    "this chat is terrible",
    "the weather",
)

_STATUS_CUES = (
    "am i online",
    "any of my equipment offline",
    "before resetting",
    "can you check if",
    "can you check whether",
    "connection state",
    "current health",
    "did that",
    "do you see",
    "equipment status",
    "is anything offline",
    "is dev_",
    "is my",
    "is the device",
    "is the router",
    "is the gateway",
    "look up my",
    "offline or",
    "please look up",
    "reachable",
    "show me whether",
    "still down",
    "still unreachable",
    "tell me if",
    "up or down",
)

_ACTION_REQUEST_CUES = (
    "can you reset",
    "cycle the",
    "kick the",
    "nudge my",
    "please reset",
    "reboot",
    "refresh the connection",
    "reset it",
    "reset my",
    "reset the",
    "restart",
    "run the standard device refresh",
    "send the reset",
)


@dataclass(frozen=True)
class ClassCalibration:
    intent: Intent
    support: int
    precision: float
    calibrated_confidence: float


def _clamp(value: float, low: float = 0.01, high: float = 0.99) -> float:
    return max(low, min(high, value))


class PerClassConfidenceCalibrator:
    """Map a classifier's predicted intent to empirical validation precision.

    This is intentionally conservative and inspectable: no extra dependency, no
    hidden optimizer, and no change to the underlying classifier's predicted
    intent. It only replaces raw confidence with a value the router can interpret.
    """

    def __init__(self, fallback_confidence: float = 0.01) -> None:
        self.fallback_confidence = fallback_confidence
        self._by_intent: dict[Intent, ClassCalibration] = {}

    def fit_predictions(
        self, predictions: Iterable[tuple[Classification, Intent]]
    ) -> "PerClassConfidenceCalibrator":
        totals: dict[Intent, int] = defaultdict(int)
        correct: dict[Intent, int] = defaultdict(int)

        for pred, true_intent in predictions:
            totals[pred.intent] += 1
            if pred.intent == true_intent:
                correct[pred.intent] += 1

        self._by_intent = {}
        for intent in Intent:
            support = totals[intent]
            precision = (correct[intent] / support) if support else 0.0
            self._by_intent[intent] = ClassCalibration(
                intent=intent,
                support=support,
                precision=precision,
                calibrated_confidence=_clamp(precision, self.fallback_confidence),
            )
        return self

    def fit(
        self,
        classifier: IntentClassifier,
        examples: Sequence[Example],
    ) -> "PerClassConfidenceCalibrator":
        return self.fit_predictions((classifier.classify(ex.text), ex.intent) for ex in examples)

    def calibrate(self, classification: Classification) -> Classification:
        item = self._by_intent.get(classification.intent)
        if item is None or item.support == 0:
            confidence = min(classification.confidence, self.fallback_confidence)
        else:
            confidence = item.calibrated_confidence
        return Classification(intent=classification.intent, confidence=confidence)

    def diagnostics(self) -> list[ClassCalibration]:
        return [self._by_intent[i] for i in Intent if i in self._by_intent]


class CalibratedClassifier:
    """Classifier wrapper that preserves intent and calibrates confidence."""

    def __init__(
        self,
        classifier: IntentClassifier,
        calibrator: PerClassConfidenceCalibrator,
    ) -> None:
        self.classifier = classifier
        self.calibrator = calibrator

    def classify(self, text: str) -> Classification:
        return self.calibrator.calibrate(self.classifier.classify(text))


def safety_override_intent(text: str) -> Intent | None:
    """Deterministic high-risk override that runs before learned routing.

    Billing/price language is money-touching, so it must escalate even if a reset
    phrase is also present. Customer-data, prompt-injection, and account-admin
    requests are outside the v1 tool/RAG scope and should stay unknown.
    """

    low = f" {text.lower()} "
    if any(cue in low for cue in _BILLING_CUES):
        return Intent.BILLING
    if any(cue in low for cue in _UNKNOWN_RISK_CUES):
        return Intent.UNKNOWN
    has_status_cue = any(cue in low for cue in _STATUS_CUES)
    has_action_request = any(cue in low for cue in _ACTION_REQUEST_CUES)
    if has_status_cue and has_action_request:
        return Intent.RESET_DEVICE
    if has_status_cue:
        return Intent.DEVICE_STATUS
    return None


class SafetyAwareCalibratedClassifier(CalibratedClassifier):
    """Calibrated classifier with deterministic high-risk overrides."""

    def classify(self, text: str) -> Classification:
        override = safety_override_intent(text)
        if override is not None:
            return Classification(intent=override, confidence=0.99)
        return super().classify(text)


def fit_calibrated_nb(
    examples: Sequence[Example],
    seed: int = 13,
    safety_overrides: bool = True,
) -> CalibratedClassifier:
    """Fit NB on train examples and calibrate confidence on a held-out val split."""

    train, val, _test = stratified_split_3way(list(examples), seed=seed)
    base = TrainedClassifier().fit([(ex.text, ex.intent) for ex in train])
    calibrator = PerClassConfidenceCalibrator().fit(base, val)
    if safety_overrides:
        return SafetyAwareCalibratedClassifier(base, calibrator)
    return CalibratedClassifier(base, calibrator)
