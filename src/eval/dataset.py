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
    group: str = ""


def load_dataset(path: Path | str | None = None) -> list[Example]:
    p = Path(path) if path else _DEFAULT_PATH
    examples: list[Example] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        examples.append(
            Example(
                text=row["text"],
                intent=Intent(row["intent"]),
                group=str(row.get("group") or row["text"]),
            )
        )
    return examples


def _grouped(items: list[Example]) -> list[list[Example]]:
    by_group: dict[str, list[Example]] = defaultdict(list)
    for ex in items:
        by_group[ex.group or ex.text].append(ex)
    return list(by_group.values())


def stratified_split(
    data: list[Example], test_frac: float = 0.3, seed: int = 13
) -> tuple[list[Example], list[Example]]:
    """Per-class split so every intent appears in both train and test.

    Examples may include an optional ``group`` field in JSONL. Generated
    paraphrases from the same template family share a group, and the splitter
    keeps groups together so train/test metrics are not inflated by near-duplicate
    template siblings leaking across the boundary.
    """
    by_intent: dict[Intent, list[Example]] = defaultdict(list)
    for ex in data:
        by_intent[ex.intent].append(ex)

    rng = random.Random(seed)
    train: list[Example] = []
    test: list[Example] = []
    for intent, items in by_intent.items():
        groups = _grouped(items)
        rng.shuffle(groups)
        target_test = max(1, round(len(items) * test_frac))
        test_groups: list[list[Example]] = []
        train_groups: list[list[Example]] = []
        intent_test: list[Example] = []
        intent_train: list[Example] = []
        for group in groups:
            if len(intent_test) < target_test:
                test_groups.append(group)
                intent_test.extend(group)
            else:
                train_groups.append(group)
                intent_train.extend(group)
        if not train_groups and len(test_groups) > 1:
            moved = test_groups.pop()
            train_groups.append(moved)
            intent_test = [ex for group in test_groups for ex in group]
            intent_train = [ex for group in train_groups for ex in group]
        test.extend(intent_test)
        train.extend(intent_train)
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
        groups = _grouped(items)
        rng.shuffle(groups)
        n = len(items)
        target_test = max(1, round(n * test_frac))
        target_val = max(1, round(n * val_frac))
        intent_train: list[Example] = []
        intent_val: list[Example] = []
        intent_test: list[Example] = []
        for group in groups:
            if len(intent_test) < target_test:
                intent_test.extend(group)
            elif len(intent_val) < target_val:
                intent_val.extend(group)
            else:
                intent_train.extend(group)
        test.extend(intent_test)
        val.extend(intent_val)
        train.extend(intent_train)
    for part in (train, val, test):
        rng.shuffle(part)
    return train, val, test
