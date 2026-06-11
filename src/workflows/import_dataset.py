"""Unified public-dataset importer — one CLI over every source.

A convenience dispatcher so you don't have to remember each importer's module
path. Picks the right mapper by ``--source`` and writes the canonical tickets
JSONL the batch runner consumes.

    python3 -m src.workflows.import_dataset --source kaggle  --input tickets.csv  --output var/imported_public_tickets.jsonl
    python3 -m src.workflows.import_dataset --source hf       --input tickets.jsonl
    python3 -m src.workflows.import_dataset --source twitter  --input twcs.csv

Then validate:

    python3 -m src.workflows.ticket_runner \\
        --input var/imported_public_tickets.jsonl \\
        --classifier nb_calibrated --assume-customer cust_alice --source kaggle
"""

from __future__ import annotations

from .importers import get_importer, write_and_report

SOURCES = ("kaggle", "hf", "twitter")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Import a public support dataset into RelayOps tickets"
    )
    parser.add_argument("--source", required=True, help=f"dataset source: {', '.join(SOURCES)}")
    parser.add_argument(
        "--input", required=True, help="downloaded dataset file (.csv/.jsonl/.json)"
    )
    parser.add_argument(
        "--output",
        default="var/imported_public_tickets.jsonl",
        help="normalized tickets JSONL output",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap number of tickets")
    args = parser.parse_args()

    load = get_importer(args.source)  # raises ValueError on an unknown source
    tickets, unmapped = load(args.input, limit=args.limit)
    write_and_report(args.source, tickets, unmapped, args.output)


if __name__ == "__main__":
    main()
