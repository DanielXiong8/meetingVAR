"""Agent evaluation metrics are deterministic and model-free in tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.agent.eval import EvalCase, evaluate, load_cases


class EvalTests(unittest.TestCase):
    def test_grounded_answer_and_refusal_metrics(self):
        cases = [
            EvalCase("cite", "交付时间？", expected_citations=("turn_1", "fact-a")),
            EvalCase("refuse", "没有证据的问题", expect_refusal=True),
        ]

        def runner(case):
            if case.case_id == "cite":
                return {"ok": True, "grounded": True, "answered": True,
                        "citations": [{"kind": "turn", "session_id": "s1",
                                       "turn_ids": ["turn_1"], "quote": "周五"},
                                      {"kind": "fact", "name": "fact-a"}],
                        "invalid_citations": [], "steps": 3, "tool_calls": 2,
                        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                        "elapsed_ms": 50, "stop_reason": "completed", "errors": []}
            return {"ok": True, "grounded": False, "answered": False,
                    "citations": [], "invalid_citations": [], "steps": 1,
                    "tool_calls": 0, "usage": {}, "elapsed_ms": 10,
                    "stop_reason": "completed_no_evidence", "errors": []}

        result = evaluate(cases, runner)
        summary = result["summary"]
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["accuracy"], 1.0)
        self.assertEqual(summary["grounded_rate"], 0.5)
        self.assertEqual(summary["citation_precision"], 1.0)
        self.assertEqual(summary["citation_recall"], 1.0)
        self.assertEqual(summary["refusal_accuracy"], 1.0)
        self.assertEqual(summary["input_tokens"], 100)

    def test_invalid_citations_lower_precision(self):
        case = EvalCase("bad", "问题", expected_citations=("turn_1",))
        report = {"ok": True, "grounded": False, "answered": True,
                  "citations": [{"kind": "turn", "turn_ids": ["turn_1"]}],
                  "invalid_citations": [{"kind": "turn", "turn_ids": ["turn_9"]}],
                  "steps": 2, "tool_calls": 1, "usage": {}, "elapsed_ms": 1,
                  "stop_reason": "completed_ungrounded", "errors": []}
        row = evaluate([case], lambda _: report)["rows"][0]
        self.assertLess(row["citation_precision"], 1.0)
        self.assertEqual(row["citation_recall"], 1.0)

    def test_load_cases_from_json(self):
        with tempfile.TemporaryDirectory(prefix="mv_eval_") as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps({"cases": [
                {"id": "a", "question": "问", "expected_citations": ["turn_1"],
                 "tags": ["smoke"]},
                {"id": "b", "question": "拒答", "expect_refusal": True},
            ]}, ensure_ascii=False), encoding="utf-8")
            cases = load_cases(path)
        self.assertEqual([case.case_id for case in cases], ["a", "b"])
        self.assertEqual(cases[0].expected_citations, ("turn_1",))
        self.assertTrue(cases[1].expect_refusal)