"""Knowledge-base loader — the changing-facts layer.

Loads small markdown articles from ``knowledge_base/`` and splits each into
paragraph chunks. This is intentionally tiny and file-based for v1; a re-embedding
pipeline into Chroma (per-brand, larger KBs) is the deferred extension behind the
same ``Chunk`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DEFAULT_KB = Path(__file__).resolve().parents[2] / "knowledge_base"


@dataclass(frozen=True)
class Chunk:
    doc_id: str       # source file stem
    title: str        # article title (first heading)
    chunk_id: int     # paragraph index within the article
    text: str
    source: str       # human-readable citation source, e.g. "device-reset.md#1"


def load_chunks(kb_dir: Path | str | None = None) -> list[Chunk]:
    kb = Path(kb_dir) if kb_dir else _DEFAULT_KB
    chunks: list[Chunk] = []
    for path in sorted(kb.glob("*.md")):
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        lines = raw.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else path.stem
        body = "\n".join(lines[1:]).strip()
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        for i, para in enumerate(paragraphs):
            chunks.append(
                Chunk(
                    doc_id=path.stem,
                    title=title,
                    chunk_id=i,
                    text=" ".join(para.split()),
                    source=f"{path.name}#{i}",
                )
            )
    return chunks
