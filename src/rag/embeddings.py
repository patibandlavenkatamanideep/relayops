"""Embedders for the dense retrieval arm.

The dense arm is pluggable behind the ``Embedder`` protocol:

  * ``VoyageEmbedder`` — REAL neural embeddings via Voyage AI, Anthropic's
    recommended embeddings partner. (Anthropic has no first-party embeddings
    endpoint — the Messages API is text-generation only — so Voyage is the
    documented path.) Activates automatically when ``VOYAGE_API_KEY`` and the
    ``voyageai`` package are present.
  * ``TfidfEmbedder`` — an OFFLINE FALLBACK so the slice runs with zero deps and
    no network. It is a lexical vector space (TF-IDF cosine), NOT neural; it is
    explicitly the second choice and is only used when Voyage is unavailable.

Grounding robustness: each embedder declares ``min_similarity``, the cosine
floor below which a chunk is considered semantically irrelevant. With neural
embeddings this is a true semantic gate (a query about Antarctic roaming scores
low against a reset article regardless of incidental word overlap), which is what
removes the dependence on a hand-tuned stopword list. The TF-IDF fallback is
still lexical, so it keeps the stopword list as a best-effort aid.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import Protocol, Sequence

# --- tokenization (shared with the BM25 arm in retriever.py) -------------------

_TOKEN = re.compile(r"\b\w+\b")
_STOP = frozenset(
    "a an the is are was were be been do does did to of in on for and or it its "
    "i you my your me we our this that these those with as at by from "
    # function / question words: shouldn't ground a lexical retrieval on their own
    "how why what when where which who can could should would will may might must "
    "up down out into onto off over under about get got also just only very "
    "if then so than there here not no yes".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


# --- protocol ------------------------------------------------------------------

# A vector is either a sparse dict (TF-IDF) or a dense list (neural). The owning
# embedder knows how to score similarity between its own vectors.
Vector = object


class Embedder(Protocol):
    name: str
    min_similarity: float

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...
    def embed_query(self, text: str) -> Vector: ...
    def cosine(self, a: Vector, b: Vector) -> float: ...


# --- offline fallback: TF-IDF cosine ------------------------------------------


class TfidfEmbedder:
    """Lexical vector-space fallback. Fit on the corpus at construction.

    Not neural — kept only so the pipeline runs offline. Grounding here is still
    fundamentally lexical, so ``min_similarity`` is a small positive floor that
    requires *some* term overlap; the stopword list does the heavy lifting.
    """

    name = "tfidf-fallback"
    min_similarity = 1e-9  # require non-zero lexical overlap

    def __init__(self, corpus_texts: Sequence[str]) -> None:
        toks = [tokenize(t) for t in corpus_texts]
        self._n = max(len(toks), 1)
        self._df: Counter[str] = Counter()
        for ts in toks:
            for term in set(ts):
                self._df[term] += 1

    def _idf(self, term: str) -> float:
        return math.log((self._n + 1) / (self._df.get(term, 0) + 1)) + 1.0

    def _vec(self, text: str) -> dict[str, float]:
        tf = Counter(tokenize(text))
        vec = {t: c * self._idf(t) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> Vector:
        return self._vec(text)

    def cosine(self, a: Vector, b: Vector) -> float:  # both L2-normalised dicts
        da, db = a, b  # type: ignore[assignment]
        if len(da) > len(db):  # iterate the smaller dict
            da, db = db, da
        return sum(w * db.get(t, 0.0) for t, w in da.items())


# --- primary: Voyage AI neural embeddings -------------------------------------


class VoyageEmbedder:
    """Neural embeddings via Voyage AI (Anthropic's recommended partner).

    Model is configurable via ``VOYAGE_MODEL`` (default ``voyage-3-large`` —
    verify the current model name at docs.voyageai.com). Voyage distinguishes
    document vs query inputs via ``input_type``, which improves retrieval.
    """

    name = "voyage"

    def __init__(self) -> None:
        import voyageai  # raises if the package isn't installed

        self._model = os.environ.get("VOYAGE_MODEL", "voyage-3-large")
        self.min_similarity = float(os.environ.get("VOYAGE_MIN_SIMILARITY", "0.5"))
        self._client = voyageai.Client()  # reads VOYAGE_API_KEY from env

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        res = self._client.embed(list(texts), model=self._model, input_type="document")
        return list(res.embeddings)

    def embed_query(self, text: str) -> Vector:
        res = self._client.embed([text], model=self._model, input_type="query")
        return res.embeddings[0]

    def cosine(self, a: Vector, b: Vector) -> float:  # dense lists
        va, vb = a, b  # type: ignore[assignment]
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(x * x for x in vb))
        return dot / (na * nb) if na and nb else 0.0


def default_embedder(corpus_texts: Sequence[str]) -> Embedder:
    """Prefer real neural embeddings; fall back to TF-IDF when unavailable."""
    if os.environ.get("VOYAGE_API_KEY"):
        try:
            return VoyageEmbedder()
        except Exception:
            pass  # package missing / client init failed -> fall back
    return TfidfEmbedder(corpus_texts)
