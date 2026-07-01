"""Redacted ticket importer tests (v2.6).

Covers CSV and JSONL import, required-field / empty-message / invalid-sensitivity
skips (each with a deterministic reason), safe handling of an empty file,
sensitivity normalization, whitelist dropping of stray credential columns (no
secret is ever imported), and that importing never mutates the input rows. The
bundled ``examples/`` sample is imported too, so the shipped synthetic data stays
valid.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from src.importers import (
    ImportResult,
    import_csv,
    import_file,
    import_jsonl,
    import_rows,
    normalize_sensitivity,
)
from src.importers.schemas import REQUIRED_FIELDS

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _row(**overrides) -> dict:
    base = {
        "ticket_id": "RT-1",
        "customer_id": "cust_1",
        "message": "please reset my router",
        "category": "device_reset",
        "expected_resolution": "scoped device reset",
        "sensitivity_level": "low",
    }
    base.update(overrides)
    return base


class ImportRowsTests(unittest.TestCase):
    def test_imports_valid_rows(self):
        result = import_rows([_row(), _row(ticket_id="RT-2")])
        self.assertEqual(result.imported_count, 2)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(result.total_received, 2)
        self.assertEqual(result.records[0].ticket_id, "RT-1")

    def test_skips_each_missing_required_field_with_reason(self):
        for field_name in REQUIRED_FIELDS:
            if field_name == "message":
                continue  # empty message has its own dedicated reason (below)
            result = import_rows([_row(**{field_name: ""})])
            self.assertEqual(result.imported_count, 0)
            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(result.skipped[0].reason, f"missing_required_field:{field_name}")

    def test_skips_empty_message_with_reason(self):
        result = import_rows([_row(message="   ")])
        self.assertEqual(result.imported_count, 0)
        self.assertEqual(result.skipped[0].reason, "empty_message")

    def test_invalid_sensitivity_is_skipped_deterministically(self):
        result = import_rows([_row(sensitivity_level="ultra-mega")])
        self.assertEqual(result.imported_count, 0)
        self.assertEqual(result.skipped[0].reason, "invalid_sensitivity_level:ultra-mega")

    def test_sensitivity_is_normalized(self):
        # Synonyms map to canonical levels; the record stores the canonical value.
        result = import_rows(
            [
                _row(ticket_id="a", sensitivity_level="PII"),
                _row(ticket_id="b", sensitivity_level="none"),
                _row(ticket_id="c", sensitivity_level="Critical"),
            ]
        )
        levels = {r.ticket_id: r.sensitivity_level for r in result.records}
        self.assertEqual(levels, {"a": "restricted", "b": "low", "c": "high"})
        self.assertIsNone(normalize_sensitivity("banana"))

    def test_empty_input_is_safe(self):
        result = import_rows([])
        self.assertIsInstance(result, ImportResult)
        self.assertEqual(result.imported_count, 0)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(result.total_received, 0)

    def test_stray_credential_columns_are_dropped(self):
        # A partner export accidentally includes secrets; none may be imported.
        row = _row(api_key="sk-should-not-load", password="hunter2", ssn="123-45-6789")
        result = import_rows([row])
        self.assertEqual(result.imported_count, 1)
        record = result.records[0].to_dict()
        self.assertNotIn("api_key", record)
        self.assertNotIn("password", record)
        self.assertNotIn("ssn", record)
        self.assertNotIn("sk-should-not-load", record.values())
        self.assertNotIn("hunter2", record.values())

    def test_import_does_not_mutate_input_rows(self):
        rows = [_row(api_key="secret"), _row(ticket_id="RT-2", sensitivity_level="none")]
        before = copy.deepcopy(rows)
        import_rows(rows)
        self.assertEqual(rows, before)

    def test_non_dict_row_is_skipped(self):
        result = import_rows([_row(), "not-a-dict", 42])  # type: ignore[list-item]
        self.assertEqual(result.imported_count, 1)
        self.assertEqual(result.skipped_count, 2)


class ImportFileTests(unittest.TestCase):
    def test_imports_valid_csv_rows(self):
        result = import_csv(_EXAMPLES / "redacted_tickets.csv")
        self.assertEqual(result.total_received, 10)
        self.assertEqual(result.imported_count, 10)
        self.assertEqual(result.skipped_count, 0)

    def test_imports_valid_jsonl_rows(self):
        result = import_jsonl(_EXAMPLES / "redacted_tickets.jsonl")
        self.assertEqual(result.imported_count, 10)
        self.assertEqual(result.skipped_count, 0)

    def test_csv_and_jsonl_samples_agree(self):
        csv_ids = [r.ticket_id for r in import_csv(_EXAMPLES / "redacted_tickets.csv").records]
        jsonl_ids = [
            r.ticket_id for r in import_jsonl(_EXAMPLES / "redacted_tickets.jsonl").records
        ]
        self.assertEqual(csv_ids, jsonl_ids)

    def test_empty_file_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("empty.csv", "empty.jsonl"):
                path = Path(d) / name
                path.write_text("")
                result = import_file(path)
                self.assertEqual(result.imported_count, 0)
                self.assertEqual(result.skipped_count, 0)

    def test_malformed_jsonl_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.jsonl"
            path.write_text('{"ticket_id": "RT-1"\n')  # truncated JSON
            result = import_jsonl(path)
            self.assertEqual(result.imported_count, 0)
            self.assertEqual(result.skipped_count, 1)

    def test_unsupported_suffix_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tickets.xlsx"
            path.write_text("nope")
            with self.assertRaises(ValueError):
                import_file(path)


class ImporterUsesNoNetworkTests(unittest.TestCase):
    def test_importer_module_imports_no_network_libraries(self):
        # A cheap structural guard that the importer stays local: it must not pull
        # in an HTTP/network client at import time.
        import src.importers.ticket_importer as mod

        source = Path(mod.__file__).read_text()
        for banned in ("import requests", "import urllib", "import httpx", "import socket"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
