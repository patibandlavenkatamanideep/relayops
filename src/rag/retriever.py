"""Hybrid retriever — BM25 (lexical) + TF-IDF cosine (vector) fused with RRF.

This is the v1 stand-in for the design's "BM25 + dense + RRF" retriever,
implemented in pure Python so the slice runs with no vector DB or embedding
model. The ``HybridRetriever.search`` interface is what a Chroma-backed dense
arm would slot into later — swap the TF-IDF arm for real embeddings and the
fusion + citation plumbing is unchanged.

Grounding: a chunk only enters a ranking if it has positive evidence for the
query (BM25 > 0 or cosine > 0). If neither arm finds anything, ``search``
returns []. The pipeline treats an empty result as "unverifiable" and escalates,
rather than answering from nothing.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .store import Chunk

_TOKEN = re.compile(r"\b\w+\b")
_STOP = frozenset(
    "a an the is are was were be been do does did to of in on for and or it its "
    "i you my your me we our this that these those with as at by from "
    # function / question words: shouldn't ground a retrieval on their own
    "how why what when where which who can could should would will may might must "
    "up down out into onto off over under about get got also just only very "
    "if then so than there here not no yes".split()
)

# BM25 params
_K1 = 1.5
_B = 0.75
# Reciprocal-rank-fusion constant
_RRF_K = 60


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float          # fused RRF score
    rank: int             # 1-based final rank

    # convenience passthroughs for citation building
    @property
    def title(self) -> str:
        return self.chunk.title

    @property
    def source(self) -> str:
        return self.chunk.source

    @property
    def doc_id(self) -> str:
        return self.chunk.doc_id

    @property
    def text(self) -> str:
        return self.chunk.text


class HybridRetriever:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._toks = [_tokenize(c.text) for c in chunks]
        self._N = len(chunks)
        self._avgdl = (sum(len(t) for t in self._toks) / self._N) if self._N else 0.0

        # document frequency over the corpus
        self._df: Counter[str] = Counter()
        for toks in self._toks:
            for term in set(toks):
                self._df[term] += 1

        # per-chunk term counts (shared by both arms)
        self._tf: list[Counter[str]] = [Counter(t) for t in self._toks]

        # tf-idf doc vectors (normalised) for the cosine arm
        self._tfidf_docs: list[dict[str, float]] = [self._tfidf_vec(tf) for tf in self._tf]

    # --- scoring arms ---

    def _idf_tfidf(self, term: str) -> float:
        return math.log((self._N + 1) / (self._df.get(term, 0) + 1)) + 1.0

    def _tfidf_vec(self, tf: Counter[str]) -> dict[str, float]:
        vec = {t: c * self._idf_tfidf(t) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def _bm25_scores(self, q_terms: list[str]) -> list[float]:
        scores = [0.0] * self._N
        for term in set(q_terms):
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self._N - df + 0.5) / (df + 0.5))
            for i, tf in enumerate(self._tf):
                f = tf.get(term, 0)
                if not f:
                    continue
                dl = len(self._toks[i])
                denom = f + _K1 * (1 - _B + _B * dl / (self._avgdl or 1))
                scores[i] += idf * (f * (_K1 + 1)) / denom
        return scores

    def _cosine_scores(self, q_terms: list[str]) -> list[float]:
        qv = self._tfidf_vec(Counter(q_terms))
        return [
            sum(w * dv.get(t, 0.0) for t, w in qv.items())
            for dv in self._tfidf_docs
        ]

    # --- fusion ---

    @staticmethod
    def _ranking(scores: list[float]) -> list[int]:
        """Indices with positive score, best first."""
        idx = [i for i, s in enumerate(scores) if s > 0]
        idx.sort(key=lambda i: scores[i], reverse=True)
        return idx

    def search(self, query: str, k: int = 2) -> list[RetrievedChunk]:
        if self._N == 0:
            return []
        q_terms = _tokenize(query)
        if not q_terms:
            return []

        bm25_rank = self._ranking(self._bm25_scores(q_terms))
        cos_rank = self._ranking(self._cosine_scores(q_terms))

        fused: dict[int, float] = {}
        for ranking in (bm25_rank, cos_rank):
            for pos, doc_i in enumerate(ranking, start=1):
                fused[doc_i] = fused.get(doc_i, 0.0) + 1.0 / (_RRF_K + pos)

        if not fused:
            return []
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [
            RetrievedChunk(chunk=self.chunks[i], score=score, rank=r)
            for r, (i, score) in enumerate(ordered, start=1)
        ]
