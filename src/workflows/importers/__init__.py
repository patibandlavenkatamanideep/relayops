"""Public-dataset importers — map external support datasets onto RelayOps tickets.

Each importer takes a *downloaded* dataset file (CSV/JSONL/JSON — we do not fetch
anything over the network) and emits canonical tickets via
``normalize_ticket.normalize``. The result is a JSONL the batch runner can process
exactly like a hand-authored queue.

    python3 -m src.workflows.importers.kaggle_support  --input <file> --output var/imported_public_tickets.jsonl
    python3 -m src.workflows.importers.hf_support      --input <file> --output var/imported_public_tickets.jsonl
    python3 -m src.workflows.importers.twitter_support --input <file> --output var/imported_public_tickets.jsonl

Then validate (authenticating under a sandbox customer so routing/safety runs):

    python3 -m src.workflows.ticket_runner \\
        --input var/imported_public_tickets.jsonl \\
        --classifier nb_calibrated --assume-customer cust_alice --source kaggle
"""

from __future__ import annotations

from typing import Any, Callable

from ..normalize_ticket import write_jsonl

# (tickets, unmapped_count)
LoadResult = tuple[list[dict[str, Any]], int]
LoadFn = Callable[..., LoadResult]


def write_and_report(source: str, tickets: list[dict[str, Any]], unmapped: int, output: str) -> int:
    """Persist normalized tickets and print a one-line import summary. Shared by
    the per-dataset CLIs and the unified ``import_dataset`` dispatcher."""
    n = write_jsonl(tickets, output)
    total = n + unmapped
    rate = unmapped / total if total else 0.0
    print(
        f"source={source}  imported={n}  unmapped/skipped={unmapped} "
        f"(unmapped_rate={rate:.3f})  -> {output}"
    )
    return n


def cli_main(source: str, load: LoadFn) -> None:
    """Shared `python -m ...` entrypoint for every importer."""
    import argparse

    parser = argparse.ArgumentParser(description=f"Import the {source} dataset into RelayOps tickets")
    parser.add_argument("--input", required=True, help="downloaded dataset file (.csv/.jsonl/.json)")
    parser.add_argument(
        "--output",
        default="var/imported_public_tickets.jsonl",
        help="normalized tickets JSONL output",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap number of tickets")
    args = parser.parse_args()

    tickets, unmapped = load(args.input, limit=args.limit)
    write_and_report(source, tickets, unmapped, args.output)


def get_importer(source: str) -> LoadFn:
    """Look up an importer's ``load`` by short source name (lazy import)."""
    source = source.lower()
    if source in ("kaggle", "kaggle_support", "kaggle_customer_support"):
        from .kaggle_support import load

        return load
    if source in ("hf", "hf_support", "huggingface"):
        from .hf_support import load

        return load
    if source in ("twitter", "twitter_support"):
        from .twitter_support import load

        return load
    raise ValueError(f"unknown importer source: {source!r} (use kaggle / hf / twitter)")
