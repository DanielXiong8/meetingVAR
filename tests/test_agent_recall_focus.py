"""Read-only memory Q&A and pre-meeting focus agent tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.focus import FocusAgent
from meetingvar.agent.memory import FactStore
from meetingvar.agent.recall import MemoryQAAgent
from meetingvar.memory.interest_context import project_context


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_agent_recall_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.store = FactStore(self.project)
        self.store.write(name="采用第二方案", description="会议决定采用第二方案",
                         body="证据：决定采用第二方案，小林负责。", fact_type="decision",
                         source_refs=["turn_2"], confidence=0.9, origin_meeting="go_1")
        self.store.write(name="周五交付", description="小林周五交付第一版",
                         body="证据：周五交付第一版。", fact_type="action-item",
                         source_refs=["turn_3"], confidence=0.8, origin_meeting="go_1")

    def test_grounded_answer_uses_retrieved_fact(self):
        prompts = []
        def llm(prompt):
            prompts.append(prompt)
            return "项目决定采用第二方案，小林负责。[采用第二方案]"
        report = MemoryQAAgent(project_root=self.project, question="第二方案怎么定的", llm=llm).run()
        self.assertTrue(report["ok"])
        self.assertTrue(report["grounded"])
        self.assertEqual(report["citations"], ["采用第二方案"])
        self.assertTrue(any("采用第二方案" in prompt for prompt in prompts))
        self.assertTrue(report["sources"])

    def test_no_facts_does_not_call_model(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        called = []
        report = MemoryQAAgent(project_root=empty, question="有什么决定",
                               llm=lambda prompt: called.append(prompt) or "x").run()
        self.assertTrue(report["ok"])
        self.assertFalse(report["answered"])
        self.assertEqual(called, [])

    def test_unknown_citation_is_flagged(self):
        report = MemoryQAAgent(project_root=self.project, question="第二方案",
                               llm=lambda prompt: "结论 [不存在的事实]").run()
        self.assertTrue(report["answered"])
        self.assertFalse(report["grounded"])
        self.assertIn("unknown_citation", [e["code"] for e in report["errors"]])


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_agent_focus_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        (self.project / "notes.md").write_text(
            "# 星河项目\n目标：本周确定第二方案，小林负责交付。\n风险：测试时间不足。\n",
            encoding="utf-8")
        self.items = [
            {"title": "第二方案是否定稿", "why": "项目材料要求本周确定方案"},
            {"title": "谁负责周五交付", "why": "材料写明小林负责"},
            {"title": "测试时间是否足够", "why": "材料列出测试时间风险"},
        ]

    def agent(self, approved=True, dry_run=False):
        payload = {"focus": self.items}
        return FocusAgent(project_root=self.project, dry_run=dry_run,
                          approval=lambda items: approved,
                          llm=lambda prompt: {"ok": True, "content": json.dumps(payload, ensure_ascii=False)})

    def test_focus_is_saved_and_project_context_includes_it(self):
        report = self.agent().run()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["saved"])
        path = self.project / "meetingvar" / "agent_memory" / "focus.json"
        self.assertTrue(path.exists())
        context = json.loads(project_context(self.project, []))
        self.assertEqual(len(context["focus_items"]), 3)
        self.assertEqual(context["focus_items"][0]["title"], "第二方案是否定稿")

    def test_denied_focus_is_not_saved(self):
        report = self.agent(approved=False).run()
        self.assertFalse(report["ok"])
        self.assertFalse(report["saved"])
        self.assertFalse((self.project / "meetingvar" / "agent_memory" / "focus.json").exists())

    def test_dry_run_does_not_save(self):
        report = self.agent(dry_run=True).run()
        self.assertTrue(report["ok"])
        self.assertFalse(report["saved"])
        self.assertFalse((self.project / "meetingvar" / "agent_memory" / "focus.json").exists())

    def test_no_readable_material_fails(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        report = FocusAgent(project_root=empty, approval=lambda items: True,
                            llm=lambda prompt: {"ok": True, "content": json.dumps({"focus": self.items})}).run()
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["code"], "no_readable_material")

    def test_large_model_reading_does_not_truncate_focus_json(self):
        meetingvar = self.project / "meetingvar"
        meetingvar.mkdir(exist_ok=True)
        large = {"overview": "x" * 3000,
                 "terms": [{"term": f"术语{i}", "why": "y" * 300} for i in range(40)]}
        (meetingvar / "interest_context.json").write_text(
            json.dumps(large, ensure_ascii=False), encoding="utf-8")
        (meetingvar / "agent_memory").mkdir(exist_ok=True)
        (meetingvar / "agent_memory" / "focus.json").write_text(
            json.dumps({"items": self.items}, ensure_ascii=False), encoding="utf-8")
        text = project_context(self.project, [])
        self.assertLessEqual(len(text), 16000)
        parsed = json.loads(text)
        self.assertEqual(len(parsed["focus_items"]), 3)


if __name__ == "__main__":
    unittest.main()