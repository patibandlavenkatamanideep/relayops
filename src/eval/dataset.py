"""Labeled intent dataset — message -> intent pairs.

Curating this set is the real work of the fine-tune (DESIGN.md §5): it is seeded
from each intent's example phrasings and grown outward with paraphrases that
deliberately lack the keyword cues, so a learned model can show generalization
the keyword baseline can't.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..core.models import Intent

_DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "intents.jsonl"


@dataclass(frozen=True)
class Example:
    text: str
    intent: Intent


def load_dataset(path: Path | str | None = None) -> list[Example]:
    p = Path(path) if path else _DEFAULT_PATH
    examples: list[Example] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        examples.append(Example(text=row["text"], intent=Intent(row["intent"])))
    return examples


def stratified_split(
    data: list[Example], test_frac: float = 0.3, seed: int = 13
) -> tuple[list[Example], list[Example]]:
    """Per-class split so every intent appears in both train and test."""
    by_intent: dict[Intent, list[Example]] = defaultdict(list)
    for ex in data:
        by_intent[ex.intent].append(ex)

    rng = random.Random(seed)
    train: list[Example] = []
    test: list[Example] = []
    for intent, items in by_intent.items():
        items = items[:]
        rng.shuffle(items)
        n_test = max(1, round(len(items) * test_frac))
        test.extend(items[:n_test])
        train.extend(items[n_test:])
    rng.shuffle(train)
    rng.shuffle(test)
    return train, test


def stratified_split_3way(
    data: list[Example],
    val_frac: float = 0.15,
    test_frac: float = 0.15,
    seed: int = 13,
) -> tuple[list[Example], list[Example], list[Example]]:
    """Per-class train/val/test split for the fine-tune (70/15/15 by default)."""
    by_intent: dict[Intent, list[Example]] = defaultdict(list)
    for ex in data:
        by_intent[ex.intent].append(ex)

    rng = random.Random(seed)
    train: list[Example] = []
    val: list[Example] = []
    test: list[Example] = []
    for items in by_intent.values():
        items = items[:]
        rng.shuffle(items)
        n = len(items)
        n_test = max(1, round(n * test_frac))
        n_val = max(1, round(n * val_frac))
        test.extend(items[:n_test])
        val.extend(items[n_test : n_test + n_val])
        train.extend(items[n_test + n_val :])
    for part in (train, val, test):
        rng.shuffle(part)
    return train, val, test
