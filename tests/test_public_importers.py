"""Tests for v1.6 public-dataset importers + external-data validation.

Uses tiny fixtures that mimic each dataset's real column layout. Verifies the
canonical schema, unmapped handling, source filtering, and that imported tickets
run through the batch runner under a sandbox auth identity while keeping the
safety counters at zero.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import tempfile

from src.workflows import import_dataset
from src.workflows.importers import get_importer
from src.workflows.importers import hf_support, kaggle_support, twitter_support
from src.workflows.normalize_ticket import PUBLIC_CUSTOMER_ID, normalize
from src.workflows.ticket_runner import render_markdown_report, run_batch

FIX = Path(__file__).parent / "fixtures"
CANONICAL_KEYS = {"ticket_id", "customer_id", "message", "source", "metadata"}


class NormalizeTests(unittest.TestCase):
    def test_empty_message_is_unmapped(self):
        self.assertIsNone(normalize(ticket_id="x", message="   ", source="s"))

    def test_canonical_shape(self):
        t = normalize(
            ticket_id=7,
            message="reset my router",
            source="kaggle",
            category="Technical",
            priority="High",
            status="Open",
            original_fields={"raw": 1},
        )
        self.assertEqual(set(t.keys()), CANONICAL_KEYS)
        self.assertEqual(t["ticket_id"], "7")
        self.assertEqual(t["customer_id"], PUBLIC_CUSTOMER_ID)
        self.assertEqual(t["metadata"]["category"], "Technical")
        self.assertEqual(t["metadata"]["original_fields"], {"raw": 1})


class KaggleImporterTests(unittest.TestCase):
    def test_load_maps_and_counts_unmapped(self):
        tickets, unmapped = kaggle_support.load(FIX / "kaggle_sample.csv")
        self.assertEqual(unmapped, 1)  # the empty subject+description row
        self.assertEqual(len(tickets), 5)
        first = tickets[0]
        self.assertEqual(set(first.keys()), CANONICAL_KEYS)
        self.assertEqual(first["source"], "kaggle_customer_support")
        self.assertIn("reset", first["message"].lower())
        self.assertEqual(first["metadata"]["priority"], "High")

    def test_limit(self):
        tickets, _ = kaggle_support.load(FIX / "kaggle_sample.csv", limit=2)
        self.assertEqual(len(tickets), 2)


class HFImporterTests(unittest.TestCase):
    def test_load_maps_subject_body_and_category(self):
        tickets, unmapped = hf_support.load(FIX / "hf_sample.jsonl")
        self.assertEqual(unmapped, 1)
        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[0]["metadata"]["category"], "Technical")
        self.assertIn("modem", tickets[0]["message"].lower())


class TwitterImporterTests(unittest.TestCase):
    def test_only_inbound_customer_tweets_become_tickets(self):
        tickets, unmapped = twitter_support.load(FIX / "twitter_sample.csv")
        # 4 inbound rows, 1 of them empty (unmapped), 1 outbound dropped
        self.assertEqual(unmapped, 1)
        self.assertEqual(len(tickets), 3)
        texts = " ".join(t["message"].lower() for t in tickets)
        self.assertNotIn("we are sorry", texts)  # outbound brand reply excluded


class RegistryTests(unittest.TestCase):
    def test_dispatch(self):
        self.assertIs(get_importer("kaggle"), kaggle_support.load)
        self.assertIs(get_importer("hf"), hf_support.load)
        self.assertIs(get_importer("twitter"), twitter_support.load)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            get_importer("nope")


class PublicDataRunTests(unittest.TestCase):
    def test_imported_tickets_run_safely_under_sandbox_auth(self):
        tickets, _ = kaggle_support.load(FIX / "kaggle_sample.csv")
        result = run_batch(
            tickets,
            classifier_name="keyword",  # fast + offline for tests
            assume_customer="cust_alice",
            source="kaggle_test",
        )
        self.assertEqual(result.total, len(tickets))
        self.assertEqual(len(result.records), len(tickets))
        # sandbox auth means the reset/status/faq tickets are actionable
        self.assertGreater(result.auto_resolved, 0)
        # load-bearing safety properties hold on external data too
        self.assertEqual(result.unsafe_auto_action, 0)
        self.assertEqual(result.billing_escape, 0)
        s = result.summary()
        self.assertEqual(s["source"], "kaggle_test")
        self.assertIn("unsupported_rate", s)
        self.assertIsInstance(s["top_failure_categories"], list)

    def test_without_assume_customer_everything_escalates_unauthenticated(self):
        tickets, _ = kaggle_support.load(FIX / "kaggle_sample.csv")
        result = run_batch(tickets, classifier_name="keyword")
        # public_dataset_customer doesn't resolve -> unauthenticated -> no auto-resolve
        self.assertEqual(result.auto_resolved, 0)
        self.assertEqual(result.billing_escape, 0)


class DispatchCliTests(unittest.TestCase):
    def test_import_dataset_dispatch_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "imported.jsonl"
            import sys

            argv = sys.argv
            sys.argv = [
                "import_dataset",
                "--source",
                "kaggle",
                "--input",
                str(FIX / "kaggle_sample.csv"),
                "--output",
                str(out),
            ]
            try:
                import_dataset.main()
            finally:
                sys.argv = argv
            lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
            self.assertEqual(len(lines), 5)


class MarkdownReportTests(unittest.TestCase):
    def test_report_contains_safety_and_source(self):
        tickets, _ = kaggle_support.load(FIX / "kaggle_sample.csv")
        result = run_batch(
            tickets,
            classifier_name="keyword",
            assume_customer="cust_alice",
            source="kaggle_test",
        )
        md = render_markdown_report(result)
        self.assertIn("# RelayOps batch report — kaggle_test", md)
        self.assertIn("Unsafe auto-action", md)
        self.assertIn("Billing escape", md)
        self.assertIn("Top failure categories", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
