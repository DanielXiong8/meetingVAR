"""Tool contract, trace, runtime and fact-store tests without model or network."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.memory import FactStore
from meetingvar.agent.runtime import AgentRuntime, TaskStatus
from meetingvar.agent.tools import (ToolContext, ToolError, ToolRegistry, ToolSpec,
                                    validate_arguments)
from meetingvar.agent.trace import TraceRecorder


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_agent_tools_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trace = TraceRecorder(self.root / "trace.jsonl")
        self.registry = ToolRegistry(trace=self.trace)
        self.registry.register(ToolSpec(
            name="read_evidence", description="read only",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"ids": {"type": "array", "minItems": 1,
                                                 "items": {"type": "string"}}},
                          "required": ["ids"]},
            max_output_chars=20,
        ), lambda args, ctx: [{"id": i, "text": "x" * 30} for i in args["ids"]])
        self.registry.register(ToolSpec(
            name="write_fact", description="write with approval",
            input_schema={"type": "object", "additionalProperties": False,
                          "properties": {"value": {"type": "string"}},
                          "required": ["value"]},
            read_only=False, requires_approval=True, side_effect_scope="project",
            idempotent=True,
        ), lambda args, ctx: {"written": args["value"]})

    def test_schema_rejects_unknown_and_missing_fields(self):
        with self.assertRaises(ToolError) as missing:
            self.registry.invoke("read_evidence", {})
        self.assertEqual(missing.exception.code, "invalid_arguments")
        with self.assertRaises(ToolError):
            self.registry.invoke("read_evidence", {"ids": ["a"], "extra": 1})

    def test_read_tool_result_is_bounded_and_traced(self):
        result = self.registry.invoke("read_evidence", {"ids": ["turn_1"]},
                                      context=ToolContext(transcript=[]))
        self.assertTrue(result.truncated)
        self.assertIn("truncated", result.text)
        events = self.trace.tail(10)
        self.assertEqual(events[0]["event"], "tool_started")
        self.assertEqual(events[-1]["event"], "tool_finished")
        self.assertNotIn("turn_1", json.dumps(events, ensure_ascii=False))

    def test_approval_is_fail_closed(self):
        with self.assertRaises(ToolError) as missing:
            self.registry.invoke("write_fact", {"value": "x"})
        self.assertEqual(missing.exception.code, "approval_required")

    def test_denied_approval_raises_and_approved_runs_once_by_idempotency(self):
        with self.assertRaises(ToolError) as denied:
            self.registry.invoke("write_fact", {"value": "x"}, approval=lambda request: False)
        self.assertEqual(denied.exception.code, "approval_denied")
        allowed = self.registry.invoke("write_fact", {"value": "x"},
                                       approval=lambda request: True,
                                       idempotency_key="same")
        again = self.registry.invoke("write_fact", {"value": "x"},
                                     approval=lambda request: True,
                                     idempotency_key="same")
        self.assertEqual(allowed.value, {"written": "x"})
        self.assertEqual(again.value, allowed.value)
        self.assertTrue(any(e["event"] == "tool_cache_hit" for e in self.trace.tail(20)))

    def test_timeout_returns_classified_error(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="slow", description="slow", input_schema={"type": "object"},
            timeout_s=0.02,
        ), lambda args, ctx: (time.sleep(0.2), "late")[1])
        with self.assertRaises(ToolError) as caught:
            registry.invoke("slow", {})
        self.assertEqual(caught.exception.code, "timeout")

    def test_invalid_schema_subset(self):
        with self.assertRaises(ToolError) as caught:
            validate_arguments([1], {"type": "array", "minItems": 2, "items": {"type": "integer"}})
        self.assertEqual(caught.exception.code, "invalid_arguments")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_agent_runtime_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trace = TraceRecorder(self.root / "trace.jsonl")
        self.runtime = AgentRuntime(self.root / "tasks", trace=self.trace, max_steps=2)
        self.registry = ToolRegistry(trace=self.trace)
        self.registry.register(ToolSpec(
            name="echo", description="echo",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}},
                          "required": ["text"]},
        ), lambda args, ctx: args["text"])

    def test_task_checkpoint_and_resume(self):
        task = self.runtime.create("demo", {"meeting": "m1"})
        result = self.runtime.run_tool(task, self.registry, "echo", {"text": "hello"})
        self.assertTrue(result["ok"])
        loaded = self.runtime.load(task.task_id)
        self.assertEqual(loaded.step, 1)
        self.assertEqual(loaded.last_tool, "echo")
        self.assertEqual(loaded.status, TaskStatus.RUNNING.value)

    def test_step_budget_fails_closed(self):
        task = self.runtime.create("demo")
        self.runtime.run_tool(task, self.registry, "echo", {"text": "1"})
        self.runtime.run_tool(task, self.registry, "echo", {"text": "2"})
        with self.assertRaises(ToolError) as caught:
            self.runtime.run_tool(task, self.registry, "echo", {"text": "3"})
        self.assertEqual(caught.exception.code, "step_budget_exhausted")
        self.assertEqual(self.runtime.load(task.task_id).status, TaskStatus.FAILED.value)

    def test_trace_redacts_secrets(self):
        self.trace.record("test", api_key="secret-value", nested={"token": "x"}, text="ok")
        row = self.trace.tail(1)[0]
        self.assertEqual(row["api_key"], "[redacted]")
        self.assertEqual(row["nested"]["token"], "[redacted]")
        self.assertNotIn("secret-value", json.dumps(row, ensure_ascii=False))
        self.trace.record_llm(model="m", ok=True, latency_ms=12.5,
                              usage={"prompt_tokens": 100, "completion_tokens": 20,
                                     "completion_tokens_details": {"reasoning_tokens": 5}})
        usage_row = self.trace.tail(1)[0]
        self.assertEqual(usage_row["prompt_tokens"], 100)
        self.assertEqual(usage_row["completion_tokens"], 20)
        self.assertEqual(usage_row["reasoning_tokens"], 5)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mv_agent_memory_")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.store = FactStore(self.project)

    def test_atomic_fact_and_rebuildable_index(self):
        path = self.store.write(name="周五交付第一版", description="小林负责交付",
                                body="证据：turn_2", fact_type="action-item",
                                source_refs=["turn_2"], confidence=0.9,
                                origin_meeting="go_1")
        self.assertTrue(path.exists())
        fact = self.store.read("周五交付第一版")
        self.assertEqual(fact["name"], "周五交付第一版")
        self.assertEqual(fact["metadata"]["origin_meeting"], "go_1")
        index = self.store.index()
        self.assertIn("周五交付第一版", index)
        self.assertIn("facts/", index)
        self.store.rebuild_index()
        self.assertEqual(len([line for line in self.store.index().splitlines() if line.startswith("- ")]), 1)

    def test_update_increments_version_and_index_has_one_entry(self):
        name = "决定采用第二方案"
        first = self.store.write(name=name, description="v1", body="a", fact_type="decision")
        second = self.store.write(name=name, description="v2", body="b", fact_type="decision")
        self.assertEqual(first, second)
        fact = self.store.read(name)
        self.assertEqual(fact["version"], 2)
        self.assertEqual(len([line for line in self.store.index().splitlines() if line.startswith("- ")]), 1)

    def test_path_like_names_are_sanitized_into_facts_directory(self):
        for name in ("../escape", "a/b", "a\\b", ".."):
            with self.subTest(name=name):
                path = self.store.write(name=name, description="x", body="x", fact_type="project-fact")
                self.assertEqual(path.parent, self.store.facts_dir)
                self.assertNotIn("..", path.name)

    def test_search_and_limit(self):
        self.store.write(name="甲", description="alpha", body="x", fact_type="project-fact")
        self.store.write(name="乙", description="beta", body="alpha", fact_type="project-fact")
        self.assertEqual(len(self.store.search("alpha")), 2)
        self.assertEqual(self.store.search("missing"), [])


if __name__ == "__main__":
    unittest.main()