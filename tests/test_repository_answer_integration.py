"""Map and targeted exploration are exposed to answering with revision-bound evidence."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import llm
from meetingvar.agent.meeting_tools import _explore_project, _read_project_map
from meetingvar.agent.policy import AgentBudget
from meetingvar.agent.tools import ToolContext, ToolError
from meetingvar.memory.repository_map import build_map, read_map
from meetingvar.memory.repository_map import store_claims
from meetingvar.model_client import CallableModelClient


class RepositoryAnswerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_answer_map_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "README.md").write_text(
            "# Demo\n\nThis repository runs a worker.\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text(
            "def run_worker(value):\n    return value\n", encoding="utf-8")
        build_map(self.root)
        self.context = ToolContext(project_root=self.root)

    def test_tools_are_registered_for_the_agent_loop(self):
        from meetingvar.agent.meeting_tools import build_meeting_registry
        names = {spec.name for spec in build_meeting_registry().specs()}
        self.assertIn("read_project_map", names)
        self.assertIn("explore_project", names)

    def test_map_tool_is_read_only_and_marks_targeted_scope(self):
        result = _read_project_map({"limit": 20}, self.context)
        self.assertEqual(result["map_status"], "complete")
        self.assertGreaterEqual(result["totals"]["units"], 2)
        self.assertFalse(result["coverage"]["semantic_exhaustive"])
        self.assertFalse(result["coverage"]["exhaustive"])
        self.assertTrue(any(item["type"] == "unit" for item in result["items"]))

    def test_explore_tool_fails_closed_without_host_model_ledger(self):
        with self.assertRaises(ToolError) as raised:
            _explore_project({"query": "worker"}, self.context)
        self.assertEqual(raised.exception.code, "model_context_required")

    def test_explore_tool_uses_injected_client_and_returns_claims(self):
        snapshot = read_map(self.root, limit=100)
        unit = next(row for row in snapshot["units"] if row["path"] == "README.md"
                    and row["kind"] == "source")
        calls = {"count": 0}

        def complete(messages, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"ok": True, "content": json.dumps(
                    {"action": "read_unit", "unit_id": unit["unit_id"]})}
            return {"ok": True, "content": json.dumps(
                {"action": "finish", "claims": [{
                    "unit_id": unit["unit_id"], "kind": "purpose",
                    "text": "The repository runs a worker.",
                    "quote": "This repository runs a worker."}]})}

        self.context.extra.update(
            model_client=CallableModelClient(complete, budget=llm.token_budget()),
            model_budget=AgentBudget(max_seconds=60))
        result = _explore_project({"query": "purpose", "scope": "question", "limit": 3},
                                  self.context)
        self.assertTrue(result["phase_complete"], result)
        self.assertTrue(result["claims"])
        self.assertFalse(result["coverage"]["exhaustive"])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any(item["type"] == "observation" for item in result["items"]))

    def test_stale_map_file_is_not_served_as_current_answer_evidence(self):
        snapshot = read_map(self.root, limit=100)
        unit = next(row for row in snapshot["units"] if row["path"] == "README.md"
                    and row["kind"] == "source")
        # Build a claim through the same map API.
        from meetingvar.memory.repository_map import read_unit
        ref = {**read_unit(self.root, unit["unit_id"])["source_refs"][0],
               "unit_id": unit["unit_id"]}
        self.assertTrue(store_claims(self.root, [{
            "text": "A current claim.", "kind": "fact", "source_refs": [ref]}])["ok"])
        (self.root / "README.md").write_text("# Demo\n\nChanged without rebuild.\n", encoding="utf-8")
        result = _read_project_map({"limit": 20}, self.context)
        self.assertEqual(result["map_status"], "partial")
        self.assertFalse(any(item.get("type") == "claim" for item in result["items"]))
        self.assertFalse(any(row["path"] == "README.md" for row in result["units"]))


if __name__ == "__main__":
    unittest.main()