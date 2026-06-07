"""Step 3 tests — hybrid RAG with citations.

Covers the retriever in isolation (relevant hit, grounding returns nothing for
off-topic queries) and the FAQ path end to end (cited answer; escalation when
the KB can't ground the question).
"""

from __future__ import annotations

import unittest

from src.core.models import Disposition, Intent
from src.graph.pipeline import handle_turn
from src.rag.retriever import HybridRetriever
from src.rag.store import load_chunks


class RetrieverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = HybridRetriever(load_chunks())

    def test_kb_loads(self):
        self.assertGreater(len(self.r.chunks), 0)

    def test_relevant_query_retrieves_reset_doc(self):
        hits = self.r.search("how long does a reset take", k=2)
        self.assertTrue(hits)
        self.assertTrue(any(h.doc_id == "device-reset" for h in hits))

    def test_offline_query_finds_troubleshooting(self):
        hits = self.r.search("why does my router keep going offline", k=2)
        self.assertTrue(hits)
        self.assertTrue(
            any(h.doc_id in {"connectivity-troubleshooting", "outages-and-coverage"}
                for h in hits)
        )

    def test_off_topic_query_returns_nothing(self):
        self.assertEqual(self.r.search("photosynthesis chlorophyll mitochondria"), [])

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.r.search("the a of to"), [])


class FaqPipelineTests(unittest.TestCase):
    def test_faq_answer_is_cited(self):
        r = handle_turn("how long does a reset take?", auth_token="tok_alice")
        self.assertEqual(r.intent, Intent.DEVICE_FAQ)
        self.assertEqual(r.disposition, Disposition.RESPOND)
        self.assertTrue(r.citations)
        # citation markers in the text line up with the citations list
        self.assertIn("[1]", r.text)
        self.assertIn("Sources:", r.text)
        self.assertEqual(r.citations[0]["n"], 1)

    def test_unverifiable_faq_escalates(self):
        r = handle_turn(
            "how do I set up roaming in Antarctica?", auth_token="tok_alice"
        )
        self.assertEqual(r.intent, Intent.DEVICE_FAQ)
        self.assertTrue(r.escalated)
        self.assertEqual(r.handoff_context["reason"], "unverifiable")
        self.assertEqual(r.citations, [])

    def test_reset_action_still_not_faq(self):
        # An imperative reset is an action, not an FAQ -> no retrieval/citations.
        r = handle_turn("reset my router", auth_token="tok_alice")
        self.assertEqual(r.intent, Intent.RESET_DEVICE)
        self.assertEqual(r.citations, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
