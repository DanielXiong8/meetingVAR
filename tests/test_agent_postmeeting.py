"""Post-meeting action-item agent tests: deterministic verification and approval."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.postmeeting import PostMeetingAgent, load_transcript


def transcript(path: Path):
    turns = [
        {"ts": 1.0, "speaker": "主持人", "text": "我们讨论一下上线方案。"},
        {"ts": 2.0, "speaker": "小林", "text": "决定采用第二个方案，由我负责。"},
        {"ts": 3.0, "speaker": "小林", "text": "周五交付第一版。"},
    ]
    path.write_text(json.dumps({"transcript": turns}, ensure_ascii=False), encoding="utf-8")
    return load_transcript(path)


def fake_llm(actions):
    def call(prompt):
        return {"ok": True, "content": json.dumps({"actions": actions}, ensure_ascii=False)}
    return call


class PostMeetingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_agent_post_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.turns = transcript(self.root / "transcript.json")
        self.action = {
            "title": "周五交付第一版", "owner": "小林", "due": "周五",
            "evidence_turn_ids": ["turn_2", "turn_3"], "confidence": 0.9,
        }

    def agent(self, actions=None, approved=True, dry_run=False, llm=None):
        return PostMeetingAgent(
            session_id="go_test", transcript=self.turns, project_root=self.project,
            approval=lambda action, evidence: approved, dry_run=dry_run,
            llm=llm or fake_llm(actions if actions is not None else [self.action]),
            max_chars=5000)

    def test_verified_and_approved_action_is_written_with_evidence(self):
        agent = self.agent()
        report = agent.run()
        self.assertTrue(report["ok"], report)
        self.assertEqual((report["candidates"], report["verified"], report["approved"], report["written"]),
                         (1, 1, 1, 1))
        fact = agent.fact_store.read("周五交付第一版")
        self.assertEqual(fact["metadata"]["origin_meeting"], "go_test")
        self.assertIn("turn_2", fact["body"])
        self.assertIn("小林", fact["body"])
        self.assertIn("周五交付第一版", agent.fact_store.index())
        self.assertTrue(any(row["event"] == "task_completed" for row in agent.trace.tail(50)))

    def test_duplicate_run_updates_one_fact(self):
        agent = self.agent()
        first = agent.run()
        second = agent.run()
        self.assertEqual(first["written"], 1)
        self.assertEqual(second["written"], 1)
        fact = agent.fact_store.read("周五交付第一版")
        self.assertEqual(fact["version"], 1, "idempotent repeat must not create a second fact")
        index_lines = [line for line in agent.fact_store.index().splitlines() if line.startswith("- ")]
        self.assertEqual(len(index_lines), 1)

    def test_denied_approval_writes_nothing(self):
        agent = self.agent(approved=False)
        report = agent.run()
        self.assertEqual(report["approved"], 0)
        self.assertEqual(report["written"], 0)
        self.assertFalse(agent.fact_store.facts_dir.exists())

    def test_dry_run_verifies_but_does_not_write(self):
        agent = self.agent(dry_run=True)
        report = agent.run()
        self.assertEqual((report["verified"], report["written"]), (1, 0))
        self.assertFalse(agent.fact_store.facts_dir.exists())

    def test_unknown_evidence_is_rejected_before_write(self):
        bad = dict(self.action, evidence_turn_ids=["turn_99"])
        agent = self.agent(actions=[bad])
        report = agent.run()
        self.assertTrue(report["ok"])
        self.assertEqual(report["candidates"], 0)
        self.assertEqual(report["written"], 0)
        self.assertFalse(agent.fact_store.facts_dir.exists())

    def test_all_fact_types_are_extracted_and_verified(self):
        payload = {
            "actions": [self.action],
            "decisions": [{"title": "采用第二方案", "rationale": "第一方案风险高",
                           "evidence_turn_ids": ["turn_2"], "confidence": 0.8}],
            "commitments": [{"title": "周五交付", "owner": "小林", "due": "周五",
                             "evidence_turn_ids": ["turn_3"], "confidence": 0.9}],
            "risks": [{"title": "时间不足", "severity": "medium",
                       "evidence_turn_ids": ["turn_3"], "confidence": 0.6}],
            "open_questions": [{"title": "是否增加测试", "evidence_turn_ids": ["turn_1"],
                                "confidence": 0.5}],
        }
        agent = self.agent(llm=lambda prompt: {"ok": True, "content": json.dumps(payload)})
        report = agent.run()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["written"], 5)
        self.assertEqual(report["written_by_type"], {
            "action-item": 1, "decision": 1, "commitment": 1, "risk": 1, "open-question": 1})
        facts = agent.fact_store.list_facts()
        types = sorted(fact["metadata"]["type"] for fact in facts)
        self.assertEqual(types, ["action-item", "commitment", "decision", "open-question", "risk"])

    def test_extract_retries_shorter_chunks_on_context_budget(self):
        turns = [{"id": f"turn_{i}", "ts": i, "speaker": "A",
                  "text": "讨论项目方案与交付。" * 20} for i in range(1, 80)]
        calls = []

        def llm(prompt):
            calls.append(len(prompt))
            if len(prompt) > 7000:
                return {"ok": False, "error_code": "context_budget", "error": "too large"}
            return {"ok": True, "content": json.dumps({"actions": [self.action]}, ensure_ascii=False)}

        agent = PostMeetingAgent(session_id="ctx", transcript=turns, project_root=self.project,
                                 dry_run=True, approval=lambda item, evidence: True,
                                 llm=llm, max_chars=12000)
        report = agent.preview()
        self.assertTrue(report["ok"], report)
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreater(calls[0], 7000)

    def test_invalid_output_reports_error_code(self):
        agent = self.agent(llm=lambda prompt: {"ok": True, "content": "not json"})
        report = agent.preview()
        self.assertFalse(report["ok"])
        self.assertEqual(report["error_code"], "invalid_output")

    def test_preview_then_selective_save_writes_only_selected(self):
        second = {"title": "更新项目文档", "owner": "小林", "due": "",
                  "evidence_turn_ids": ["turn_1"], "confidence": 0.7}
        agent = self.agent(actions=[self.action, second])
        preview = agent.preview()
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(len(preview["items"]), 2)
        self.assertFalse(agent.fact_store.facts_dir.exists())
        saved = agent.save_items([preview["items"][0]])
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["written"], 1)
        self.assertEqual(len(agent.fact_store.list_facts()), 1)
        self.assertEqual(agent.fact_store.list_facts()[0]["name"], "周五交付第一版")

    def test_llm_failure_writes_nothing_and_marks_failed(self):
        agent = self.agent(llm=lambda prompt: {"ok": False, "error_code": "truncated"})
        report = agent.run()
        self.assertFalse(report["ok"])
        self.assertEqual(report["written"], 0)
        self.assertTrue(report["errors"])
        self.assertFalse(agent.fact_store.facts_dir.exists())

    def test_transcript_loader_rejects_non_array(self):
        path = self.root / "bad.json"
        path.write_text(json.dumps({"transcript": "no"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_transcript(path)

    def test_cli_requires_explicit_approval_for_json_mode(self):
        from types import SimpleNamespace
        from meetingvar import cli, config
        with patch.object(config, "POOL_DIR", self.root / "pool"):
            (self.root / "pool" / "go_cli").mkdir(parents=True)
            (self.root / "pool" / "go_cli" / "transcript.json").write_text(
                json.dumps({"transcript": [{"ts": 1, "speaker": "A", "text": "测试"}]}),
                encoding="utf-8")
            args = SimpleNamespace(session="go_cli", project=str(self.project), dry_run=False,
                                   yes=False, json=True, max_chars=5000)
            with self.assertRaises(SystemExit):
                cli.cmd_agent_actions(args)

    def test_cli_yes_mode_writes_action_item(self):
        from types import SimpleNamespace
        from meetingvar import cli, config
        self.project.mkdir(parents=True, exist_ok=True)
        with patch.object(config, "POOL_DIR", self.root / "pool"), \
             patch("meetingvar.llm.chat_result", return_value={
                 "ok": True, "content": json.dumps({"actions": [self.action]}, ensure_ascii=False)}):
            (self.root / "pool" / "go_cli2").mkdir(parents=True)
            (self.root / "pool" / "go_cli2" / "transcript.json").write_text(
                json.dumps({"transcript": [
                    {"ts": 1, "speaker": "主持人", "text": "讨论方案"},
                    {"ts": 2, "speaker": "小林", "text": "决定采用第二个方案，由我负责。"},
                    {"ts": 3, "speaker": "小林", "text": "周五交付第一版。"},
                ]}, ensure_ascii=False), encoding="utf-8")
            args = SimpleNamespace(session="go_cli2", project=str(self.project), dry_run=False,
                                   yes=True, json=True, max_chars=5000)
            cli.cmd_agent_actions(args)
        fact = self.project / "meetingvar" / "agent_memory" / "facts"
        self.assertEqual(len(list(fact.glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()