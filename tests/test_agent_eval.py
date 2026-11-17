"""Legacy interface regressions; load evaluator without application/provider imports."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = "meetingvar_answer_quality_eval"
if MODULE not in sys.modules:
    spec = importlib.util.spec_from_file_location(MODULE, ROOT / "src/meetingvar/agent/eval.py")
    ev = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = ev
    spec.loader.exec_module(ev)
ev = sys.modules[MODULE]
EvalCase, evaluate, load_cases = ev.EvalCase, ev.evaluate, ev.load_cases


class EvalTests(unittest.TestCase):
    def test_grounded_answer_and_refusal_metrics_are_unverified_without_gold(self):
        cases = [EvalCase("cite", "交付时间？", expected_citations=("turn_1", "fact-a")),
                 EvalCase("refuse", "没有证据的问题", expect_refusal=True)]

        def runner(case):
            if case.case_id == "cite":
                return {"ok": True, "grounded": True, "answered": True,
                        "semantic_verified": True, "answer": "周五",
                        "citations": [{"kind": "turn", "session_id": "s1",
                                       "turn_ids": ["turn_1"], "quote": "周五"},
                                      {"kind": "fact", "name": "fact-a"}],
                        "invalid_citations": [], "steps": 3, "tool_calls": 2,
                        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                        "elapsed_ms": 50, "stop_reason": "completed", "errors": []}
            return {"ok": True, "grounded": False, "answered": False,
                    "answer": "没有证据", "citations": [], "steps": 1,
                    "stop_reason": "completed_no_evidence", "elapsed_ms": 10}

        result = evaluate(cases, runner)
        summary = result["summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["accuracy"], 0.0)
        self.assertEqual(summary["grounded_rate"], 0.0)
        self.assertEqual(summary["unverified"], 2)
        self.assertEqual(summary["citation_precision"], 0.0)
        self.assertEqual(summary["refusal_evaluated"], 0)
        self.assertEqual(summary["input_tokens"], 100)
        self.assertFalse(summary["all_passed"])
        self.assertTrue(result["rows"][0]["reported_semantic_verified"])

    def test_invalid_citations_not_accepted_from_report_metadata(self):
        case = EvalCase("bad", "问题", expected_citations=("turn_1",))
        report = {"ok": True, "grounded": True, "answered": True,
                  "answer": "一个断言", "citations": [{"kind": "turn", "turn_ids": ["turn_1"]}],
                  "invalid_citations": [], "source_refs": [{"id": "turn_1"}]}
        row = evaluate([case], lambda _: report)["rows"][0]
        self.assertEqual(row["citation_precision"], 0.0)
        self.assertEqual(row["citation_recall"], 0.0)
        self.assertFalse(row["ok"])
        self.assertEqual(row["status"], "unverified")

    def test_load_cases_from_json(self):
        with tempfile.TemporaryDirectory(prefix="mv_eval_") as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps({"cases": [
                {"id": "a", "question": "问", "expected_citations": ["turn_1"], "tags": ["smoke"]},
                {"id": "b", "question": "拒答", "expect_refusal": True},
            ]}, ensure_ascii=False), encoding="utf-8")
            cases = load_cases(path)
        self.assertEqual([case.case_id for case in cases], ["a", "b"])
        self.assertEqual(cases[0].expected_citations, ("turn_1",))
        self.assertTrue(cases[1].expect_refusal)
        self.assertEqual(cases[0].label_origin, "unlabeled")

    def test_empty_summary_is_not_all_green(self):
        summary = ev.summarize([])
        self.assertEqual(summary["total"], 0)
        self.assertFalse(summary["all_passed"])
        self.assertIsNone(summary["evaluated_accuracy"])
        self.assertIsNone(summary["real_semantic_accuracy"])

    def test_runner_exception_is_infra_failure_and_next_case_runs(self):
        def runner(case):
            if case.case_id == "one":
                raise RuntimeError("sensitive exception body must not enter report")
            return {"answer": "a"}
        result = evaluate([EvalCase("one", "q"), EvalCase("two", "q")], runner)
        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["infra_failed"], 1)
        self.assertEqual(result["rows"][0]["quality_status"], "unverified")
        self.assertNotIn("sensitive exception", json.dumps(result))

    def test_duplicate_ids_cannot_inflate_case_count(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate([EvalCase("a", "q"), EvalCase("a", "q")], lambda _: {})

    def test_malformed_numeric_usage_cannot_crash_evaluation(self):
        row = ev.score_report(EvalCase("a", "q"), {"steps": "oops", "tool_calls": {},
                             "elapsed_ms": float("nan"), "usage": {"prompt_tokens": -9}})
        self.assertEqual((row.steps, row.tool_calls, row.elapsed_ms, row.input_tokens), (0, 0, 0, 0))

    def test_malformed_cases_are_not_silently_skipped(self):
        with tempfile.TemporaryDirectory(prefix="mv_eval_") as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"cases": [{"id": "bad"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path)
