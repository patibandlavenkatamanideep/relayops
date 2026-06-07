"""Step 3 tests — hybrid RAG with citations.

Covers the retriever in isolation (relevant hit, grounding returns nothing for
off-topic queries) and the FAQ path end to end (cited answer; escalation when
the KB can't ground the question).
"""

from __future__ import annotations

import re
import unittest

from src.core.models import Disposition, Intent
from src.graph.pipeline import handle_turn
from src.rag.retriever import HybridRetriever
from src.rag.store import load_chunks


class TopicEmbedder:
    """A stand-in NEURAL embedder for tests: maps text to a small topic vector so
    similarity is *semantic*, not lexical. Lets us prove grounding ignores
    incidental function-word overlap (the thing real Voyage embeddings fix)."""

    name = "fake-neural"
    min_similarity = 0.5
    _TOPICS = [
        ("reset", {"reset", "reboot", "restart", "device", "router", "online", "offline", "power"}),
        ("connectivity", {"firmware", "signal", "interference", "drops", "connection"}),
        ("outage", {"outage", "coverage", "tower", "area", "status"}),
        ("weather", {"weather", "rain", "umbrella", "sunny", "forecast", "temperature"}),
    ]

    def _vec(self, text: str) -> list[float]:
        toks = set(re.findall(r"[a-z]+", text.lower()))
        return [float(len(toks & words)) for _, words in self._TOPICS]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)

    def cosine(self, a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0


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


class SemanticGroundingTests(unittest.TestCase):
    """Prove the dense arm is pluggable and grounding is semantic, not lexical."""

    def setUp(self):
        self.r = HybridRetriever(load_chunks(), embedder=TopicEmbedder())

    def test_uses_injected_embedder(self):
        self.assertEqual(self.r.embedder.name, "fake-neural")

    def test_semantically_relevant_query_is_grounded(self):
        hits = self.r.search("my router is offline, can you reset it", k=2)
        self.assertTrue(hits)

    def test_function_word_overlap_does_not_ground(self):
        # Shares plenty of function words with the KB ("do", "i", "it", "be",
        # "out") but is semantically about weather -> neural cosine ~0 -> no
        # grounding. This is the case the stopword hack used to be needed for.
        hits = self.r.search("do I need an umbrella, will it be sunny out?", k=2)
        self.assertEqual(hits, [])

    def test_threshold_is_embedder_owned(self):
        # Raise the bar above what any topic match can reach -> nothing admitted.
        emb = TopicEmbedder()
        emb.min_similarity = 1.01
        r = HybridRetriever(load_chunks(), embedder=emb)
        self.assertEqual(r.search("reset my router", k=2), [])


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
