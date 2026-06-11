"""Dataset, metrics, and trained-classifier-beats-baseline tests."""

from __future__ import annotations

import unittest

from src.core.models import Classification, Intent
from src.eval import metrics
from src.eval.dataset import load_dataset, stratified_split
from src.router.classifier import BaselineClassifier
from src.router.trained_classifier import TrainedClassifier


class DatasetTests(unittest.TestCase):
    def test_loads_all_intents(self):
        data = load_dataset()
        self.assertGreater(len(data), 50)
        intents = {ex.intent for ex in data}
        self.assertEqual(intents, set(Intent))

    def test_stratified_split_covers_every_class_in_both(self):
        train, test = stratified_split(load_dataset(), test_frac=0.3, seed=13)
        self.assertEqual({e.intent for e in train}, set(Intent))
        self.assertEqual({e.intent for e in test}, set(Intent))
        # no leakage: disjoint
        self.assertTrue(
            set((e.text, e.intent) for e in train).isdisjoint(set((e.text, e.intent) for e in test))
        )

    def test_stratified_split_keeps_groups_disjoint(self):
        train, test = stratified_split(load_dataset(), test_frac=0.3, seed=13)
        train_groups = {(e.intent, e.group) for e in train}
        test_groups = {(e.intent, e.group) for e in test}
        self.assertTrue(train_groups.isdisjoint(test_groups))


class MetricsTests(unittest.TestCase):
    def test_accuracy(self):
        self.assertAlmostEqual(metrics.accuracy(["a", "b", "c"], ["a", "x", "c"]), 2 / 3)

    def test_confusion_matrix_counts(self):
        cm = metrics.confusion_matrix(["a", "a", "b"], ["a", "b", "b"], ["a", "b"])
        self.assertEqual(cm["a"]["a"], 1)
        self.assertEqual(cm["a"]["b"], 1)
        self.assertEqual(cm["b"]["b"], 1)

    def test_per_class_precision_recall(self):
        m = {
            c.label: c
            for c in metrics.per_class_metrics(["a", "a", "b"], ["a", "b", "b"], ["a", "b"])
        }
        self.assertEqual(m["a"].recall, 0.5)  # 1 of 2 true-a predicted a
        self.assertEqual(m["a"].precision, 1.0)  # the one a-pred was correct


class TrainedClassifierTests(unittest.TestCase):
    def test_returns_classification(self):
        clf = TrainedClassifier().fit(
            [("reset my router", Intent.RESET_DEVICE), ("hi there", Intent.GREETING)]
        )
        out = clf.classify("reset my router")
        self.assertIsInstance(out, Classification)
        self.assertEqual(out.intent, Intent.RESET_DEVICE)

    def test_classify_before_fit_raises(self):
        with self.assertRaises(RuntimeError):
            TrainedClassifier().classify("hi")

    def test_generalizes_beyond_keywords(self):
        # phrasing with no keyword cue; learned model should still get reset_device
        train, _ = stratified_split(load_dataset(), test_frac=0.3, seed=13)
        clf = TrainedClassifier().fit([(e.text, e.intent) for e in train])
        self.assertEqual(clf.classify("my internet is dead").intent, Intent.RESET_DEVICE)

    def test_beats_keyword_baseline_cross_validated(self):
        # Average over seeds so the comparison isn't a single-split fluke.
        data = load_dataset()
        keyword = BaselineClassifier()
        kw_accs, tr_accs = [], []
        for seed in (13, 29, 47, 71, 101):
            train, test = stratified_split(data, test_frac=0.3, seed=seed)
            trained = TrainedClassifier().fit([(e.text, e.intent) for e in train])
            y_true = [e.intent.value for e in test]
            keyword_preds = [keyword.classify(e.text).intent.value for e in test]
            trained_preds = [trained.classify(e.text).intent.value for e in test]
            kw_accs.append(metrics.accuracy(y_true, keyword_preds))
            tr_accs.append(metrics.accuracy(y_true, trained_preds))
        mean_kw = sum(kw_accs) / len(kw_accs)
        mean_tr = sum(tr_accs) / len(tr_accs)
        # trained should beat the keyword baseline by a clear margin
        self.assertGreater(mean_tr, mean_kw + 0.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)
