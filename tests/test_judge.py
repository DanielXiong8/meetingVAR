"""L3 离线裁判与事件级评测测试（model-free）：解析/滑窗/契约/证据回填/合并/F2。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.eval import judge, matching  # noqa: E402


class TranscriptTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        p = Path(tempfile.mkdtemp(prefix="mv_judge_")) / "t.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_parse_both_clock_formats_and_relative_seconds(self) -> None:
        p = self._write("[00:07:20] spk0: 第一句\n[00:07:50] spk1: 第二句\n[00:08:05] spk0: 第三句\n")
        turns = judge.parse_transcript(p)
        self.assertEqual([t["t0"] for t in turns], [0.0, 30.0, 45.0])
        self.assertEqual(turns[1]["speaker"], "spk1")
        self.assertEqual(turns[0]["turn_id"], "turn_0")

    def test_parse_minute_only_and_midnight_rollover(self) -> None:
        p = self._write("[23:59] a: 前\n[00:01] b: 后\n")
        turns = judge.parse_transcript(p)
        self.assertEqual(turns[1]["t0"], 120.0, "跨午夜应展开为 +2 分钟而不是负数")

    def test_windows_cover_and_carry_turns(self) -> None:
        p = self._write("[00:00:00] a: 零\n[00:02:10] b: 二百一十\n")
        turns = judge.parse_transcript(p)
        wins = judge.windows(turns, window=120, stride=30)
        self.assertTrue(wins)
        self.assertTrue(any(any(t["turn_id"] == "turn_0" for t in w["turns"]) for w in wins))
        self.assertTrue(any(any(t["turn_id"] == "turn_1" for t in w["turns"]) for w in wins))
        # 最后一窗必须覆盖到末句（不能漏尾）
        self.assertLessEqual(turns[-1]["t0"], wins[-1]["end"])


class JudgeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        p = Path(tempfile.mkdtemp(prefix="mv_judge_c_")) / "t.txt"
        p.write_text("[00:00:00] spk0: 那就按这个架构推进，周五前给结果。\n"
                     "[00:00:10] spk1: 好。\n"
                     "[00:00:20] spk0: KG 先当检索层。\n", encoding="utf-8")
        self.turns = judge.parse_transcript(p)

    def _fake(self, payload: dict):
        def fn(prompt, temperature=0.2, timeout=300):
            fn.prompt = prompt
            return json.dumps(payload, ensure_ascii=False)
        return fn

    def test_quote_backfilled_from_turn_ids_and_prompt_is_position_blind(self) -> None:
        fn = self._fake({"hit": True, "event_type": "DECISION", "event_status": "decided",
                         "focus_id": "focus_1", "evidence_turn_ids": ["turn_0"],
                         "reason": "确定架构", "confidence": 0.9,
                         "owner": None, "deadline": None, "related_event_id": None})
        r = judge.judge_window(self.turns, [{"focus_id": "focus_1", "goal": "架构"}], fn)
        self.assertTrue(r["hit"])
        self.assertEqual(r["evidence_turn_ids"], [0])
        self.assertIn("那就按这个架构推进", r["evidence_quote"], "引文必须由系统从原文回填")
        self.assertEqual(r["event_status"], "decided")
        self.assertNotIn("00:00:00", fn.prompt, "窗口不能带时钟（位置盲）")
        self.assertIn("turn_0", fn.prompt)

    def test_invalid_type_or_missing_evidence_is_rejected(self) -> None:
        fn = self._fake({"hit": True, "event_type": "SOMETHING", "event_status": "decided",
                         "evidence_turn_ids": ["turn_0"], "reason": "x"})
        r = judge.judge_window(self.turns, [], fn)
        self.assertFalse(r["hit"], "未知事件类型必须判否")
        fn2 = self._fake({"hit": True, "event_type": "DECISION", "event_status": "decided",
                          "evidence_turn_ids": ["turn_99"], "reason": "x"})
        r2 = judge.judge_window(self.turns, [], fn2)
        self.assertFalse(r2["hit"], "证据轮次不在窗口内→宁漏勿误判否")

    def test_unknown_focus_id_is_nulled_not_rejected(self) -> None:
        fn = self._fake({"hit": True, "event_type": "DIRECTION", "event_status": "discussed",
                         "focus_id": "focus_9", "evidence_turn_ids": ["turn_2"], "reason": "x"})
        r = judge.judge_window(self.turns, [{"focus_id": "focus_1", "goal": "x"}], fn)
        self.assertTrue(r["hit"])
        self.assertIsNone(r["focus_id"])

    def test_merge_hits_same_type_only(self) -> None:
        hits = [
            {"hit": True, "event_type": "COMMITMENT", "focus_id": "f1", "start": 60, "end": 180,
             "evidence_turn_ids": [1], "evidence_quote": "a", "reason": ""},
            {"hit": True, "event_type": "COMMITMENT", "focus_id": "f1", "start": 210, "end": 330,
             "evidence_turn_ids": [2], "evidence_quote": "b", "reason": ""},
            {"hit": True, "event_type": "DECISION", "focus_id": "f1", "start": 240, "end": 360,
             "evidence_turn_ids": [3], "evidence_quote": "c", "reason": ""},
        ]
        merged = judge.merge_hits(hits, gap=60)
        self.assertEqual(len(merged), 2, "同类型相邻合并，不同类型不合并")
        self.assertEqual(merged[0]["evidence_turn_ids"], [1, 2])
        self.assertEqual(merged[0]["windows"], 2)


class MetricsTests(unittest.TestCase):
    def test_f2_weights_recall(self) -> None:
        self.assertAlmostEqual(matching.f2_score(0.5, 1.0), 0.833, places=3)
        self.assertAlmostEqual(matching.f2_score(1.0, 0.5), 0.556, places=3)
        self.assertEqual(matching.f2_score(0.0, 0.0), 0.0)

    def test_evaluate_preds_merges_and_counts_once(self) -> None:
        gold = [(100.0, 160.0)]
        near = [(90.0, 150.0), (150.0, 210.0)]
        r = matching.evaluate_preds(gold, near, meeting_hours=1.0, merge_gap=60)
        self.assertEqual(r["merged_events"], 1, "相邻/重叠命中先合并为一个事件")
        self.assertEqual((r["TP"], r["FP"], r["FN"]), (1, 0, 0))
        self.assertEqual(r["false_triggers_per_hour"], 0.0)
        self.assertIn("f2", r)
        far = near + [(600.0, 660.0)]
        r2 = matching.evaluate_preds(gold, far, meeting_hours=1.0, merge_gap=60)
        self.assertEqual((r2["TP"], r2["FP"]), (1, 1), "远端合并事件算一次误触发")
        self.assertEqual(r2["false_triggers_per_hour"], 1.0)


if __name__ == "__main__":
    unittest.main()