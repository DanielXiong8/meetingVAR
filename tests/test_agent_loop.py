"""Agent loop tests: native tools, JSON fallback, citations, budgets, resume, policy."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.loop import AgentLoop
from meetingvar.agent.policy import ApprovalBroker, Principal
from meetingvar.agent.meeting_tools import build_meeting_registry
from meetingvar.agent.tools import ToolContext, ToolRegistry, ToolSpec


def _usage(prompt=10, completion=5):
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}


def _content(text, *, usage=None):
    return {"ok": True, "content": text, "message": {"role": "assistant", "content": text},
            "tool_calls": [], "finish_reason": "stop", "usage": usage or _usage()}


def _tool_call(name, args, call_id="call_1", *, usage=None):
    call = {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
    return {"ok": True, "content": "", "message": {"role": "assistant", "content": None,
                                                    "tool_calls": [call]},
            "tool_calls": [call], "finish_reason": "tool_calls", "usage": usage or _usage()}


class ScriptLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        return self.responses.pop(0)


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mv_agent_loop_")
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

    def _loop(self, responses, **kwargs):
        llm = ScriptLLM(responses)
        loop = AgentLoop(project_root=self.root, data_dir=self.data,
                         question=kwargs.pop("question", "交付时间变了吗？"),
                         llm=llm, **kwargs)
        return loop, llm

    @staticmethod
    def _plan():
        return _content(json.dumps({"steps": [
            {"goal": "搜索交付时间", "tool": "search_sessions", "why": "找原话"},
            {"goal": "读原始发言", "tool": "get_transcript_window", "why": "核对引用"},
        ]}, ensure_ascii=False))

    @staticmethod
    def _final(*, cite_turn_2=True, invalid=False):
        citations = [{"kind": "turn", "session_id": "s1", "turn_ids": ["turn_1"],
                      "quote": "周五给初版"}]
        if cite_turn_2:
            citations.append({"kind": "turn", "session_id": "s1", "turn_ids": ["turn_2"],
                              "quote": "下周一给"})
        if invalid:
            citations = [{"kind": "turn", "session_id": "s1", "turn_ids": ["turn_99"],
                          "quote": "不存在"}]
        return _content(json.dumps({
            "answer": "交付时间从周五改到了下周一。" if cite_turn_2 else "周五有交付安排。",
            "citations": citations, "unsupported": [], "confidence": 0.9,
        }, ensure_ascii=False))

    def test_native_tool_calling_and_grounded_final(self):
        script = [
            self._plan(),
            _tool_call("search_sessions", {"query": "周五"}),
            _tool_call("get_transcript_window",
                       {"session_id": "s1", "turn_ids": ["turn_1", "turn_2"]}, "call_2"),
            self._final(),
        ]
        loop, llm = self._loop(script, mode="native", max_steps=6)
        report = loop.run()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["grounded"], report)
        self.assertEqual(len(report["citations"]), 2)
        self.assertEqual(report["tool_calls"], 2)
        self.assertEqual(report["stop_reason"], "completed")
        self.assertEqual(loop.mode, "native")
        # The tool contract is actually sent to the model.
        self.assertTrue(llm.calls[1]["tools"])
        self.assertEqual(llm.calls[1]["tools"][0]["type"], "function")

    def test_json_action_fallback_is_a_real_react_loop(self):
        script = [
            self._plan(),
            _content(json.dumps({"action": "tool", "tool": "search_sessions",
                                 "arguments": {"query": "周五"},
                                 "thought": "先找原话"}, ensure_ascii=False)),
            _content(json.dumps({"action": "final",
                                 "answer": "交付从周五改到下周一。",
                                 "citations": [{"kind": "turn", "session_id": "s1",
                                                "turn_ids": ["turn_1"], "quote": "周五给初版"}],
                                 "confidence": 0.8}, ensure_ascii=False)),
        ]
        loop, _ = self._loop(script, mode="json", max_steps=4)
        report = loop.run()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["grounded"], report)
        self.assertEqual(report["mode"], "json")
        self.assertEqual(report["tool_calls"], 1)
        self.assertTrue(any("观察结果" in m["content"] for m in loop.messages
                            if m.get("role") == "user"))

    def test_invalid_citation_requests_correction_and_reports_ungrounded(self):
        script = [
            self._plan(),
            _tool_call("search_sessions", {"query": "周五"}),
            self._final(invalid=True),
            _content(json.dumps({"answer": "根据现有证据无法确认改期。",
                                 "citations": [], "confidence": 0.2}, ensure_ascii=False)),
            _content(json.dumps({"answer": "根据现有证据无法确认改期。",
                                 "citations": [], "confidence": 0.2}, ensure_ascii=False)),
            _content(json.dumps({"answer": "根据现有证据无法确认改期。",
                                 "citations": [], "confidence": 0.2}, ensure_ascii=False)),
            _content(json.dumps({"answer": "根据现有证据无法确认改期。",
                                 "citations": [], "confidence": 0.2}, ensure_ascii=False)),
        ]
        loop, _ = self._loop(script, mode="native", max_steps=6)
        report = loop.run()
        self.assertEqual(report["stop_reason"], "completed_no_evidence")
        self.assertFalse(report["grounded"])
        self.assertFalse(report["answered"])

    def test_pending_ui_action_ends_run_instead_of_repeating_tools(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(name="prepare_focus", description="propose",
                                   input_schema={"type": "object", "properties": {}}),
                          lambda args, ctx: {
                              "ui_action": "save_focus", "label": "保存 7 条会前关注",
                              "payload": {"items": [{"title": "x"}]},
                              "summary": "会前关注清单已生成，确认后写入项目记忆。"})
        script = [
            self._plan(),
            _tool_call("prepare_focus", {}, "call_1"),
            _tool_call("prepare_focus", {}, "call_2"),
        ]
        loop, _ = self._loop(script, mode="native", max_steps=8)
        loop.registry = registry
        report = loop.run()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["stop_reason"], "action_pending")
        self.assertEqual(report["tool_calls"], 1)
        self.assertEqual(len(report["ui_actions"]), 1)
        self.assertIn("确认", report["answer"])

    def test_identical_tool_call_is_not_executed_twice(self):
        script = [
            self._plan(),
            _tool_call("search_sessions", {"query": "周五"}, "call_1"),
            _tool_call("search_sessions", {"query": "周五"}, "call_2"),
            _tool_call("search_sessions", {"query": "周五"}, "call_3"),
        ]
        loop, _ = self._loop(script, mode="native", max_steps=6)
        report = loop.run()
        self.assertEqual(report["stop_reason"], "repeated_tool_call")
        self.assertEqual(report["tool_calls"], 1)

    def test_step_budget_stops_the_loop(self):
        script = [
            self._plan(),
            _tool_call("search_sessions", {"query": "周五"}),
        ]
        loop, _ = self._loop(script, mode="native", max_steps=1, max_continuations=0)
        report = loop.run()
        self.assertEqual(report["stop_reason"], "step_budget_exhausted")
        self.assertEqual(report["tool_calls"], 1)
        self.assertFalse(report["ok"])
        self.assertNotIn("预算", report["answer"])
        self.assertIn("周五", report["answer"])

    def test_unlimited_steps_do_not_hit_step_budget(self):
        loop, _ = self._loop([], mode="native", max_steps=0)
        loop.budget.steps = 50
        can, reason = loop.budget.can_step()
        self.assertTrue(can, reason)
        self.assertEqual(reason, "")

    def test_step_budget_auto_continues_with_compacted_observations(self):
        script = [
            self._plan(),
            _tool_call("search_sessions", {"query": "周五"}),
            self._final(cite_turn_2=False),
        ]
        loop, _ = self._loop(script, mode="native", max_steps=1, max_continuations=1)
        report = loop.run()
        self.assertTrue(report["ok"], report)
        self.assertEqual(loop._continuations, 1)
        self.assertTrue(any("自动续跑" in str(m.get("content", "")) for m in loop.messages))
        self.assertTrue(any("早期观察摘要" in str(m.get("content", "")) for m in loop.messages))
        self.assertIn("search_sessions", loop._observation_summary)
        self.assertEqual(report["stop_reason"], "completed")

    def test_resume_continues_from_persisted_tool_observation(self):
        first_script = [
            self._plan(),
            _tool_call("search_sessions", {"query": "周五"}),
        ]
        loop, _ = self._loop(first_script, mode="native", max_steps=1, max_continuations=0)
        first = loop.run()
        self.assertEqual(first["stop_reason"], "step_budget_exhausted")
        task_id = first["task_id"]

        resumed_script = [self._final(cite_turn_2=False)]
        resumed, _ = self._loop(resumed_script, mode="native", max_steps=4)
        report = resumed.run(resume_task_id=task_id)
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["grounded"])
        self.assertEqual(report["task_id"], task_id)
        self.assertGreaterEqual(report["steps"], 1)

    def test_project_scope_is_enforced_for_tools(self):
        registry = build_meeting_registry()
        other = self.root / "other"
        principal = Principal(actor_id="u", roles=frozenset({"agent:read"}),
                              project_scopes=(str(other.resolve()),))
        context = ToolContext(project_root=self.root, principal=principal,
                              extra={"catalog": __import__(
                                  "meetingvar.agent.sessions", fromlist=["SessionCatalog"]
                              ).SessionCatalog(self.data)})
        with self.assertRaises(Exception) as caught:
            registry.invoke("search_sessions", {"query": "周五"}, context=context)
        self.assertEqual(getattr(caught.exception, "code", ""), "project_forbidden")

    def test_approval_token_is_one_time_and_argument_bound(self):
        broker = ApprovalBroker(ttl_seconds=60)
        registry = build_meeting_registry()
        spec = ToolSpec(name="write_test", description="test write", read_only=False,
                        requires_approval=True, side_effect_scope="project",
                        input_schema={"type": "object", "additionalProperties": False,
                                      "properties": {"value": {"type": "string"}},
                                      "required": ["value"]})
        registry.register(spec, lambda args, context: {"value": args["value"]})
        principal = Principal(actor_id="u", roles=frozenset({"agent:read", "agent:write"}),
                              project_scopes=(str(self.root.resolve()),))
        args = {"value": "ok"}
        token = broker.issue(tool="write_test", actor_id="u", args=args, scope="project")
        context = ToolContext(project_root=self.root, principal=principal,
                              approval_broker=broker, approval_token=token.token)
        result = registry.invoke("write_test", args, context=context)
        self.assertEqual(result.value, {"value": "ok"})
        with self.assertRaises(Exception):
            registry.invoke("write_test", args, context=context)
        wrong = broker.issue(tool="write_test", actor_id="u", args=args, scope="project")
        with self.assertRaises(Exception):
            registry.invoke("write_test", {"value": "different"},
                            context=ToolContext(project_root=self.root, principal=principal,
                                                approval_broker=broker,
                                                approval_token=wrong.token))