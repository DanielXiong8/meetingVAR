"""Deterministic map→graph projection must produce navigable structure without an LLM."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.kg.map_graph import build_map_graph
from meetingvar.kg.store import GraphStore
from meetingvar.memory.repository_map import build_map


class MapGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_map_graph_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "README.md").write_text(
            "# Project Goal\n\nThe project builds a worker.\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text(
            "def run_worker(value):\n    return value\n", encoding="utf-8")
        build_map(self.root)

    def test_map_graph_links_documents_topics_and_project(self):
        report = build_map_graph(self.root)
        self.assertTrue(report["ok"], report)
        self.assertGreaterEqual(report["coverage"]["documents"], 2)
        self.assertGreater(report["coverage"]["topics"], 0)
        store = GraphStore(self.root)
        stats = store.stats()
        self.assertGreaterEqual(stats["nodes"], 4)
        self.assertGreaterEqual(stats["edges"], 2)
        kinds = stats["by_kind"]
        self.assertGreaterEqual(kinds.get("Document", 0), 2)
        self.assertGreaterEqual(kinds.get("Topic", 0), 1)

    def test_map_graph_is_idempotent(self):
        first = build_map_graph(self.root)
        second = build_map_graph(self.root)
        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(first["stats"]["nodes"], second["stats"]["nodes"])
        self.assertEqual(first["stats"]["edges"], second["stats"]["edges"])


if __name__ == "__main__":
    unittest.main()