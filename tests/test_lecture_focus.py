"""Transcript-only lecture focus search contracts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.lecture import build_windows, find_focus, parse_transcript  # noqa: E402


class LectureFocusTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            "[00:00:00] spk0: 今天先讲课程安排",
            "[00:00:30] spk0: 我们来看极值和条件极值",
            "[00:01:30] spk0: 拉格朗日乘子法的条件是梯度为零",
            "[00:02:30] spk0: 下面休息一下",
        ]

    def test_parse_and_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lecture.txt"
            path.write_text("\n".join(self.rows), encoding="utf-8")
            turns = parse_transcript(path)
        self.assertEqual(len(turns), 4)
        self.assertEqual(turns[1].ts, 30.0)
        windows = build_windows(turns, window=90, stride=45)
        self.assertTrue(windows)
        self.assertIn("极值", windows[0]["text"])
        self.assertTrue(any("拉格朗日" in row["text"] for row in windows))

    def test_parse_accepts_minute_only_timestamps(self):
        # Whisper-style output omits the hour field; it must map to the same
        # second offsets instead of being silently dropped.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lecture.txt"
            path.write_text("[00:30] spk0: 极值\n[61:05] spk0: 拉格朗日\n",
                            encoding="utf-8")
            turns = parse_transcript(path)
        self.assertEqual([row.ts for row in turns], [30.0, 3665.0])

    def test_find_focus_returns_timestamped_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lecture.txt"
            path.write_text("\n".join(self.rows), encoding="utf-8")
            turns = parse_transcript(path)

        def fake_judge(prompt, **kwargs):
            hit = "拉格朗日" in prompt and "极值" in prompt
            return json.dumps({"hit": hit, "reason": "讲解条件极值",
                               "evidence_ids": ["turn_1", "turn_2"] if hit else []})

        result = find_focus(turns, "条件极值怎么用拉格朗日乘子法？",
                            keywords=["极值", "拉格朗日"], chat_fn=fake_judge,
                            window=120, stride=60, max_candidates=3)
        self.assertGreaterEqual(result["hits"], 1)
        segment = result["segments"][0]
        self.assertLessEqual(segment["start"], 90)
        self.assertTrue(segment["evidence_ids"])
        self.assertIn("拉格朗日", segment["text"])

    def test_two_keyword_window_is_kept_even_if_judge_says_no(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lecture.txt"
            path.write_text("\n".join(self.rows), encoding="utf-8")
            turns = parse_transcript(path)
        result = find_focus(turns, "极值", keywords=["极值", "拉格朗日"],
                            chat_fn=lambda *a, **k: '{"hit":false,"reason":"","evidence_ids":[]}',
                            window=120, stride=60, max_candidates=2)
        self.assertGreaterEqual(result["hits"], 1)


if __name__ == "__main__":
    unittest.main()