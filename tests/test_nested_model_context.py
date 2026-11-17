"""No-network tool model injection tests; mechanism checks, not answer quality."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
with patch("meetingvar.env.load_local_env"):
    from meetingvar import config
    from meetingvar.llm import error_result, TokenBudget
    from meetingvar.agent.focus import FocusAgent
    from meetingvar.agent.postmeeting import PostMeetingAgent, PostMeetingError
    from meetingvar.agent.policy import AgentBudget
    from meetingvar.agent.tools import ToolContext, ToolError
    from meetingvar.agent.loop import AgentLoop
    from meetingvar.agent.meeting_tools import build_meeting_registry
    from meetingvar.agent.sessions import SessionCatalog

from test_system_model_context import FakeClient, content


def focus_reply():
    return content(json.dumps({"focus": [{"title": "确认交付责任", "why": "项目材料提出交付安排"}]}, ensure_ascii=False))


def facts_reply():
    return content(json.dumps({"actions": [{"title": "交付初版", "owner": "小林", "due": "周五",
        "evidence_turn_ids": ["turn_1"], "confidence": 0.8}]}, ensure_ascii=False))


class NestedModelContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mv-nested-model-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "brief.txt").write_text("小林周五交付初版，会上确认安排。", encoding="utf-8")
        self.data = self.root / "data"
        pool = self.data / "pool" / "s1"
        pool.mkdir(parents=True)
        (pool / "transcript.json").write_text(json.dumps({"transcript": [
            {"text": "小林周五交付初版。", "speaker": "小林", "ts": 1}]}), encoding="utf-8")
        (self.root / "meetingvar" / "minutes" / "s1").mkdir(parents=True)
        self.turns = [{"id": "turn_1", "text": "小林周五交付初版。", "speaker": "小林", "ts": 1}]
        for target in ("meetingvar.llm.chat_result", "meetingvar.llm.chat_messages",
                       "meetingvar.llm.chat_messages_stream", "meetingvar.llm.urllib.request.urlopen",
                       "meetingvar.llm.urllib.request.build_opener"):
            guard = patch(target, side_effect=AssertionError("global provider bypass"))
            guard.start(); self.addCleanup(guard.stop)

    def context(self, client, ledger):
        return ToolContext(project_root=self.root, extra={"catalog": SessionCatalog(self.data, project_root=self.root),
            "model_client": client, "model_budget": ledger})

    def test_registered_focus_uses_exact_caller_ledger_and_client(self):
        fake = FakeClient([focus_reply()]); ledger = AgentBudget()
        ledger.consume({"prompt_tokens": 90, "completion_tokens": 40})
        result = build_meeting_registry().invoke("prepare_focus", {}, context=self.context(fake, ledger))
        self.assertEqual(result.value["ui_action"], "save_focus")
        self.assertEqual((ledger.input_tokens, ledger.output_tokens), (107, 51))
        self.assertEqual(result.value["budget"]["input_tokens"], ledger.input_tokens)
        self.assertEqual(result.value["usage"]["model_calls"], 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse((self.root / "meetingvar" / "agent_memory" / "focus.json").exists())

    def test_registered_facts_uses_same_ledger_and_preserves_coverage(self):
        fake = FakeClient([facts_reply()]); ledger = AgentBudget()
        ledger.consume({"prompt_tokens": 70, "completion_tokens": 30})
        result = build_meeting_registry().invoke("prepare_meeting_facts", {"session_id": "s1"}, context=self.context(fake, ledger))
        self.assertEqual(result.value["ui_action"], "save_meeting_facts")
        self.assertEqual(ledger.input_tokens, 87)
        self.assertEqual(ledger.model_calls, 1)
        self.assertTrue(result.value["coverage"]["complete"])
        self.assertEqual(result.value["coverage"]["processed"], 1)

    def test_missing_tool_model_context_fails_closed(self):
        with self.assertRaises(ToolError) as caught:
            build_meeting_registry().invoke("prepare_focus", {}, context=ToolContext(project_root=self.root))
        self.assertEqual(caught.exception.code, "model_context_required")

    def test_nested_auth_error_stays_visible_without_retry_or_write(self):
        fake = FakeClient([error_result("auth")]); ledger = AgentBudget()
        result = build_meeting_registry().invoke("prepare_focus", {}, context=self.context(fake, ledger))
        self.assertFalse(result.value["ok"])
        self.assertEqual(result.value["error_code"], "auth")
        self.assertEqual(result.value["errors"][0]["code"], "auth")
        self.assertGreater(result.value["budget"]["input_tokens"], 0)
        self.assertEqual(ledger.output_tokens, fake.budget().max_output_tokens)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result.evidence["status"], "error")

    def test_nested_admission_failure_is_visible_and_never_calls_fake(self):
        fake = FakeClient([]); ledger = AgentBudget(max_input_tokens=10)
        result = build_meeting_registry().invoke("prepare_meeting_facts", {"session_id": "s1"}, context=self.context(fake, ledger))
        self.assertFalse(result.value["ok"])
        self.assertEqual(result.value["error_code"], "input_budget_exhausted")
        self.assertEqual(result.value["budget"]["model_calls"], 0)
        self.assertFalse(fake.calls)

    def test_nonretryable_truncation_does_not_restart_smaller_extraction(self):
        for task in ("focus", "facts"):
            with self.subTest(task=task):
                fake = FakeClient([error_result("truncated", usage={"prompt_tokens": 3, "completion_tokens": 5})])
                ledger = AgentBudget()
                agent = (FocusAgent(project_root=self.root, model_client=fake, model_budget=ledger, dry_run=True)
                    if task == "focus" else PostMeetingAgent(session_id="s1", transcript=self.turns, project_root=self.root,
                        model_client=fake, model_budget=ledger, dry_run=True))
                report = agent.preview()
                self.assertEqual(report["error_code"], "truncated")
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual((ledger.input_tokens, ledger.output_tokens), (3, 5))

    def test_second_chunk_failure_keeps_first_cost_and_coverage(self):
        fake = FakeClient([facts_reply(), error_result("auth", usage={"prompt_tokens": 4, "completion_tokens": 0})])
        ledger = AgentBudget()
        turns = [dict(self.turns[0], text="首段" * 600),
                 {"id": "turn_2", "speaker": "乙", "ts": 2, "text": "次段" * 600}]
        report = PostMeetingAgent(session_id="s1", transcript=turns, project_root=self.root,
            max_chars=1000, model_client=fake, model_budget=ledger).preview()
        self.assertFalse(report["ok"])
        self.assertEqual(report["error_code"], "auth")
        self.assertEqual(report["coverage"]["processed"], 1)
        self.assertEqual(report["coverage"]["failed"], 1)
        self.assertFalse(report["coverage"]["complete"])
        self.assertTrue(report["partial_candidates"])
        self.assertEqual(ledger.input_tokens, 21)
        self.assertEqual(len(fake.calls), 2)

    def test_conflict_helper_uses_injected_callback_and_fails_closed(self):
        fake = FakeClient([error_result("quota", usage={"prompt_tokens": 6, "completion_tokens": 0})])
        ledger = AgentBudget()
        agent = PostMeetingAgent(session_id="s1", transcript=self.turns, project_root=self.root,
            model_client=fake, model_budget=ledger)
        item = {"fact_type": "action-item", "title": "交付初版", "owner": "小林", "due": "周五",
            "rationale": "", "severity": "", "confidence": 0.8, "evidence_turn_ids": ["turn_1"]}
        with patch.object(agent.fact_store, "related_candidates", return_value=[{"name": "old", "body": "old date"}]), patch.object(agent.fact_store, "write") as write:
            report = agent.save_items([item])
        self.assertEqual(report["error_code"], "quota")
        self.assertFalse(report["ok"])
        self.assertEqual(ledger.input_tokens, 6)
        write.assert_not_called()
        self.assertEqual(len(fake.calls), 1)

    def test_successful_conflict_check_settles_same_ledger(self):
        fake = FakeClient([content('{"conflicts":["old"],"reason":"date differs"}')])
        ledger = AgentBudget()
        agent = PostMeetingAgent(session_id="s1", transcript=self.turns, project_root=self.root,
            model_client=fake, model_budget=ledger)
        with patch.object(agent.fact_store, "related_candidates", return_value=[{"name": "old", "body": "old date"}]):
            conflicts = agent._semantic_conflicts({"fact_type": "action-item", "title": "new date"})
        self.assertEqual(conflicts, ["old"])
        self.assertEqual(ledger.input_tokens, 17)
        self.assertIs(agent.model.budget, ledger)

    def test_progress_is_observer_for_real_nested_focus_path(self):
        seen = []
        for observer in (None, lambda event: seen.append(event)):
            call = {"id": "focus-call", "type": "function", "function": {"name": "prepare_focus", "arguments": "{}"}}
            reply = content(""); reply.update(tool_calls=[call], message={"role": "assistant", "content": None, "tool_calls": [call]})
            fake = FakeClient([content('{"steps":[]}'), reply, focus_reply()])
            loop = AgentLoop(project_root=self.root, data_dir=self.data, question="准备会前关注",
                model_client=fake, progress=observer)
            result = loop.run()
            self.assertEqual(result["answer_status"], "awaiting_approval")
            self.assertEqual(result["budget"]["model_calls"], 3)
            self.assertEqual(result["budget"]["input_tokens"], 51)
            self.assertEqual(len(fake.calls), 3)
            self.assertEqual(fake.streams, 0)
        self.assertTrue(seen)

    def test_registered_retrieval_tools_disable_global_llm_reranking(self):
        registry = build_meeting_registry()
        fake = FakeClient([]); ledger = AgentBudget(); context = self.context(fake, ledger)
        with patch("meetingvar.agent.meeting_tools._rag") as rag:
            rag.return_value.search.return_value = []
            registry.invoke("search_project_knowledge", {"query": "q"}, context=context)
            self.assertFalse(rag.return_value.search.call_args.kwargs["rerank"])
        with patch("meetingvar.kg.graphrag.GraphRAG") as graph:
            graph.return_value.search.return_value = {"nodes": [], "edges": [], "chunks": []}
            registry.invoke("search_graph", {"query": "q"}, context=context)
            self.assertFalse(graph.return_value.search.call_args.kwargs["rerank"])
        self.assertFalse(fake.calls)
        self.assertEqual(ledger.model_calls, 0)


    def test_nested_model_error_does_not_reenter_outer_provider(self):
        call = {"id": "focus-call", "type": "function", "function": {"name": "prepare_focus", "arguments": "{}"}}
        tool = content(""); tool.update(tool_calls=[call], message={"role": "assistant", "content": None, "tool_calls": [call]})
        fake = FakeClient([content('{"steps":[]}'), tool, error_result("auth")])
        loop = AgentLoop(project_root=self.root, data_dir=self.data, question="准备会前关注",
            model_client=fake, progress=lambda event: None)
        report = loop.run()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "auth")
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(report["budget"]["model_calls"], 3)
        self.assertTrue(any(e.get("code") == "auth" for e in report["errors"]))
        self.assertFalse(report["coverage"]["confirmed_absence"])
        self.assertEqual(loop.messages[-1]["role"], "tool")

    def test_nested_json_protocol_failure_also_stops_outer_provider(self):
        fake = FakeClient([content('{"steps":[]}'), content('{"action":"tool","tool":"prepare_focus","arguments":{}}'),
                           error_result("quota")])
        loop = AgentLoop(project_root=self.root, data_dir=self.data, question="准备会前关注",
            model_client=fake, mode="json")
        result = loop.run()
        self.assertEqual(result["stop_reason"], "quota")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(fake.calls), 3)

    def test_approved_chat_memory_conflict_preserves_same_ledger(self):
        from meetingvar.agent.chat import ChatAgent
        from meetingvar.agent.memory import FactStore
        fake = FakeClient([error_result("auth", usage={"prompt_tokens": 8, "completion_tokens": 0})])
        chat = ChatAgent(self.root, data_dir=self.data, model_client=fake)
        ledger = chat._request_budget
        action = {"id": "a"*32, "type": "save_memory", "label": "save", "status": "pending",
            "payload": {"items": [{"title": "new", "body": "new date", "fact_type": "project-fact"}]}}
        chat.store.add_actions(chat.conversation_id, [action])
        with patch.object(FactStore, "related_candidates", return_value=[{"name": "old", "body": "old date"}]), patch.object(FactStore, "write") as write:
            report = chat.execute_action(action["id"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["result"]["error_code"], "auth")
        self.assertEqual(report["result"]["budget"]["input_tokens"], 8)
        self.assertEqual(ledger.input_tokens, 8)
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
