"""Trained intent classifier — a learned model that beats the keyword baseline.

This is a **Complement Naive Bayes** (Rennie et al., 2003) over word + phrase
features, fit on the labeled dataset (``fit``). CNB is a well-known, strong text classifier for
small / class-imbalanced data — it weights each class against the *complement* of
its examples and normalises the weights, which fixes the bias plain Multinomial
NB shows on short, skewed corpora. It is a *real* trained model: it learns word
-> intent associations and generalises to paraphrases the keyword rules miss
(e.g. "my internet is down" -> reset_device).

In production this slot is a fine-tuned small LM (or an embedding + linear head);
the methodology demonstrated here is identical and is what matters for the
portfolio claim: train -> held-out eval -> confusion matrix -> beat the baseline.
We keep CNB because it runs offline with zero dependencies. The
``IntentClassifier`` interface is unchanged, so the router/pipeline accept it as a
drop-in.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Sequence

from ..core.models import Classification, Intent

_ALPHA = 1.0  # Laplace smoothing

_TOKEN = re.compile(r"\b\w+\b")


def _tokenize(text: str) -> list[str]:
    words = _TOKEN.findall(text.lower())
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    trigrams = [f"{a}_{b}_{c}" for a, b, c in zip(words, words[1:], words[2:])]
    return words + bigrams + trigrams


class TrainedClassifier:
    """Weight-normalised Complement Naive Bayes. Call ``fit`` before ``classify``.

    For each class c, weights are learned from the *complement* of c (every other
    class's tokens); a token strongly associated with the complement weighs
    against predicting c. Weights are L1-normalised per class so long-document or
    frequent-class bias doesn't dominate. Prediction picks the class with the
    lowest complement score.
    """

    def __init__(self) -> None:
        self._classes: list[Intent] = []
        self._weights: dict[Intent, dict[str, float]] = {}
        self._unk_weight: dict[Intent, float] = {}
        self._fitted = False

    def fit(self, examples: Sequence[tuple[str, Intent]]) -> "TrainedClassifier":
        class_tokens: dict[Intent, Counter[str]] = defaultdict(Counter)
        vocab: set[str] = set()
        for text, intent in examples:
            toks = _tokenize(text)
            class_tokens[intent].update(toks)
            vocab.update(toks)

        self._classes = sorted(class_tokens, key=lambda c: c.value)
        v = len(vocab)
        global_tokens: Counter[str] = Counter()
        for c in self._classes:
            global_tokens.update(class_tokens[c])
        global_total = sum(global_tokens.values())

        for c in self._classes:
            comp_total = global_total - sum(class_tokens[c].values())
            denom = comp_total + _ALPHA * v
            raw = {
                w: math.log((global_tokens[w] - class_tokens[c][w] + _ALPHA) / denom)
                for w in vocab
            }
            norm = sum(abs(x) for x in raw.values()) or 1.0
            self._weights[c] = {w: x / norm for w, x in raw.items()}
            self._unk_weight[c] = math.log(_ALPHA / denom) / norm

        self._fitted = True
        return self

    def classify(self, text: str) -> Classification:
        if not self._fitted:
            raise RuntimeError("TrainedClassifier.classify called before fit()")

        tf = Counter(_tokenize(text))
        # complement score: lower = better fit for the class
        scores: dict[Intent, float] = {}
        for c in self._classes:
            w = self._weights[c]
            unk = self._unk_weight[c]
            scores[c] = sum(count * w.get(tok, unk) for tok, count in tf.items())

        best = min(scores, key=scores.get)
        # softmax over negated scores (lower complement -> higher confidence)
        neg = {c: -s for c, s in scores.items()}
        m = max(neg.values())
        denom = sum(math.exp(x - m) for x in neg.values()) or 1.0
        confidence = math.exp(neg[best] - m) / denom
        return Classification(intent=best, confidence=confidence)
