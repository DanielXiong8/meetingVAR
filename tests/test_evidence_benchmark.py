"""Event-level evidence benchmark contracts (no private meeting data)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.eval.evidence_benchmark import (  # noqa: E402
    evaluate_case, evaluate_root, gate, load_case, markdown)


class EvidenceBenchmarkTests(unittest.TestCase):
    def _case(self, root: Path) -> Path:
        case = root / "case1"
        case.mkdir()
        (case / "labels.json").write_text(json.dumps({"events": [
            {"event_id": "e1", "start_time": 100, "end_time": 160,
             "event_type": "DECISION", "focus_ids": ["f1"], "requires_capture": True},
            {"event_id": "e2", "start_time": 300, "end_time": 360,
             "event_type": "COMMITMENT", "focus_ids": ["f2"], "requires_capture": True},
            {"event_id": "e3", "start_time": 600, "end_time": 660,
             "event_type": "RISK_BLOCKER", "focus_ids": ["f3"], "requires_capture": True},
        ]}, ensure_ascii=False), encoding="utf-8")
        (case / "predictions.json").write_text(json.dumps({"runs": [{"clips": [
            {"clip_id": "c1", "start_time": 90, "end_time": 170},
            {"clip_id": "c2", "start_time": 280, "end_time": 380},
            {"clip_id": "c3", "start_time": 900, "end_time": 1000},
        ]}]}, ensure_ascii=False), encoding="utf-8")
        (case / "manifest.json").write_text(json.dumps({
            "case_id": "case1", "title": "synthetic", "duration_s": 3600,
            "labels": "labels.json", "labels_status": "human_verified",
            "predictions": "predictions.json",
        }), encoding="utf-8")
        return case

    def test_recall_precision_and_false_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = load_case(self._case(Path(tmp)))
            report = evaluate_case(case)
        self.assertEqual(report["events_total"], 3)
        self.assertEqual(report["events_matched"], 2)
        self.assertAlmostEqual(report["event_recall"], 2 / 3)
        self.assertEqual(report["clips_related"], 2)
        self.assertEqual(report["clips_false"], 1)
        self.assertAlmostEqual(report["clip_precision"], 2 / 3)
        self.assertGreater(report["false_saved_minutes_per_hour"], 0)
        self.assertEqual(report["focus_total"], 3)
        self.assertEqual(report["focus_covered"], 2)

    def test_test5_exam_focus_set_is_complete(self):
        data = json.loads((ROOT / "benchmarks" / "meeting_evidence"
                           / "test5_focus_questions.json").read_text(encoding="utf-8"))
        ids = [row["id"] for row in data["questions"]]
        self.assertEqual(ids, [f"F{i}" for i in range(1, 13)])
        self.assertTrue(all(row.get("question") for row in data["questions"]))

    def test_root_gate_requires_real_meetings(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._case(Path(tmp))
            report = evaluate_root(tmp)
            failed = gate(report, min_meetings=20, require_human=True)
            passed = gate(report, min_meetings=1, min_events=1, min_recall=0.5,
                          max_false_minutes_per_hour=1000, require_human=True)
        self.assertFalse(failed["passed"])
        self.assertTrue(passed["passed"], passed)
        self.assertIn("case1", markdown(report, passed))


if __name__ == "__main__":
    unittest.main()