"""Hybrid retriever — BM25 (lexical) + a pluggable dense arm, fused with RRF.

The dense arm is now a real, swappable ``Embedder`` (Voyage AI neural embeddings
when configured; a labeled TF-IDF fallback otherwise — see ``embeddings.py``).

Grounding is driven by the dense arm's **semantic similarity threshold**
(``embedder.min_similarity``), not by lexical ">0 on any term". A chunk is
admitted only if its dense cosine clears that threshold; BM25 then refines the
ranking of the admitted set. With neural embeddings this makes grounding robust
and stopword-independent: an off-topic query scores low even if it happens to
share a function word with a document, so ``search`` returns [] and the pipeline
escalates as "unverifiable" instead of answering from nothing.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .embeddings import Embedder, default_embedder, tokenize
from .store import Chunk

# BM25 params
_K1 = 1.5
_B = 0.75
# Reciprocal-rank-fusion constant
_RRF_K = 60


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float  # fused RRF score
    rank: int  # 1-based final rank

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
    def __init__(self, chunks: list[Chunk], embedder: Embedder | None = None) -> None:
        self.chunks = chunks
        texts = [c.text for c in chunks]
        self.embedder = embedder or default_embedder(texts)

        # --- lexical (BM25) index ---
        self._toks = [tokenize(t) for t in texts]
        self._N = len(chunks)
        self._avgdl = (sum(len(t) for t in self._toks) / self._N) if self._N else 0.0
        self._df: Counter[str] = Counter()
        for toks in self._toks:
            for term in set(toks):
                self._df[term] += 1
        self._tf: list[Counter[str]] = [Counter(t) for t in self._toks]

        # --- dense index (embedder-owned vectors) ---
        self._doc_vecs = self.embedder.embed_documents(texts) if self._N else []

    # --- scoring arms ---

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

    # --- fusion ---

    def search(self, query: str, k: int = 2) -> list[RetrievedChunk]:
        if self._N == 0:
            return []

        q_terms = tokenize(query)
        bm25 = self._bm25_scores(q_terms) if q_terms else [0.0] * self._N

        qv = self.embedder.embed_query(query)
        cos = [self.embedder.cosine(qv, dv) for dv in self._doc_vecs]

        # Semantic grounding gate: admit only chunks above the similarity floor.
        threshold = self.embedder.min_similarity
        admitted = [i for i in range(self._N) if cos[i] >= threshold]
        if not admitted:
            return []

        # Rankings restricted to the admitted set.
        bm_rank = sorted((i for i in admitted if bm25[i] > 0), key=lambda i: bm25[i], reverse=True)
        cos_rank = sorted(admitted, key=lambda i: cos[i], reverse=True)

        fused: dict[int, float] = {}
        for ranking in (bm_rank, cos_rank):
            for pos, doc_i in enumerate(ranking, start=1):
                fused[doc_i] = fused.get(doc_i, 0.0) + 1.0 / (_RRF_K + pos)

        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [
            RetrievedChunk(chunk=self.chunks[i], score=score, rank=r)
            for r, (i, score) in enumerate(ordered, start=1)
        ]
