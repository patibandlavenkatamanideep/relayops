"""Export the intent dataset to chat-format JSONL for fine-tuning.

Run:  python3 -m src.eval.export_finetune_data

Produces train/val/test JSONL in the chat format Unsloth/TRL SFT expects. Each
line is one conversation; the assistant target is **strict JSON with intent
only** — no fabricated confidence/risk (confidence is read from the model's token
probabilities at inference; risk/route stay in the deterministic router). The
system prompt is imported from the classifier so training and inference match.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..router.finetuned_classifier import SYSTEM_PROMPT
from .dataset import Example, load_dataset, stratified_split_3way

_OUT_DIR = Path(__file__).resolve().parent / "data" / "finetune"


def _to_chat_line(ex: Example) -> str:
    target = json.dumps({"intent": ex.intent.value})  # strict JSON, intent only
    record = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ex.text},
            {"role": "assistant", "content": target},
        ]
    }
    return json.dumps(record, ensure_ascii=False)


def export(out_dir: Path | None = None, seed: int = 13) -> dict[str, int]:
    out = out_dir or _OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    train, val, test = stratified_split_3way(load_dataset(), seed=seed)
    counts = {}
    for name, split in (("train", train), ("val", val), ("test", test)):
        path = out / f"{name}.jsonl"
        path.write_text("\n".join(_to_chat_line(ex) for ex in split) + "\n", encoding="utf-8")
        counts[name] = len(split)
    return counts


def main() -> None:
    counts = export()
    total = sum(counts.values())
    print(f"exported {total} examples to {_OUT_DIR}")
    for name, n in counts.items():
        print(f"  {name}: {n}")


if __name__ == "__main__":
    main()
