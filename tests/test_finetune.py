"""Tests for the fine-tuning path — parsing, data export, 3-way split, registry.

The model itself needs a GPU + transformers (not in the offline slice), so these
cover the parts that run everywhere: the strict-JSON parser, the chat-format
exporter, the stratified 3-way split, and the classifier registry.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.core.models import Intent
from src.eval.dataset import load_dataset, stratified_split_3way
from src.eval.export_finetune_data import export
from src.router.classifier import BaselineClassifier
from src.router.finetuned_classifier import (
    SYSTEM_PROMPT,
    _materialize_model_path,
    _model_input_device,
    parse_intent,
)
from src.router.registry import get_classifier


class ParseIntentTests(unittest.TestCase):
    def test_strict_json(self):
        c = parse_intent('{"intent": "reset_device"}')
        self.assertEqual(c.intent, Intent.RESET_DEVICE)

    def test_confidence_passthrough(self):
        c = parse_intent('{"intent": "billing"}', confidence=0.77)
        self.assertEqual(c.intent, Intent.BILLING)
        self.assertEqual(c.confidence, 0.77)

    def test_noisy_wrapping_text(self):
        c = parse_intent('Sure! {"intent": "greeting"} hope that helps')
        self.assertEqual(c.intent, Intent.GREETING)

    def test_substring_fallback(self):
        c = parse_intent("intent: device_faq")
        self.assertEqual(c.intent, Intent.DEVICE_FAQ)

    def test_garbage_is_unknown(self):
        c = parse_intent("no idea")
        self.assertEqual(c.intent, Intent.UNKNOWN)


class SplitTests(unittest.TestCase):
    def test_3way_disjoint_and_covers_classes(self):
        train, val, test = stratified_split_3way(load_dataset(), seed=13)
        for part in (train, val, test):
            self.assertEqual({e.intent for e in part}, set(Intent))
        s_train = {(e.text, e.intent) for e in train}
        s_val = {(e.text, e.intent) for e in val}
        s_test = {(e.text, e.intent) for e in test}
        self.assertTrue(s_train.isdisjoint(s_val))
        self.assertTrue(s_train.isdisjoint(s_test))
        self.assertTrue(s_val.isdisjoint(s_test))
        g_train = {(e.intent, e.group) for e in train}
        g_val = {(e.intent, e.group) for e in val}
        g_test = {(e.intent, e.group) for e in test}
        self.assertTrue(g_train.isdisjoint(g_val))
        self.assertTrue(g_train.isdisjoint(g_test))
        self.assertTrue(g_val.isdisjoint(g_test))


class ExportTests(unittest.TestCase):
    def test_export_writes_valid_chat_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            counts = export(out_dir=Path(d), seed=13)
            self.assertEqual(set(counts), {"train", "val", "test"})
            for name in counts:
                lines = (Path(d) / f"{name}.jsonl").read_text().strip().splitlines()
                self.assertEqual(len(lines), counts[name])
                rec = json.loads(lines[0])
                roles = [m["role"] for m in rec["messages"]]
                self.assertEqual(roles, ["system", "user", "assistant"])
                self.assertEqual(rec["messages"][0]["content"], SYSTEM_PROMPT)
                # assistant target is strict JSON, intent only (no risk/route)
                target = json.loads(rec["messages"][2]["content"])
                self.assertEqual(set(target), {"intent"})
                self.assertIn(target["intent"], [i.value for i in Intent])


class LoaderHelperTests(unittest.TestCase):
    def test_materialize_zip_adapter(self):
        tempdirs = []
        try:
            with tempfile.TemporaryDirectory() as d:
                zip_path = Path(d) / "intent-lora.zip"
                with zipfile.ZipFile(zip_path, "w") as zf:
                    zf.writestr("adapter_config.json", "{}")
                    zf.writestr("tokenizer_config.json", "{}")

                resolved = Path(_materialize_model_path(str(zip_path), tempdirs))
                self.assertTrue((resolved / "adapter_config.json").exists())
                self.assertTrue((resolved / "tokenizer_config.json").exists())
        finally:
            for td in tempdirs:
                td.cleanup()

    def test_model_input_device_falls_back_to_parameters(self):
        class Param:
            device = "cpu"

        class WrappedModel:
            def parameters(self):
                yield Param()

        self.assertEqual(_model_input_device(WrappedModel()), "cpu")


class RegistryTests(unittest.TestCase):
    def test_keyword(self):
        self.assertIsInstance(get_classifier("keyword"), BaselineClassifier)

    def test_nb_is_fitted_and_classifies(self):
        clf = get_classifier("nb")
        self.assertEqual(clf.classify("my internet is down").intent, Intent.RESET_DEVICE)

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            get_classifier("does-not-exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
