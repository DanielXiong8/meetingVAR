"""Mechanism regressions: no live provider, transcript or model quality claims."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
with patch("meetingvar.env.load_local_env"):
    from meetingvar.llm import TokenBudget, error_result, _messages_bytes
    from meetingvar.model_client import CallableModelClient
    from meetingvar.request_context import model_request
    from meetingvar.agent.context import ContextManager, build_context
    from meetingvar.agent.policy import AgentBudget
    from meetingvar.agent.loop import AgentLoop
    from meetingvar.agent.chat import ChatAgent


def content(text, usage=None):
    return {"ok": True, "content": text, "tool_calls": [],
            "message": {"role": "assistant", "content": text},
            "usage": {"prompt_tokens": 17, "completion_tokens": 11} if usage is None else usage}


class FakeClient:
    def __init__(self, responses, budget=None):
        self.responses = list(responses)
        self.calls = []
        self.streams = 0
        self._budget = budget or TokenBudget(32000, 6000)

    def budget(self):
        return self._budget

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected model request")
        return self.responses.pop(0)

    def stream(self, messages, on_delta=None, **kwargs):
        self.streams += 1
        return self.complete(messages, **kwargs)


class ContextProtectionTests(unittest.TestCase):
    def test_current_question_in_middle_and_multiple_systems_are_immutable(self):
        rows = [{"role": "system", "content": "permission-a"},
                {"role": "user", "content": "old" * 600},
                {"role": "system", "content": "permission-b"},
                {"role": "user", "content": "CURRENT-EXACT"},
                {"role": "assistant", "content": "history" * 700},
                {"role": "user", "content": "Correction: keep this too"}]
        fit = ContextManager(input_limit=120).fit(rows, current_turn=3)
        self.assertTrue(fit.ok)
        self.assertEqual([m["content"] for m in fit.messages],
                         ["permission-a", "permission-b", "CURRENT-EXACT", "Correction: keep this too"])
        self.assertEqual(rows[1]["content"], "old" * 600)

    def test_multiple_tool_calls_are_removed_as_one_atomic_group(self):
        rows = [{"role": "system", "content": "rules"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "a", "function": {"name": "one", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "two", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "b", "content": "payload" * 200},
                {"role": "tool", "tool_call_id": "a", "content": "payload" * 200},
                {"role": "user", "content": "current"}]
        fit = ContextManager(input_limit=100).fit(rows, current_turn=4)
        self.assertTrue(fit.ok)
        self.assertEqual([r["role"] for r in fit.messages], ["system", "user"])
        incomplete = rows[:3] + rows[4:]
        self.assertEqual(ContextManager(input_limit=10000).fit(incomplete, current_turn=3).error_code, "invalid_messages")

    def test_schema_and_protected_input_oversize_fail_before_provider(self):
        client = FakeClient([], TokenBudget(800, 200, margin_tokens=100))
        result = model_request(client, [{"role": "system", "content": "s" * 2000},
            {"role": "user", "content": "current"}], current_turn=1,
            tools=[{"description": "schema" * 400}])
        self.assertEqual(result["error_code"], "input_too_large")
        self.assertFalse(client.calls)

    def test_explicit_prior_correction_is_protected(self):
        rows=[{"role":"user","content":"correction"},
              {"role":"assistant","content":"old"*1000},
              {"role":"user","content":"question"}]
        fit=ContextManager(input_limit=60).fit(rows,current_turn=2,protected_indices=[0])
        self.assertEqual([r["content"] for r in fit.messages],["correction","question"])

    def test_workspace_material_is_not_system_authority(self):
        with tempfile.TemporaryDirectory() as temp, patch("meetingvar.agent.context.workspace_context", return_value="fake workspace instructions"), patch("meetingvar.agent.context.project_guidance", return_value="file permission fiction"):
            rows=build_context(Path(temp),"summary",[{"role":"user","content":"u"*5000,"seq":2}])
        self.assertFalse(any("fake workspace instructions" in r["content"] for r in rows if r["role"]=="system"))
        self.assertTrue(rows[-1]["content"].endswith("u"*5000))
        self.assertTrue(rows[-1]["_context_protected"])


class AccountingTests(unittest.TestCase):
    def test_continuation_and_restore_never_reset_total_cost(self):
        ledger=AgentBudget(max_steps=1,max_input_tokens=500,max_output_tokens=500)
        ledger.steps=1;ledger.consume({"prompt_tokens":120,"completion_tokens":80},model_call=True)
        self.assertFalse(ledger.can_step()[0])
        ledger.next_segment()
        self.assertTrue(ledger.can_step()[0])
        self.assertEqual((ledger.steps,ledger.input_tokens,ledger.output_tokens),(1,120,80))
        restored=AgentBudget();restored.restore(ledger.as_dict())
        self.assertEqual((restored.steps,restored.input_tokens,restored.output_tokens),(1,120,80))

    def test_missing_usage_is_estimated_and_nonretryable_is_preserved(self):
        client=FakeClient([error_result("auth")])
        ledger=AgentBudget(max_output_tokens=12000)
        result=model_request(client,[{"role":"user","content":"q"}],current_turn=0,ledger=ledger)
        self.assertEqual(result["error_code"],"auth")
        self.assertEqual(len(client.calls),1)
        self.assertGreater(ledger.input_tokens,0)
        self.assertEqual(ledger.output_tokens,6000)
        self.assertEqual(ledger.estimated_usage_calls,1)

    def test_admission_denies_before_provider(self):
        client=FakeClient([]);ledger=AgentBudget(max_input_tokens=5,max_output_tokens=12000)
        result=model_request(client,[{"role":"user","content":"q"}],current_turn=0,ledger=ledger)
        self.assertEqual(result["error_code"],"input_budget_exhausted")
        self.assertFalse(client.calls)

    def test_retry_is_finite_and_settles_each_attempt(self):
        failure=error_result("rate_limit",usage={"prompt_tokens":4,"completion_tokens":0})
        client=FakeClient([failure,failure,failure])
        ledger=AgentBudget(max_output_tokens=12000)
        result=model_request(client,[{"role":"user","content":"q"}],current_turn=0,ledger=ledger)
        self.assertEqual(result["error_code"],"rate_limit")
        self.assertEqual(len(client.calls),2)
        self.assertEqual(ledger.input_tokens,8)

    def test_output_cap_comes_from_client_and_full_answer_survives(self):
        answer=chr(10).join(f"entry-{i}" for i in range(600))
        client=FakeClient([content(answer,{})],TokenBudget(32000,7000))
        result=model_request(client,[{"role":"user","content":"enumerate"}],current_turn=0,ledger=AgentBudget())
        self.assertEqual(client.calls[0]["max_tokens"],7000)
        self.assertEqual(result["content"],answer)


class LoopAndChatTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix="mv_core_")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        for target in ("meetingvar.llm.urllib.request.urlopen", "meetingvar.llm.urllib.request.build_opener",
                       "meetingvar.agent.loop.chat_messages_stream", "meetingvar.agent.chat.chat_messages_stream"):
            mock=patch(target,side_effect=AssertionError("global provider bypass"));mock.start();self.addCleanup(mock.stop)

    def loop(self,client,**kw):
        return AgentLoop(project_root=self.root,data_dir=self.root/"data",question="question",model_client=client,**kw)

    def test_progress_is_observer_with_identical_injected_calls(self):
        answers=[content('{"steps":[]}'),content('{"answer":"normal response","citations":[]}')]
        paths=[]
        for progress in (None,lambda event:None):
            fake=FakeClient(answers)
            report=self.loop(fake,progress=progress).run()
            self.assertEqual(report["answer"],"normal response")
            self.assertEqual(fake.streams,0)
            paths.append([(c["max_tokens"],c.get("tools"),[m["role"] for m in c["messages"]]) for c in fake.calls])
        self.assertEqual(paths[0],paths[1])

    def test_stream_entry_is_explicit_and_never_uses_globals(self):
        client=FakeClient([content('{"steps":[]}'),content('{"answer":"stream response"}')])
        report=self.loop(client,stream=True,progress=lambda e:None).run()
        self.assertEqual(client.streams,2)
        self.assertEqual(report["answer"],"stream response")

    def test_legacy_callable_with_progress_cannot_escape(self):
        fake=FakeClient([content('{"steps":[]}'),content('{"answer":"legacy"}')])
        loop=AgentLoop(project_root=self.root,data_dir=self.root/"data",question="q",llm=fake.complete,progress=lambda e:None)
        self.assertEqual(loop.run()["answer"],"legacy")
        self.assertEqual(len(fake.calls),2)

    def test_rate_limit_with_zero_retrieval_is_failed_not_absence(self):
        failure=error_result("rate_limit",usage={"prompt_tokens":1,"completion_tokens":0})
        client=FakeClient([failure,failure])
        report=self.loop(client).run()
        self.assertEqual(report["status"],"failed")
        self.assertEqual(report["answer_status"],"failed")
        self.assertEqual(report["tool_calls"],0)
        self.assertFalse(report["observations"])
        self.assertFalse(report["answered"])
        self.assertNotIn("未找到",report["answer"])
        self.assertEqual(report["errors"][0]["code"],"rate_limit")

    def test_scalar_project_facts_never_slice_or_synthesize(self):
        fake=FakeClient([]);loop=self.loop(fake)
        loop.observations=[{"tool":"project_status","value":{"facts":1},"text":""}]
        final=loop._fallback_final()
        self.assertEqual(final["answer_status"],"partial")
        self.assertFalse(final["semantic_verified"])
        self.assertEqual(len(fake.calls),0)

    def test_source_identifier_is_not_semantic_grounding(self):
        loop=self.loop(FakeClient([]))
        loop.observations=[{"tool":"read_file","arguments":{"path":"notes.txt"},
             "value":{"path":"notes.txt","text":"observed text","revision":"abc","line_start":1,"line_end":1}}]
        ref=loop._evidence_set().sources()[0]
        final=loop._normalize_final({"answer":"model claim","citations":[{"source_ref_id":ref["id"]}]},"")
        self.assertTrue(final["source_validated"])
        self.assertFalse(final["semantic_verified"])
        self.assertFalse(final["grounded"])

    def test_chat_preserves_model_error_metadata(self):
        client=FakeClient([error_result("auth")])
        chat=ChatAgent(self.root,data_dir=self.root/"data",model_client=client,progress=lambda e:None)
        result=chat.ask("question",mode="chat")
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"]["answer_status"],"failed")
        self.assertEqual(result["message"]["errors"][0]["code"],"auth")
        self.assertEqual(len(client.calls),1)

    def test_planner_overlarge_question_is_rejected_without_call(self):
        fake=FakeClient([],TokenBudget(4000,1000))
        loop=AgentLoop(project_root=self.root,data_dir=self.root/"data",question="中"*8000,model_client=fake)
        report=loop.run()
        self.assertEqual(report["stop_reason"],"input_too_large")
        self.assertFalse(fake.calls)


    def test_real_continuation_preserves_plan_and_prior_step_usage(self):
        call={"id":"c1","type":"function","function":{"name":"project_status","arguments":"{}"}}
        tool_reply={"ok":True,"content":"","message":{"role":"assistant","content":None,"tool_calls":[call]},
                    "tool_calls":[call],"usage":{"prompt_tokens":17,"completion_tokens":11}}
        fake=FakeClient([content('{"steps":[]}'),tool_reply,content('{"answer":"partial outcome","answer_status":"partial"}')])
        loop=self.loop(fake,max_steps=1,max_continuations=1)
        report=loop.run()
        self.assertEqual(report["continuations"],1)
        self.assertEqual(report["budget"]["steps"],2)
        self.assertEqual(report["budget"]["input_tokens"],51)
        self.assertEqual(report["budget"]["output_tokens"],33)
        self.assertEqual(report["budget"]["model_calls"],3)
        self.assertIn("coverage",json.dumps(fake.calls[-1]["messages"]))

    def test_full_answer_enumeration_and_code_survive_loop_and_chat(self):
        answer=chr(10).join([f"- item {i}" for i in range(600)])+chr(10)+"```python"+chr(10)+"print('keep')"+chr(10)+"```"
        fake=FakeClient([content(json.dumps({"steps":[]})),content(json.dumps({"answer":answer}))])
        chat=ChatAgent(self.root,data_dir=self.root/"data",model_client=fake,progress=lambda e:None)
        report=chat.ask("enumerate all",mode="agent")
        self.assertEqual(report["message"]["content"],answer)
        self.assertEqual(fake.calls[-1]["max_tokens"],6000)

    def test_http_error_does_not_force_unrelated_provider_fallback(self):
        failure=error_result("http_error",http_status=400)
        fake=FakeClient([content('{"steps":[]}'),failure])
        report=self.loop(fake,progress=lambda e:None).run()
        self.assertEqual(report["stop_reason"],"http_error")
        self.assertEqual(report["answer_status"],"failed")
        self.assertEqual(len(fake.calls),2)

    def test_chat_oversize_preserves_question_and_never_calls_provider(self):
        fake=FakeClient([],TokenBudget(3000,1000))
        chat=ChatAgent(self.root,data_dir=self.root/"data",model_client=fake)
        question="中"*6000
        report=chat.ask(question,mode="chat")
        self.assertEqual(report["message"]["error_code"],"input_too_large")
        self.assertEqual(report["conversation"]["messages"][0]["content"],question)
        self.assertFalse(fake.calls)

    def test_explicit_approval_is_not_answered_or_semantically_verified(self):
        loop=self.loop(FakeClient([]))
        loop.ui_actions=[{"type":"write_file","action_id":"pending"}]
        result=loop._result(status="waiting_approval",stop_reason="action_pending",final={"answer":"Await confirmation","answered":True})
        self.assertEqual(result["answer_status"],"awaiting_approval")
        self.assertFalse(result["answered"])
        self.assertFalse(result["semantic_verified"])


class ProviderBoundaryTests(unittest.TestCase):
    def test_complete_and_stream_respect_cap_and_do_not_emit_thinking(self):
        from meetingvar import config,llm
        class Response:
            def __init__(self,lines): self.lines=lines
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def __iter__(self): return iter(self.lines)
        def event(delta,finish=None):
            return ("data: "+json.dumps({"choices":[{"delta":delta,"finish_reason":finish}]})+chr(10)).encode()
        lines=[event({"content":"<thi"}),event({"content":"nk>hidden reasoning</think>full answer"},"stop"),b"data: [DONE]"]
        with patch.multiple(config,LLM_BASE_URL="https://invalid.test/v1",LLM_MODEL="fake",LLM_API_KEY="fake",
                LLM_MAX_TOKENS="6000",LLM_CONTEXT_TOKENS="32000",LLM_THINKING=""), patch("meetingvar.llm.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value=Response(lines)
            deltas=[]
            result=llm.chat_messages_stream([{"role":"user","content":"q"}],max_tokens=9000,on_delta=lambda k,t:deltas.append((k,t)))
            self.assertEqual(result["content"],"full answer")
            self.assertEqual(deltas,[("content","full answer")])
            self.assertEqual(json.loads(opener.return_value.open.call_args.args[0].data)["max_tokens"],6000)
            opener.return_value.open.return_value=Response(lines)
            def observer(*args): raise RuntimeError("observer failure")
            self.assertTrue(llm.chat_messages_stream([{"role":"user","content":"q"}],on_delta=observer)["ok"])

    def test_truncated_stream_never_emits_unvalidated_partial_content(self):
        from meetingvar import config,llm
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def __iter__(self):
                yield ("data: "+json.dumps({"choices":[{"delta":{"content":"incomplete"},"finish_reason":"length"}]})+chr(10)).encode()
        with patch.multiple(config,LLM_BASE_URL="https://invalid.test/v1",LLM_MODEL="fake",LLM_API_KEY="fake",
                LLM_MAX_TOKENS="6000",LLM_CONTEXT_TOKENS="32000",LLM_THINKING=""), patch("meetingvar.llm.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value=Response()
            deltas=[]
            result=llm.chat_messages_stream([{"role":"user","content":"q"}],on_delta=lambda k,t:deltas.append(t))
            self.assertEqual(result["error_code"],"truncated")
            self.assertFalse(deltas)


if __name__ == "__main__":
    unittest.main()
