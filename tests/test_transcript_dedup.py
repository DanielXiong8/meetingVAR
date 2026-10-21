"""Overlapping ASR windows must not duplicate sentences."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.pipeline import is_duplicate_transcript  # noqa: E402


class TranscriptDedupTests(unittest.TestCase):
    def test_exact_duplicate(self):
        rows = [{"ts": 10.0, "text": "它就形成这个 r n 的基底"}]
        self.assertTrue(is_duplicate_transcript(rows, "它就形成这个 r n 的基底", 10.4))

    def test_fuzzy_ending_is_duplicate(self):
        rows = [{"ts": 10.0, "text": "它就形成这个 r n 的基底"}]
        self.assertTrue(is_duplicate_transcript(rows, "形成这个 r n 的基", 11.2))

    def test_distinct_nearby_sentence_is_kept(self):
        rows = [{"ts": 10.0, "text": "同学们，我们现在看第二个问题"}]
        self.assertFalse(is_duplicate_transcript(rows, "这个定理的证明需要先定义开集", 11.2))

    def test_short_fragments_are_not_fuzzy_merged(self):
        rows = [{"ts": 10.0, "text": "呃"}]
        self.assertFalse(is_duplicate_transcript(rows, "好", 10.2))

    def test_outside_window_is_kept(self):
        rows = [{"ts": 1.0, "text": "同一个句子同一个句子"}]
        self.assertFalse(is_duplicate_transcript(rows, "同一个句子同一个句子", 9.0))


if __name__ == "__main__":
    unittest.main()