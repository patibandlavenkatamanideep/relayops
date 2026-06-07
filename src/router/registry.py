"""Classifier registry — switch the Tier-1 intent classifier by name.

Makes the README claim literal: the pipeline can run any of the classifiers
behind the one ``IntentClassifier`` interface. ``handle_turn(..., classifier=...)``
accepts whatever this returns.

  keyword   -> rule baseline (no training, always available)
  nb        -> Complement Naive Bayes, fit on the labeled dataset (offline)
  nb_calibrated -> NB with validation-set confidence calibration for routing
  prompted  -> Claude Haiku few-shot       (needs ANTHROPIC_API_KEY)
  finetuned -> fine-tuned small LM          (needs transformers + a model)
"""

from __future__ import annotations

from .classifier import BaselineClassifier, IntentClassifier


def get_classifier(name: str = "keyword") -> IntentClassifier:
    name = name.lower()
    if name == "keyword":
        return BaselineClassifier()

    if name in ("nb", "trained"):
        from ..eval.dataset import load_dataset
        from .trained_classifier import TrainedClassifier

        pairs = [(e.text, e.intent) for e in load_dataset()]
        return TrainedClassifier().fit(pairs)

    if name in ("nb_calibrated", "calibrated", "trained_calibrated"):
        from ..eval.dataset import load_dataset
        from .calibration import fit_calibrated_nb

        return fit_calibrated_nb(load_dataset())

    if name == "prompted":
        from ..eval.dataset import load_dataset
        from .prompted_classifier import PromptedClassifier

        pairs = [(e.text, e.intent) for e in load_dataset()]
        return PromptedClassifier().fit(pairs)

    if name == "finetuned":
        from .finetuned_classifier import FineTunedIntentClassifier

        return FineTunedIntentClassifier()

    raise ValueError(f"unknown classifier: {name!r}")
