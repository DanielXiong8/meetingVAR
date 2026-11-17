"""Bounded iterative exploration: map -> read targets -> claims, never exhaustive."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import llm
from meetingvar.agent.policy import AgentBudget
from meetingvar.memory.repository_explorer import explore, select_targets
from meetingvar.memory.repository_map import build_map, read_map
from meetingvar.model_client import CallableModelClient


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return {"ok": False, "error_code": "output_budget_exhausted",
                "error": "预算已耗尽", "retryable": False}


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mv_explorer_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "README.md").write_text(
            "# Demo\n\nThis repository runs a worker.\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text(
            "def run_worker(value):\n    return value\n", encoding="utf-8")
        build_map(self.root)
        self.readme = next(row for row in read_map(self.root, limit=100)["units"]
                           if row["path"] == "README.md" and row["kind"] == "source")

    def client(self, responses):
        return CallableModelClient(lambda messages, **kwargs: script.complete(messages, **kwargs),
                                   budget=llm.token_budget())

    def test_targets_prefer_rules_and_entrypoints_over_prefix(self):
        onboarding = select_targets(self.root, "", limit=4)
        paths = [unit["path"] for unit in onboarding["selected"]]
        self.assertIn("README.md", paths)
        self.assertIn("src/main.py", paths)
        self.assertEqual(len(paths), len(set(paths)))
        queried = select_targets(self.root, "worker", limit=4)
        self.assertIn("src/main.py", [unit["path"] for unit in queried["selected"]])

    def test_explore_reads_then_finishes_with_verified_claim(self):
        global script
        script = ScriptedClient([
            {"ok": True, "content": json.dumps({"action": "read_unit",
                                                "unit_id": self.readme["unit_id"]})},
            {"ok": True, "content": json.dumps({"action": "finish", "claims": [{
                "unit_id": self.readme["unit_id"], "kind": "purpose",
                "text": "The repository runs a worker.",
                "quote": "This repository" + chr(10) + chr(10) + "runs a worker."}]})},
        ])
        result = explore(self.root, limit=3, model_client=self.client(None),
                         model_budget=AgentBudget(max_seconds=60))
        self.assertTrue(result["phase_complete"], result)
        self.assertEqual(len(result["claims"]), 1)

    def test_invalid_action_is_retried_before_giving_up(self):
        global script
        script = ScriptedClient([
            {"ok": True, "content": "I think I should read the README next."},
            {"ok": True, "content": json.dumps({"action": "read_unit",
                                                "unit_id": self.readme["unit_id"]})},
            {"ok": True, "content": json.dumps({"action": "finish", "claims": [{
                "unit_id": self.readme["unit_id"], "kind": "purpose",
                "text": "The repository runs a worker.",
                "quote": "This repository runs a worker."}]})},
        ])
        result = explore(self.root, limit=5, model_client=self.client(None),
                         model_budget=AgentBudget(max_seconds=60))
        self.assertTrue(result["phase_complete"], result)
        self.assertFalse(any(error["code"] == "invalid_explorer_action"
                             for error in result["errors"]))

    def test_two_invalid_actions_pause_with_progress(self):
        global script
        script = ScriptedClient([
            {"ok": True, "content": "not json"},
            {"ok": True, "content": "still not json"},
        ])
        result = explore(self.root, limit=3, model_client=self.client(None),
                         model_budget=AgentBudget(max_seconds=60))
        self.assertEqual(result["exploration_status"], "paused_model")
        self.assertFalse(result["complete"])

    def test_no_model_returns_mapped_awaiting_model_without_side_effects(self):
        result = explore(self.root, limit=2)
        self.assertEqual(result["exploration_status"], "awaiting_model")
        self.assertEqual(result["claims"], [])
        self.assertFalse(result["complete"])
        self.assertFalse(result["coverage"]["exhaustive"])

    def test_budget_stop_preserves_pending_and_checkpoint(self):
        global script
        script = ScriptedClient([
            {"ok": True, "content": json.dumps({"action": "read_unit",
                                                "unit_id": self.readme["unit_id"]})},
            {"ok": False, "error_code": "output_budget_exhausted",
             "error": "本任务累计输出预算已用尽", "retryable": False},
        ])
        checkpoints = []
        result = explore(self.root, limit=5, model_client=self.client(None),
                         model_budget=AgentBudget(max_seconds=60),
                         checkpoint=checkpoints.append)
        self.assertEqual(result["exploration_status"], "paused_budget")
        self.assertFalse(result["phase_complete"])
        self.assertGreater(result["coverage"]["pending"], 0)
        self.assertTrue(any(error["code"] == "output_budget_exhausted"
                            for error in result["errors"]))
        self.assertEqual(checkpoints[-1]["exploration_status"], "paused_budget")


if __name__ == "__main__":
    unittest.main()
