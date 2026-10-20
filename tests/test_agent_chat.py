"""Conversation store and chat agent tests (model-free)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.chat import ChatAgent, ChatStore


def _content(text):
    return {"ok": True, "content": text, "message": {"role": "assistant", "content": text},
            "tool_calls": [], "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _tool(name, args, call_id="call_1"):
    call = {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
    return {"ok": True, "content": "", "message": {"role": "assistant", "content": None,
                                                    "tool_calls": [call]},
            "tool_calls": [call], "finish_reason": "tool_calls",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class ScriptLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        return self.responses.pop(0)


class ChatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mv_chat_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        pool = self.data / "pool" / "s1"
        pool.mkdir(parents=True)
        (pool / "transcript.json").write_text(json.dumps({
            "session_id": "s1", "clips": [],
            "transcript": [
                {"ts": 1, "speaker": "spk0", "text": "周五给初版"},
                {"ts": 2, "speaker": "spk0", "text": "下周一给"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "meetingvar" / "agent_memory").mkdir(parents=True)

    def test_store_create_append_list_and_load(self):
        store = ChatStore(self.root)
        conversation = store.create()
        cid = conversation["id"]
        store.append(cid, {"role": "user", "content": "项目进度如何？"})
        store.append(cid, {"role": "assistant", "content": "目前有两条事实。"})
        rows = store.list()
        self.assertEqual(rows[0]["id"], cid)
        self.assertEqual(rows[0]["title"], "项目进度如何？")
        loaded = store.load(cid)
        self.assertEqual(len(loaded["messages"]), 2)
        self.assertEqual(loaded["messages"][1]["content"], "目前有两条事实。")

    def test_greeting_is_local_and_persisted(self):
        agent = ChatAgent(project_root=self.root, data_dir=self.data)
        result = agent.ask("你好")
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"]["mode"], "greeting")
        loaded = ChatStore(self.root).load(result["conversation_id"])
        self.assertEqual([m["role"] for m in loaded["messages"]], ["user", "assistant"])

    def test_meeting_question_uses_tools_and_history(self):
        script = ScriptLLM([
            _content(json.dumps({"steps": [{"goal": "搜索交付", "tool": "search_sessions",
                                            "why": "找原话"}]}, ensure_ascii=False)),
            _tool("search_sessions", {"query": "周五"}),
            _content(json.dumps({
                "answer": "周五有交付安排。",
                "citations": [{"kind": "turn", "session_id": "s1",
                               "turn_ids": ["turn_1"], "quote": "周五给初版"}],
                "confidence": 0.9,
            }, ensure_ascii=False)),
        ])
        agent = ChatAgent(project_root=self.root, data_dir=self.data, llm=script)
        # Seed a prior turn so history is actually sent to the loop.
        store = ChatStore(self.root)
        store.append(agent.conversation_id,
                     {"role": "assistant", "content": "上一轮：项目已启动。"})
        agent.conversation = store.load(agent.conversation_id)
        result = agent.ask("周五的交付怎么安排？")
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"]["mode"], "agent")
        self.assertTrue(result["message"]["grounded"])
        self.assertEqual(result["message"]["citations"][0]["turn_ids"], ["turn_1"])
        # The new loop request contains the prior assistant turn in its user context.
        self.assertIn("上一轮：项目已启动", json.dumps(script.calls[0][0], ensure_ascii=False))
        loaded = ChatStore(self.root).load(result["conversation_id"])
        self.assertEqual(loaded["messages"][-1]["role"], "assistant")

    def test_recent_meeting_summary_uses_digest_skill(self):
        script = ScriptLLM([
            _content(json.dumps({
                "summary": "讨论了交付安排。",
                "decisions": [{"title": "周五交付初版", "evidence_turns": ["turn_1"]}],
                "actions": [], "open_questions": [],
            }, ensure_ascii=False))
        ])
        agent = ChatAgent(project_root=self.root, data_dir=self.data, llm=script)
        result = agent.ask("总结最近会议的主要决定")
        self.assertEqual(result["message"]["mode"], "skill")
        self.assertIn("周五交付初版", result["message"]["content"])
        self.assertTrue(result["message"]["citations"])
        self.assertEqual(result["message"]["citations"][0]["turn_ids"], ["turn_1"])

    def test_chat_action_start_meeting_is_ui_only(self):
        agent = ChatAgent(project_root=self.root, data_dir=self.data)
        action = {"id": "a" * 32, "type": "start_meeting", "label": "开始会议",
                  "payload": {}, "status": "pending", "created_at": "",
                  "result": ""}
        agent.store.add_actions(agent.conversation_id, [action])
        result = agent.execute_action(action["id"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["ui_only"])
        self.assertEqual(result["action"], "start_meeting")
        again = agent.execute_action(action["id"])
        self.assertFalse(again["ok"], "同一审批操作不能执行两次")

    def test_chat_action_save_focus_writes_project_memory(self):
        agent = ChatAgent(project_root=self.root, data_dir=self.data)
        action = {"id": "b" * 32, "type": "save_focus", "label": "保存关注",
                  "payload": {"items": [{"title": "确认方案", "why": "项目文档"}],
                              "sources": ["README.md"]},
                  "status": "pending", "created_at": "", "result": ""}
        agent.store.add_actions(agent.conversation_id, [action])
        result = agent.execute_action(action["id"])
        self.assertTrue(result["ok"], result)
        focus = self.root / "meetingvar" / "agent_memory" / "focus.json"
        self.assertTrue(focus.exists())
        self.assertIn("确认方案", focus.read_text(encoding="utf-8"))

    def test_chat_action_save_terms_merges_keep_terms(self):
        path = self.root / "meetingvar" / "terms.txt"
        path.write_text("#!keep 旧词\n", encoding="utf-8")
        agent = ChatAgent(project_root=self.root, data_dir=self.data)
        action = {"id": "c" * 32, "type": "save_terms", "label": "写入词表",
                  "payload": {"terms": ["新词", "旧词"]}, "status": "pending",
                  "created_at": "", "result": ""}
        agent.store.add_actions(agent.conversation_id, [action])
        result = agent.execute_action(action["id"])
        self.assertTrue(result["ok"], result)
        text = path.read_text(encoding="utf-8")
        self.assertIn("#!keep 旧词", text)
        self.assertIn("#!keep 新词", text)

    def test_general_chat_has_no_tools(self):
        script = ScriptLLM(["这是一段普通回答。"])
        agent = ChatAgent(project_root=self.root, data_dir=self.data, llm=script)
        result = agent.ask("解释一下什么是向量数据库")
        self.assertEqual(result["message"]["mode"], "chat")
        self.assertEqual(result["message"]["content"], "这是一段普通回答。")