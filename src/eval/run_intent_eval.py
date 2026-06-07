"""Intent-classifier evaluation — baseline vs trained, with a confusion matrix.

Run:  python3 -m src.eval.run_intent_eval

Splits the labeled dataset (stratified, seeded), evaluates each classifier on the
held-out test set, and prints accuracy + a confusion matrix. This is the artifact
behind the portfolio claim "I improved routing accuracy from X to Y."

  * keyword  — the rule baseline (BaselineClassifier)
  * trained  — Naive Bayes fit on the train split (offline stand-in for the
               fine-tuned model)
  * prompted — Claude Haiku few-shot, only if ANTHROPIC_API_KEY is set
"""

from __future__ import annotations

import os

from pathlib import Path

from ..core.models import Intent
from ..router.classifier import BaselineClassifier
from ..router.trained_classifier import TrainedClassifier
from .dataset import Example, load_dataset, stratified_split
from .metrics import accuracy, classification_report, confusion_matrix, format_confusion_matrix

_LABELS = [i.value for i in Intent]
_ADVERSARIAL = Path(__file__).resolve().parent / "data" / "adversarial.jsonl"


def _evaluate(clf, test: list[Example]) -> tuple[list[str], list[str]]:
    y_true = [ex.intent.value for ex in test]
    y_pred = [clf.classify(ex.text).intent.value for ex in test]
    return y_true, y_pred


def _report(name: str, y_true: list[str], y_pred: list[str]) -> float:
    acc = accuracy(y_true, y_pred)
    print(f"\n===== {name} =====")
    print(classification_report(y_true, y_pred, _LABELS))
    print("\nconfusion matrix (rows=true):")
    print(format_confusion_matrix(confusion_matrix(y_true, y_pred, _LABELS), _LABELS))
    return acc


_SEEDS = [13, 29, 47, 71, 101]


def _cv_accuracy(data, build_clf) -> float:
    """Mean held-out accuracy across seeds — stabilises the small-data variance."""
    accs = []
    for seed in _SEEDS:
        train, test = stratified_split(data, test_frac=0.3, seed=seed)
        clf = build_clf([(e.text, e.intent) for e in train])
        accs.append(accuracy(*_evaluate(clf, test)))
    return sum(accs) / len(accs)


def main() -> None:
    data = load_dataset()
    train, test = stratified_split(data, test_frac=0.3, seed=13)
    print(f"dataset: {len(data)} examples  |  train: {len(train)}  test: {len(test)}")

    train_pairs = [(ex.text, ex.intent) for ex in train]

    keyword = BaselineClassifier()
    trained = TrainedClassifier().fit(train_pairs)

    acc_keyword = _report("keyword baseline", *_evaluate(keyword, test))
    acc_trained = _report("trained (Complement NB)", *_evaluate(trained, test))

    # Stable headline: 5-seed cross-validation.
    cv_keyword = _cv_accuracy(data, lambda _: BaselineClassifier())
    cv_trained = _cv_accuracy(data, lambda pairs: TrainedClassifier().fit(pairs))

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from ..router.prompted_classifier import PromptedClassifier

            prompted = PromptedClassifier().fit(train_pairs)
            acc_prompted = _report("prompted few-shot (Haiku)", *_evaluate(prompted, test))
            print(f"\nprompted few-shot accuracy: {acc_prompted:.3f}")
        except Exception as e:  # network / SDK issues shouldn't break the run
            print(f"\n[prompted classifier skipped: {type(e).__name__}: {e}]")
    else:
        print("\n[prompted few-shot (Haiku) skipped — set ANTHROPIC_API_KEY to include it]")

    print("\n----- summary (single seed=13 held-out) -----")
    print(f"keyword baseline accuracy : {acc_keyword:.3f}")
    print(f"trained model    accuracy : {acc_trained:.3f}")

    print(f"\n----- summary ({len(_SEEDS)}-seed cross-validation) -----")
    print(f"keyword baseline accuracy : {cv_keyword:.3f}")
    print(f"trained model    accuracy : {cv_trained:.3f}")
    delta = (cv_trained - cv_keyword) * 100
    print(f"improvement              : {delta:+.1f} points")

    # Adversarial / paraphrase set: trained on the FULL dataset, tested on held-out
    # hard cases (slang, mixed-intent, out-of-taxonomy) that expose brittleness.
    if _ADVERSARIAL.exists():
        adv = load_dataset(_ADVERSARIAL)
        full = TrainedClassifier().fit([(e.text, e.intent) for e in data])
        kw = BaselineClassifier()
        print("\n----- adversarial / paraphrase set -----")
        print(f"keyword baseline accuracy : {accuracy(*_evaluate(kw, adv)):.3f}")
        print(f"trained model    accuracy : {accuracy(*_evaluate(full, adv)):.3f}")


if __name__ == "__main__":
    main()
