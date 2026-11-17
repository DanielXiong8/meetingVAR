"""Conversation store and chat agent tests (model-free)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
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
        (self.root / "meetingvar" / "minutes" / "s1").mkdir(parents=True)

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

    def test_greeting_uses_selected_chat_mode_and_is_persisted(self):
        agent = ChatAgent(project_root=self.root, data_dir=self.data, llm=lambda prompt: "你好")
        result = agent.ask("你好", mode="chat")
        self.assertTrue(result["ok"])
        self.assertEqual(result["message"]["mode"], "chat")
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
        self.assertTrue(result["message"]["source_validated"])
        self.assertFalse(result["message"]["semantic_verified"])
        self.assertEqual(result["message"]["citations"][0]["turn_ids"], ["turn_1"])
        # The new loop request contains the prior assistant turn in its user context.
        self.assertIn("上一轮：项目已启动", json.dumps(script.calls[0][0], ensure_ascii=False))
        self.assertIn("上一轮：项目已启动", json.dumps(script.calls[1][0], ensure_ascii=False))
        loaded = ChatStore(self.root).load(result["conversation_id"])
        self.assertEqual(loaded["messages"][-1]["role"], "assistant")

    def test_recent_meeting_summary_uses_same_tool_loop(self):
        script = ScriptLLM([_content('{"steps":[]}'),
            _tool("get_transcript_window", {"session_id":"s1","turn_ids":["turn_1"]}),
            _content(json.dumps({"answer":"周五交付初版。","citations":[{"kind":"turn","session_id":"s1","turn_ids":["turn_1"],"quote":"周五给初版"}]}))])
        result = ChatAgent(self.root,data_dir=self.data,llm=script).ask("总结最近会议的主要决定")
        self.assertEqual(result["message"]["mode"],"agent")
        self.assertIn("周五交付初版",result["message"]["content"])
        self.assertTrue(result["message"]["citations"])

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
        result = agent.ask("解释一下什么是向量数据库", mode="chat")
        self.assertEqual(result["message"]["mode"], "chat")
        self.assertEqual(result["message"]["content"], "这是一段普通回答。")

    def test_focus_request_is_a_tool_approval_not_a_page(self):
        proposal = {"ui_action":"save_focus","label":"保存关注",
                    "payload":{"items":[{"title":"交付方案是否确认","why":"用户关注"}],"sources":[]},
                    "summary":"确认后保存关注点"}
        script = ScriptLLM([_content('{"steps":[]}'),_tool("prepare_focus",{})])
        with patch("meetingvar.agent.meeting_tools._propose_focus",return_value=proposal):
            agent=ChatAgent(self.root,data_dir=self.data,llm=script)
            result=agent.ask("生成会前关注清单")
        self.assertEqual(result["message"]["actions"][0]["type"],"save_focus")
        self.assertTrue(agent.execute_action(result["message"]["actions"][0]["id"])["ok"])

    def test_facts_request_is_a_tool_approval_not_a_page(self):
        proposal={"ui_action":"save_meeting_facts","label":"写入事实",
                  "payload":{"session_id":"s1","items":[{"title":"周五初版"}]},
                  "summary":"候选已准备，确认后写入"}
        script=ScriptLLM([_content('{"steps":[]}'),_tool("prepare_meeting_facts",{"session_id":"s1"})])
        with patch("meetingvar.agent.meeting_tools._propose_facts",return_value=proposal):
            result=ChatAgent(self.root,data_dir=self.data,llm=script).ask("从最近一场会议提取会后事实")
        self.assertEqual(result["message"]["actions"][0]["type"],"save_meeting_facts")
        self.assertEqual(result["message"]["stop_reason"],"action_pending")

    def test_project_memory_request_uses_registered_memory_tool(self):
        from meetingvar.agent.memory import FactStore
        FactStore(self.root).write(name="周五交付",description="周五交付第一版",
            body="小林负责周五交付第一版。",fact_type="commitment",source_refs=[],confidence=.9,origin_meeting="s1")
        script=ScriptLLM([_content('{"steps":[]}'),_tool("read_fact",{"name":"周五交付"}),
            _content(json.dumps({"answer":"周五交付第一版。","citations":[{"kind":"fact","name":"周五交付"}]}))])
        result=ChatAgent(self.root,data_dir=self.data,llm=script).ask("查看项目记忆")
        self.assertEqual(result["message"]["mode"],"agent")
        self.assertIn("周五交付第一版",result["message"]["content"])

    def test_rag_index_request_is_an_approved_operation(self):
        script=ScriptLLM([_content('{"steps":[]}'),_tool("prepare_project_index",{})])
        agent=ChatAgent(self.root,data_dir=self.data,llm=script)
        result=agent.ask("建立项目知识库索引")
        action=result["message"]["actions"][0]
        self.assertEqual(action["type"],"index_project")
        self.assertEqual(agent.operations.get(action["id"])["status"],"awaiting_approval")
