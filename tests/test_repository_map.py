"""Deterministic repository map: complete structure without a model or false claims."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.memory.repository_map import (build_map, map_unit, read_map,
                                              read_unit, search_units,
                                              store_claims)


class RepositoryMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_map_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "README.md").write_text(
            "# Demo\n\nThis repository runs a worker.\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text(
            "import json\n\n"
            "def run_worker(value):\n"
            "    return value\n\n"
            "class Worker:\n"
            "    pass\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text(
            "# Guide\n\nRun the worker with a value.\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_worker.py").write_text(
            "def test_worker():\n    assert True\n", encoding="utf-8")

    def test_missing_map_is_read_only_and_not_fabricated(self):
        before = self.root / "meetingvar"
        snapshot = read_map(self.root)
        self.assertEqual(snapshot["map_status"], "not_built")
        self.assertFalse(before.exists())
        self.assertIsNone(map_unit(self.root, "missing"))

    def test_build_maps_roles_entrypoints_symbols_and_imports(self):
        result = build_map(self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["map_status"], "complete")
        roles = {row["path"]: row["role"] for row in result["sources"]}
        self.assertEqual(roles["README.md"], "rules")
        self.assertEqual(roles["src/main.py"], "entrypoint")
        self.assertEqual(roles["tests/test_worker.py"], "test")
        python = next(row for row in result["sources"] if row["path"] == "src/main.py")
        self.assertIn("json", python["imports"])
        symbols = {unit["label"] for unit in result["units"] if unit["path"] == "src/main.py"
                   and unit["kind"] == "symbol"}
        self.assertEqual(symbols, {"run_worker", "Worker"})
        self.assertTrue(any(row["path"] == "README.md" for row in result["entrypoints"]))
        self.assertFalse(result["coverage"]["semantic_exhaustive"])

    def test_read_unit_returns_exact_window_and_current_ref(self):
        build_map(self.root)
        snapshot = read_map(self.root, limit=200)
        unit = next(row for row in snapshot["units"]
                    if row["path"] == "src/main.py" and row["kind"] == "symbol"
                    and row["label"] == "run_worker")
        result = read_unit(self.root, unit["unit_id"])
        self.assertEqual(result["status"], "ok")
        self.assertIn("def run_worker", result["items"][0]["text"])
        ref = result["source_refs"][0]
        self.assertTrue(ref["bytes_hash"])
        self.assertEqual(ref["source_revision"], unit["source_ref"]["source_revision"])
        self.assertIn(ref["quote"], result["items"][0]["text"])

    def test_search_reads_body_beyond_display_excerpt(self):
        marker = "UNIQUE_TAIL_MARKER"
        (self.root / "docs" / "long.md").write_text(
            "# Long\n\n" + ("filler " * 8000) + marker, encoding="utf-8")
        build_map(self.root)
        hits = search_units(self.root, marker)
        self.assertEqual(hits["status"], "ok")
        self.assertGreaterEqual(hits["total"], 1)
        self.assertTrue(any(row["path"] == "docs/long.md" for row in hits["units"]))

    def test_claim_requires_current_exact_quote_and_expires_on_change(self):
        build_map(self.root)
        snapshot = read_map(self.root, limit=200)
        unit = next(row for row in snapshot["units"] if row["path"] == "README.md"
                    and row["kind"] == "source")
        read = read_unit(self.root, unit["unit_id"])
        ref = {**read["source_refs"][0], "unit_id": unit["unit_id"]}
        good = store_claims(self.root, [{"text": "The repo runs a worker.", "kind": "purpose",
                                         "source_refs": [ref]}])
        self.assertTrue(good["ok"], good)
        self.assertFalse(good["claims"][0]["semantic_verified"])
        bad = store_claims(self.root, [{"text": "Invented.", "kind": "fact",
                                        "source_refs": [{**ref, "quote": "not in source"}]}])
        self.assertFalse(bad["ok"])
        (self.root / "README.md").write_text("# Demo\n\nChanged.\n", encoding="utf-8")
        build_map(self.root)
        self.assertFalse(any(row["text"] == "The repo runs a worker."
                             for row in read_map(self.root)["claims"]))

    def test_read_map_hides_claims_and_units_after_unbuilt_file_change(self):
        build_map(self.root)
        snapshot = read_map(self.root, limit=200)
        unit = next(row for row in snapshot["units"] if row["path"] == "README.md"
                    and row["kind"] == "source")
        read = read_unit(self.root, unit["unit_id"])
        ref = {**read["source_refs"][0], "unit_id": unit["unit_id"]}
        self.assertTrue(store_claims(self.root, [{"text": "Fresh claim.", "kind": "fact",
                                                  "source_refs": [ref]}])["ok"])
        (self.root / "README.md").write_text("# Demo\n\nChanged but not rebuilt.\n", encoding="utf-8")
        stale = read_map(self.root, limit=200)
        self.assertEqual(stale["map_status"], "partial")
        self.assertFalse(any(row["text"] == "Fresh claim." for row in stale["claims"]))
        self.assertFalse(any(row["path"] == "README.md" for row in stale["units"]))
        self.assertTrue(any(row["path"] == "README.md" and row["status"] == "changed"
                            for row in stale["sources"]))

    def test_deleted_and_unreadable_sources_are_explicit(self):
        (self.root / "bad.txt").write_bytes(b"PK\x03\x04" + b"\xd2" * 200)
        result = build_map(self.root)
        bad = next(row for row in result["sources"] if row["path"] == "bad.txt")
        self.assertFalse(bad["complete"])
        self.assertTrue(any(row["path"] == "bad.txt" for row in result["errors"]))
        self.assertEqual(result["map_status"], "partial")
        self.assertTrue(any(row["path"] == "README.md" for row in result["sources"]))
        (self.root / "README.md").unlink()
        result = build_map(self.root)
        self.assertTrue(any(row["path"] == "README.md" and row["status"] == "stale"
                            for row in result["sources"]))


if __name__ == "__main__":
    unittest.main()