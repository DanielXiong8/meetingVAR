"""Commercial memory v2: FTS, lifecycle, conflicts, chat capture, summaries."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.chat import ChatAgent, ChatStore  # noqa: E402
from meetingvar.agent.memory import FactStore  # noqa: E402
from meetingvar.agent.memory_intent import is_memory_request, propose_memory  # noqa: E402
from meetingvar.agent.postmeeting import quick_candidates  # noqa: E402


class MemoryV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_mem_v2_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.store = FactStore(self.project)

    def test_fts_synonym_and_type_filter(self):
        self.store.write(name="整体交付计划", description="项目上线时间",
                         body="定于11月15日上线，负责人张三", fact_type="decision",
                         origin_meeting="m1")
        self.store.write(name="外部接口风险", description="接口依赖风险",
                         body="外部接口可能延期", fact_type="risk", origin_meeting="m1")
        self.assertEqual(self.store.search("发版日期")[0]["name"], "整体交付计划")
        risks = self.store.search("接口", types=["risk"])
        self.assertEqual([row["name"] for row in risks], ["外部接口风险"])

    def test_version_history_and_single_active_fact(self):
        self.store.write(name="发布计划", description="产品发布日期",
                         body="第一版 11 月发布", fact_type="decision")
        self.store.write(name="发布计划", description="产品发布日期",
                         body="第二版 12 月发布", fact_type="decision")
        fact = self.store.read("发布计划")
        self.assertEqual(fact["version"], 2)
        versions = list((self.store.versions_dir / Path(fact["path"]).stem).glob("v*.md"))
        self.assertEqual(len(versions), 1)
        self.assertEqual(len(self.store.search("发布")), 1)

    def test_conflict_queue_and_resolutions(self):
        self.store.write(name="交付A", description="项目交付时间定在周五",
                         body="负责人张三，周五交付", fact_type="decision",
                         metadata={"owner": "张三"})
        self.store.write(name="交付B", description="项目交付时间改到周一",
                         body="负责人李四，周一交付", fact_type="decision",
                         metadata={"owner": "李四"})
        self.assertEqual(self.store.read("交付B")["status"], "pending-conflict")
        self.assertEqual([row["name"] for row in self.store.search("交付")], ["交付A"])
        self.store.resolve_conflict("交付B", "keep_new")
        self.assertEqual(self.store.read("交付A")["status"], "superseded")
        self.assertEqual(self.store.read("交付B")["status"], "active")
        self.assertEqual([row["name"] for row in self.store.search("交付")], ["交付B"])

    def test_pin_expire_and_delete(self):
        self.store.write(name="决定", description="采用第二方案", body="决定采用第二方案",
                         fact_type="decision")
        self.store.pin("决定")
        self.assertTrue(self.store.read("决定")["pinned"])
        self.store.expire("决定")
        self.assertEqual(self.store.search("第二方案"), [])
        self.assertTrue(self.store.delete("决定", hard=True))
        self.assertEqual(list(self.store.facts_dir.glob("*.md")), [])

    def test_chat_remember_requires_confirmation_then_writes(self):
        agent = ChatAgent(self.project, llm=lambda *a, **k: "unused")
        result = agent.ask("记住：项目周五交付第一版，负责人小林。")
        self.assertEqual(result["message"]["mode"], "memory")
        action_id = result["message"]["actions"][0]["id"]
        done = agent.execute_action(action_id)
        self.assertTrue(done["ok"])
        facts = FactStore(self.project).list_facts()
        self.assertEqual(len(facts), 1)
        self.assertIn("周五", facts[0]["name"])

    def test_quick_candidates_and_conversation_summary(self):
        rows = [{"id": "turn_1", "text": "我们决定采用第二方案，负责人小林，周五交付第一版。"},
                {"id": "turn_2", "text": "测试时间不足是一个风险。"}]
        candidates = quick_candidates(rows)
        self.assertEqual({row["fact_type"] for row in candidates}, {"decision", "risk"})

        store = ChatStore(self.project)
        conversation = store.create()
        for i in range(13):
            store.append(conversation["id"], {"role": "user", "content": f"问题 {i} 交付时间"})
            store.append(conversation["id"], {"role": "assistant", "content": f"回答 {i}"})
        loaded = store.load(conversation["id"])
        self.assertEqual(loaded["summary"], "")
        store.update_summary(conversation["id"], "项目采用第二方案，周五交付", 20)
        hits = store.search_conversations("第二方案")
        self.assertEqual(hits[0]["conversation_id"], conversation["id"])

    def test_memory_intent_pronoun_uses_context(self):
        history = [{"role": "user", "content": "项目周五交付第一版"},
                   {"role": "assistant", "content": "收到"}]
        self.assertTrue(is_memory_request("记住这件事"))
        proposals = propose_memory("记住这件事", history, "c1")
        self.assertEqual(len(proposals), 1)
        self.assertIn("周五交付", proposals[0]["body"])


if __name__ == "__main__":
    unittest.main()