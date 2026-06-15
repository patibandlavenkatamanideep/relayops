"""v1.2 calibration and route-safety tests."""

from __future__ import annotations

import unittest

from src.core.models import Action, Classification, Intent
from src.eval.dataset import Example, load_dataset, stratified_split_3way
from src.eval.eval_calibration import (
    _ADVERSARIAL,
    _HELDOUT,
    build_calibrated_nb,
    evaluate_route_safety,
)
from src.router.calibration import fit_calibrated_nb
from src.router.router import CONFIDENCE_THRESHOLD, route


class CalibrationTests(unittest.TestCase):
    def test_calibrated_nb_promotes_supported_route_confidence(self):
        train, val, _test = stratified_split_3way(load_dataset(), seed=13)
        raw, calibrated, _calibrator = build_calibrated_nb(train, val)

        raw_prediction = raw.classify("my internet is down and the router needs a bounce")
        calibrated_prediction = calibrated.classify(
            "my internet is down and the router needs a bounce"
        )

        self.assertEqual(raw_prediction.intent, calibrated_prediction.intent)
        self.assertLess(raw_prediction.confidence, CONFIDENCE_THRESHOLD)
        self.assertGreaterEqual(calibrated_prediction.confidence, CONFIDENCE_THRESHOLD)

    def test_calibration_improves_route_correctness_on_heldout_set(self):
        train, val, test = stratified_split_3way(load_dataset(), seed=13)
        raw, calibrated, _calibrator = build_calibrated_nb(train, val)

        raw_metrics = evaluate_route_safety(raw, test)
        calibrated_metrics = evaluate_route_safety(calibrated, test)

        self.assertGreater(
            calibrated_metrics.route_correct_rate,
            raw_metrics.route_correct_rate,
        )
        self.assertLess(
            calibrated_metrics.over_escalation_rate,
            raw_metrics.over_escalation_rate,
        )

    def test_billing_still_escalates_after_calibration(self):
        train, val, _test = stratified_split_3way(load_dataset(), seed=13)
        _raw, calibrated, _calibrator = build_calibrated_nb(train, val)

        decision = route(calibrated.classify("I want a refund on my last bill"))

        self.assertEqual(decision.reason, "billing/plan/payment")

    def test_safety_aware_calibration_overrides_mixed_billing_reset(self):
        classifier = fit_calibrated_nb(load_dataset())

        prediction = classifier.classify("reset my router and waive the activation fee")
        decision = route(prediction)

        self.assertEqual(prediction.intent, Intent.BILLING)
        self.assertEqual(decision.reason, "billing/plan/payment")

    def test_status_override_does_not_hide_explicit_reset_request(self):
        classifier = fit_calibrated_nb(load_dataset())

        prediction = classifier.classify("is my router down, can you reset it")

        self.assertEqual(prediction.intent, Intent.RESET_DEVICE)


class HeldOutGeneralizationTests(unittest.TestCase):
    """Guards the integrity of the frozen-denylist generalization benchmark."""

    def test_heldout_set_exists_and_covers_all_intents(self):
        heldout = load_dataset(_HELDOUT)
        self.assertGreaterEqual(len(heldout), 30)
        self.assertEqual({ex.intent for ex in heldout}, set(Intent))

    def test_heldout_is_disjoint_from_in_set_adversarial(self):
        in_set = {ex.text.strip().lower() for ex in load_dataset(_ADVERSARIAL)}
        heldout = {ex.text.strip().lower() for ex in load_dataset(_HELDOUT)}
        # The cues are frozen against the in-set suite; the held-out slice must
        # share no cases with it or it stops being a generalization benchmark.
        self.assertEqual(in_set & heldout, set())

    def test_route_safety_computes_on_heldout(self):
        classifier = fit_calibrated_nb(load_dataset())
        metrics = evaluate_route_safety(classifier, load_dataset(_HELDOUT))
        self.assertEqual(metrics.total, len(load_dataset(_HELDOUT)))


class RouteSafetyMetricTests(unittest.TestCase):
    def test_unsafe_auto_action_is_counted(self):
        class ResetHappyClassifier:
            def classify(self, _text: str) -> Classification:
                return Classification(intent=Intent.RESET_DEVICE, confidence=0.99)

        examples = [
            Example("give me a refund", Intent.BILLING),
            Example("can you reset my router", Intent.RESET_DEVICE),
        ]

        metrics = evaluate_route_safety(ResetHappyClassifier(), examples)

        self.assertEqual(metrics.total, 2)
        self.assertEqual(metrics.unsafe_auto_actions, 1)
        self.assertEqual(metrics.billing_escapes, 1)

    def test_expected_reset_route_is_correct_and_safe(self):
        class ResetClassifier:
            def classify(self, _text: str) -> Classification:
                return Classification(intent=Intent.RESET_DEVICE, confidence=0.99)

        metrics = evaluate_route_safety(
            ResetClassifier(),
            [Example("reset my router", Intent.RESET_DEVICE)],
        )

        self.assertEqual(metrics.route_correct, 1)
        self.assertEqual(metrics.safe_routes, 1)
        self.assertEqual(route(ResetClassifier().classify("reset")).tool, Action.DEVICE_RESET)


if __name__ == "__main__":
    unittest.main(verbosity=2)
