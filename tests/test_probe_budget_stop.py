"""Synthetic-only legacy probe stop, pending-tail and source-cache regressions."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
with mock.patch("meetingvar.env.load_local_env"):
    from meetingvar import llm
    from meetingvar.agent.model_calls import ToolModelCalls
    from meetingvar.agent.policy import AgentBudget
    from meetingvar.memory import probe
    from meetingvar.model_client import CallableModelClient


TEXT = "Alpha delivers Friday.\n"
CHUNKS = 1151
GOOD = json.dumps({"persons": [], "keywords": [{"term": "Alpha", "why": "delivery"}],
                   "events": []})


def reply(content=GOOD):
    return {"ok": True, "content": content, "error": None, "error_code": None,
            "retryable": False, "finish_reason": "stop", "usage": {}}


class ProbeBudgetStopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mv-probe-stop-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "original.txt"
        self.write_source(CHUNKS)
        for function in ("urlopen", "build_opener"):
            blocker = mock.patch("meetingvar.llm.urllib.request." + function,
                                 side_effect=AssertionError("unexpected network operation"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def write_source(self, count):
        self.original = (TEXT * count).encode("utf-8")
        self.source.write_bytes(self.original)

    def client(self, *replies, cache=False):
        transport = mock.Mock(side_effect=replies)
        client = CallableModelClient(transport, budget=llm.TokenBudget(16384, 512))
        if cache:
            client.cache_key = "synthetic-legacy-probe-stop"
        return client, transport

    def run_probe(self, client, **kwargs):
        progress = []
        original = ToolModelCalls.complete
        with mock.patch.object(ToolModelCalls, "complete", autospec=True,
                               side_effect=original) as boundary:
            result = probe.understand(self.root, chunk_chars=len(TEXT),
                                      model_client=client, progress=progress.append, **kwargs)
        # Count the admission boundary as well as provider calls: a spent ledger
        # could otherwise spin through 1151 chunks without another network call.
        self.assertEqual(self.source.read_bytes(), self.original)
        return result, boundary.call_count, progress

    def assert_stopped(self, result, code, *, read=0, total=CHUNKS):
        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], code)
        self.assertEqual(result["chunks_total"], total)
        self.assertEqual(result["chunks_read"], read)
        self.assertEqual(result["covered_chars"], read * len(TEXT))
        self.assertEqual(result["coverage_pct"], round(read / total * 100, 1))
        self.assertEqual(len(result["failed_chunks"]), 1)
        failed = result["failed_chunks"][0]
        self.assertEqual(failed["chunk"], read + 1)
        self.assertEqual(failed["error_code"], code)
        self.assertEqual(failed["offset"], read * len(TEXT))
        pending = result["pending_chunks"]
        self.assertEqual(result["chunks_pending"], total - read - 1)
        self.assertEqual(len(pending), result["chunks_pending"])
        self.assertEqual([row["chunk"] for row in pending], list(range(read + 2, total + 1)))
        self.assertTrue(all(row["doc"] == self.source.name for row in pending))
        self.assertTrue(all("text" not in row and "error_code" not in row for row in pending))
        self.assertEqual(pending[0]["offset"], (read + 1) * len(TEXT))
        self.assertEqual(pending[-1]["end"], total * len(TEXT))
        self.assertEqual(result["chunks_read"] + len(result["failed_chunks"]) +
                         result["chunks_pending"], total)
        self.assertEqual(result["overview"], "", "A stopped run must not call the overview model")

    def test_fatal_provider_or_budget_error_stops_before_compact_or_tail(self):
        codes = ("auth", "quota", "model_not_found", "invalid_config", "not_configured",
                 "input_too_large", "input_budget_exhausted", "output_budget_exhausted",
                 "time_budget_exhausted", "step_budget_exhausted", "tool_budget_exhausted",
                 "future_budget_exhausted")
        for code in codes:
            with self.subTest(code=code):
                # Some ledger codes are not provider error_result entries.
                failure = {"ok": False, "content": "", "error_code": code,
                           "error": "synthetic " + code, "retryable": False, "usage": {}}
                client, transport = self.client(failure, reply())
                result, attempts, progress = self.run_probe(client)
                self.assert_stopped(result, code)
                self.assertEqual(attempts, 1)
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(result["usage"]["model_calls"], 1)
                self.assertEqual([error["code"] for error in result["model_errors"]], [code])
                self.assertEqual(sum(line.startswith("处理中") for line in progress), 1)
                self.assertFalse(any(line.startswith("已读") for line in progress))
                self.assertFalse(probe._cache_dir(self.root).exists())

    def test_no_usage_transport_failure_preserves_cause_and_stops_once(self):
        for code in ("network", "rate_limit", "truncated", "context_budget", "invalid_json"):
            with self.subTest(code=code):
                client, transport = self.client(llm.error_result(code), reply())
                ledger = AgentBudget(max_output_tokens=512, max_seconds=900)
                result, attempts, _ = self.run_probe(client, model_budget=ledger)
                self.assert_stopped(result, code)
                self.assertEqual(attempts, 1)
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(ledger.output_tokens, 512)
                self.assertEqual(ledger.model_calls, 1)
                self.assertEqual(ledger.estimated_usage_calls, 1)
                self.assertEqual([error["code"] for error in result["model_errors"]], [code])
                self.assertNotIn("output_budget_exhausted", [e["code"] for e in result["errors"]])

    def test_spent_ledger_stops_on_first_admission_without_provider_call(self):
        cases = (
            ("input_budget_exhausted", {"max_input_tokens": 1}),
            ("output_budget_exhausted", {"max_output_tokens": 512, "output_tokens": 512}),
            ("time_budget_exhausted", {"elapsed_before_resume": 1000}),
        )
        for code, options in cases:
            with self.subTest(code=code):
                client, transport = self.client(reply())
                ledger = AgentBudget(max_seconds=900, **options)
                result, attempts, progress = self.run_probe(client, model_budget=ledger)
                self.assert_stopped(result, code)
                self.assertEqual(attempts, 1)
                transport.assert_not_called()
                self.assertEqual(result["usage"]["model_calls"], 0)
                self.assertEqual(sum(line.startswith("处理中") for line in progress), 1)

    def test_output_exhaustion_keeps_successful_cache_and_pending_tail(self):
        completed = reply()
        completed["usage"] = {"prompt_tokens": 100, "completion_tokens": 512}
        client, transport = self.client(completed, reply(), cache=True)
        ledger = AgentBudget(max_output_tokens=512, max_seconds=900)
        result, attempts, progress = self.run_probe(client, model_budget=ledger)
        self.assert_stopped(result, "output_budget_exhausted", read=1)
        self.assertEqual(attempts, 2)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["usage"]["model_calls"], 1)
        self.assertEqual(sum(line.startswith("处理中") for line in progress), 2)
        self.assertEqual(sum(line.startswith("已读") for line in progress), 1)
        self.assertEqual(len(list(probe._cache_dir(self.root).glob("*.json"))), 1)
        self.assertEqual(result["terms"][0]["term"], "Alpha")

        # The completed chunk remains reusable without spending a new call.
        resumed, transport = self.client(cache=True)
        result, attempts, _ = self.run_probe(resumed, max_chunks=1, overview=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["cache_hits"], 1)
        self.assertEqual(result["chunks_pending"], CHUNKS - 1)
        self.assertEqual(result["failed_chunks"], [])
        self.assertEqual(attempts, 0)
        transport.assert_not_called()

    def test_failure_during_json_repair_or_compact_is_not_hidden(self):
        for malformed_count in (1, 2, 3):
            with self.subTest(malformed_count=malformed_count):
                client, transport = self.client(*([reply("not JSON")] * malformed_count),
                                                llm.error_result("quota"), reply())
                result, attempts, _ = self.run_probe(client)
                self.assert_stopped(result, "quota")
                self.assertEqual(attempts, malformed_count + 1)
                self.assertEqual(transport.call_count, malformed_count + 1)
                self.assertEqual(result["model_errors"][0]["code"], "quota")
                prompts = [call.args[0][0]["content"] for call in transport.call_args_list]
                self.assertTrue(all(TEXT in prompt for prompt in prompts))
                self.assertTrue(prompts[1].startswith(prompts[0]))
                self.assertFalse(any("精简模式读取" in warning for warning in result["warnings"]))

    def test_malformed_success_can_still_recover_with_original_source(self):
        self.write_source(1)
        client, transport = self.client(reply("not JSON"), reply())
        result, attempts, _ = self.run_probe(client, overview=False)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["complete"])
        self.assertEqual(result["coverage_pct"], 100.0)
        self.assertEqual(result["chunks_pending"], 0)
        self.assertEqual(result["pending_chunks"], [])
        self.assertEqual(result["failed_chunks"], [])
        self.assertEqual(attempts, 2)
        first, repaired = [call.args[0][0]["content"] for call in transport.call_args_list]
        self.assertTrue(repaired.startswith(first))
        self.assertIn(TEXT, repaired)

    def test_parse_only_failure_is_failed_not_pending_and_later_chunks_can_read(self):
        self.write_source(3)
        client, transport = self.client(*([reply("not JSON")] * 4), reply(), reply())
        result, attempts, _ = self.run_probe(client)
        self.assertFalse(result["complete"])
        self.assertEqual(result["error_code"], "partial_read")
        self.assertEqual(result["chunks_read"], 2)
        self.assertEqual(result["coverage_pct"], round(2 / 3 * 100, 1))
        self.assertEqual(result["failed_chunks"][0]["chunk"], 1)
        self.assertEqual(len(result["failed_chunks"]), 1)
        self.assertEqual(result["chunks_pending"], 0)
        self.assertEqual(result["pending_chunks"], [])
        self.assertEqual(attempts, 6)
        self.assertEqual(transport.call_count, 6)

    def test_max_chunks_sample_has_explicit_unattempted_tail(self):
        self.write_source(4)
        client, transport = self.client(reply())
        result, attempts, _ = self.run_probe(client, max_chunks=1, overview=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["chunks_read"], 1)
        self.assertEqual(result["coverage_pct"], 25.0)
        self.assertEqual(result["chunks_pending"], 3)
        self.assertEqual([row["chunk"] for row in result["pending_chunks"]], [2, 3, 4])
        self.assertEqual(result["failed_chunks"], [])
        self.assertEqual(attempts, 1)
        self.assertEqual(transport.call_count, 1)

    def test_corpus_notes_carry_source_bytes_and_extraction_revision(self):
        docs, notes = probe.load_corpus(self.root)
        self.assertEqual(docs, [(self.source.name, self.original.decode("utf-8"))])
        self.assertEqual(len(notes.sources), 1)
        self.assertEqual(notes.sources[0]["bytes_hash"], hashlib.sha256(self.original).hexdigest())
        self.assertTrue(notes.sources[0]["revision"])
        self.assertTrue(notes.sources[0]["complete"])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_cache_binds_optional_source_bytes_and_extraction_revision(self):
        self.write_source(1)
        docs = [(self.source.name, TEXT)]
        variants = [None, {"bytes_hash": "bytes-a", "revision": "revision-a"},
                    {"bytes_hash": "bytes-b", "revision": "revision-a"},
                    {"bytes_hash": "bytes-b", "revision": "revision-b"}]
        for version in variants:
            with self.subTest(version=version):
                notes = [] if version is None else probe.CorpusNotes()
                if version is not None:
                    notes.sources.append({"path": self.source.name, "complete": True,
                                          "coverage": {}, **version})
                sources_before = copy.deepcopy(getattr(notes, "sources", []))
                with mock.patch.object(probe, "load_corpus", return_value=(docs, notes)):
                    client, transport = self.client(reply(), cache=True)
                    first, attempts, _ = self.run_probe(client, overview=False)
                    self.assertTrue(first["complete"])
                    self.assertEqual(first["cache_hits"], 0)
                    self.assertEqual(attempts, 1)
                    self.assertEqual(transport.call_count, 1)
                    client, transport = self.client(cache=True)
                    reused, attempts, _ = self.run_probe(client, overview=False)
                    self.assertTrue(reused["complete"])
                    self.assertEqual(reused["cache_hits"], 1)
                    self.assertEqual(attempts, 0)
                    transport.assert_not_called()
                self.assertEqual(getattr(notes, "sources", []), sources_before)
                self.assertEqual(docs, [(self.source.name, TEXT)])
        self.assertEqual(len(list(probe._cache_dir(self.root).glob("*.json"))), len(variants))


if __name__ == "__main__":
    unittest.main()
