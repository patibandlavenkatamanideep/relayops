"""Calibration and route-safety evaluation for RelayOps v1.2.

Run:
    python3 -m src.eval.eval_calibration

This evaluates the production question that plain classifier accuracy misses:
after confidence calibration, does the router make safe decisions?
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..core.models import Action, Disposition, Intent, RouteDecision
from ..router.calibration import (
    CalibratedClassifier,
    PerClassConfidenceCalibrator,
    SafetyAwareCalibratedClassifier,
)
from ..router.classifier import IntentClassifier
from ..router.router import route
from ..router.trained_classifier import TrainedClassifier
from .agent_cases import CASES, run_case
from .dataset import Example, load_dataset, stratified_split_3way
from .metrics import accuracy, macro_f1, per_class_metrics

_ADVERSARIAL = Path(__file__).resolve().parent / "data" / "adversarial.jsonl"
_LABELS = [i.value for i in Intent]


@dataclass(frozen=True)
class RouteSafetyMetrics:
    total: int
    route_correct: int
    safe_routes: int
    over_escalations: int
    unsafe_auto_actions: int
    billing_escapes: int
    unsupported_escapes: int

    @property
    def route_correct_rate(self) -> float:
        return self.route_correct / self.total if self.total else 0.0

    @property
    def safe_route_rate(self) -> float:
        return self.safe_routes / self.total if self.total else 0.0

    @property
    def over_escalation_rate(self) -> float:
        return self.over_escalations / self.total if self.total else 0.0

    @property
    def unsafe_auto_action_rate(self) -> float:
        return self.unsafe_auto_actions / self.total if self.total else 0.0

    @property
    def billing_escape_rate(self) -> float:
        return self.billing_escapes / self.total if self.total else 0.0

    @property
    def unsupported_escape_rate(self) -> float:
        return self.unsupported_escapes / self.total if self.total else 0.0


def _is_expected_route(true_intent: Intent, decision: RouteDecision) -> bool:
    if true_intent == Intent.BILLING:
        return decision.disposition == Disposition.ESCALATE
    if true_intent == Intent.UNKNOWN:
        return decision.disposition == Disposition.ESCALATE
    if true_intent == Intent.RESET_DEVICE:
        return (
            decision.disposition == Disposition.RESPOND
            and decision.tool == Action.DEVICE_RESET
        )
    if true_intent == Intent.DEVICE_STATUS:
        return (
            decision.disposition == Disposition.RESPOND
            and decision.tool == Action.ACCOUNT_LOOKUP
        )
    if true_intent == Intent.DEVICE_FAQ:
        return decision.disposition == Disposition.RESPOND and decision.retrieve
    if true_intent == Intent.GREETING:
        return (
            decision.disposition == Disposition.RESPOND
            and decision.tool is None
            and not decision.retrieve
        )
    return False


def _is_safe_route(true_intent: Intent, decision: RouteDecision) -> bool:
    if true_intent in (Intent.BILLING, Intent.UNKNOWN):
        return decision.disposition == Disposition.ESCALATE
    if decision.disposition == Disposition.ESCALATE:
        return True
    if decision.tool == Action.DEVICE_RESET:
        return true_intent == Intent.RESET_DEVICE
    return True


def evaluate_route_safety(
    classifier: IntentClassifier,
    examples: Sequence[Example],
) -> RouteSafetyMetrics:
    route_correct = 0
    safe_routes = 0
    over_escalations = 0
    unsafe_auto_actions = 0
    billing_escapes = 0
    unsupported_escapes = 0

    for ex in examples:
        decision = route(classifier.classify(ex.text))
        if _is_expected_route(ex.intent, decision):
            route_correct += 1
        if _is_safe_route(ex.intent, decision):
            safe_routes += 1
        if (
            ex.intent in (Intent.RESET_DEVICE, Intent.DEVICE_STATUS, Intent.DEVICE_FAQ, Intent.GREETING)
            and decision.disposition == Disposition.ESCALATE
        ):
            over_escalations += 1
        if (
            ex.intent != Intent.RESET_DEVICE
            and decision.disposition == Disposition.RESPOND
            and decision.tool == Action.DEVICE_RESET
        ):
            unsafe_auto_actions += 1
        if ex.intent == Intent.BILLING and decision.disposition != Disposition.ESCALATE:
            billing_escapes += 1
        if ex.intent == Intent.UNKNOWN and decision.disposition != Disposition.ESCALATE:
            unsupported_escapes += 1

    return RouteSafetyMetrics(
        total=len(examples),
        route_correct=route_correct,
        safe_routes=safe_routes,
        over_escalations=over_escalations,
        unsafe_auto_actions=unsafe_auto_actions,
        billing_escapes=billing_escapes,
        unsupported_escapes=unsupported_escapes,
    )


def classify_examples(
    classifier: IntentClassifier,
    examples: Sequence[Example],
) -> tuple[list[str], list[str]]:
    y_true = [ex.intent.value for ex in examples]
    y_pred = [classifier.classify(ex.text).intent.value for ex in examples]
    return y_true, y_pred


def scope_violation_block_rate() -> tuple[int, int, float]:
    scope_cases = [case for case in CASES if case.expect_tool_error == "scope_violation"]
    blocked = 0
    for case in scope_cases:
        response = run_case(case)
        errors = [result.error for result in response.tool_results if not result.ok]
        if response.disposition == Disposition.ESCALATE and "scope_violation" in errors:
            blocked += 1
    total = len(scope_cases)
    return blocked, total, blocked / total if total else 0.0


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _print_classifier_line(name: str, classifier: IntentClassifier, examples: Sequence[Example]) -> None:
    y_true, y_pred = classify_examples(classifier, examples)
    print(f"{name:<18} acc {_fmt(accuracy(y_true, y_pred))}  macro-F1 {_fmt(macro_f1(y_true, y_pred, _LABELS))}")


def _print_safety_line(name: str, classifier: IntentClassifier, examples: Sequence[Example]) -> None:
    metrics = evaluate_route_safety(classifier, examples)
    print(
        f"{name:<18} safe_route {_fmt(metrics.safe_route_rate)}  "
        f"route_correct {_fmt(metrics.route_correct_rate)}  "
        f"over_escalation {_fmt(metrics.over_escalation_rate)}  "
        f"unsafe_auto_action {_fmt(metrics.unsafe_auto_action_rate)}  "
        f"billing_escape {_fmt(metrics.billing_escape_rate)}"
    )


def _print_adversarial_recall(classifier: IntentClassifier, examples: Sequence[Example]) -> None:
    y_true, y_pred = classify_examples(classifier, examples)
    print("\n----- adversarial per-class recall (safe calibrated NB) -----")
    for item in per_class_metrics(y_true, y_pred, _LABELS):
        print(f"{item.label:<14} recall {_fmt(item.recall)}  support {item.support}")


def build_calibrated_nb(
    train: Sequence[Example],
    val: Sequence[Example],
) -> tuple[TrainedClassifier, CalibratedClassifier, PerClassConfidenceCalibrator]:
    raw = TrainedClassifier().fit([(ex.text, ex.intent) for ex in train])
    calibrator = PerClassConfidenceCalibrator().fit(raw, val)
    calibrated = CalibratedClassifier(raw, calibrator)
    return raw, calibrated, calibrator


def split_adversarial(
    adversarial: Sequence[Example], dev_frac: float = 0.6, seed: int = 13
) -> tuple[list[Example], list[Example]]:
    """Deterministic, per-intent split of the adversarial set into a
    cue-development slice and a held-out slice. The held-out slice is the harness
    for reporting generalization once the deterministic cues are frozen against it
    (see README — the cues were historically authored with visibility of the full
    set, so the held-out number is the scaffold for an honest future benchmark)."""
    by_intent: dict[Intent, list[Example]] = defaultdict(list)
    for ex in adversarial:
        by_intent[ex.intent].append(ex)
    rng = random.Random(seed)
    dev: list[Example] = []
    held: list[Example] = []
    for items in by_intent.values():
        items = items[:]
        rng.shuffle(items)
        k = round(len(items) * dev_frac)
        dev.extend(items[:k])
        held.extend(items[k:])
    return dev, held


def main() -> None:
    data = load_dataset()
    train, val, test = stratified_split_3way(data, seed=13)
    raw, calibrated, calibrator = build_calibrated_nb(train, val)
    safe_calibrated = SafetyAwareCalibratedClassifier(raw, calibrator)
    adversarial = load_dataset(_ADVERSARIAL) if _ADVERSARIAL.exists() else []

    print(
        f"dataset: {len(data)} examples  |  train: {len(train)}  "
        f"calibration: {len(val)}  test: {len(test)}"
    )

    print("\n----- learned confidence map -----")
    print("predicted intent  support  precision  calibrated_confidence")
    for item in calibrator.diagnostics():
        print(
            f"{item.intent.value:<16} {item.support:>7}  "
            f"{item.precision:>9.3f}  {item.calibrated_confidence:>21.3f}"
        )

    print("\n----- held-out classifier quality -----")
    _print_classifier_line("raw NB", raw, test)
    _print_classifier_line("calibrated NB", calibrated, test)
    _print_classifier_line("safe calibrated NB", safe_calibrated, test)

    print("\n----- held-out route safety -----")
    _print_safety_line("raw NB", raw, test)
    _print_safety_line("calibrated NB", calibrated, test)
    _print_safety_line("safe calibrated NB", safe_calibrated, test)

    if adversarial:
        print(f"\n----- adversarial classifier quality ({len(adversarial)} cases) -----")
        _print_classifier_line("raw NB", raw, adversarial)
        _print_classifier_line("calibrated NB", calibrated, adversarial)
        _print_classifier_line("safe calibrated NB", safe_calibrated, adversarial)

        print("\n----- adversarial route safety -----")
        _print_safety_line("raw NB", raw, adversarial)
        _print_safety_line("calibrated NB", calibrated, adversarial)
        _print_safety_line("safe calibrated NB", safe_calibrated, adversarial)
        _print_adversarial_recall(safe_calibrated, adversarial)

        # Dev vs held-out: the full-set numbers above are in-distribution coverage
        # for the authored cue set. The held-out slice is the honest-generalization
        # harness (freeze cues against it going forward).
        dev_adv, held_adv = split_adversarial(adversarial)
        print(
            f"\n----- adversarial DEV ({len(dev_adv)}) vs HELD-OUT ({len(held_adv)}) "
            "route safety: safe calibrated NB -----"
        )
        _print_safety_line("dev", safe_calibrated, dev_adv)
        _print_safety_line("held-out", safe_calibrated, held_adv)
        print(
            "note: cues were historically authored with visibility of the full set, "
            "so held-out is in-distribution today; freeze cues to make it a true "
            "generalization benchmark."
        )

    blocked, total, rate = scope_violation_block_rate()
    print("\n----- end-to-end scope safety -----")
    print(f"scope_violation_block_rate: {_fmt(rate)} ({blocked}/{total})")


if __name__ == "__main__":
    main()
