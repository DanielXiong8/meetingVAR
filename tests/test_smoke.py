"""不依赖模型、音频设备或网络的 MVP 契约回归测试。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar import config  # noqa: E402
from meetingvar.monitor.hotwords import HotwordMonitor  # noqa: E402
from meetingvar.pool.store import Pool  # noqa: E402
from meetingvar.report.generate import _fmt_ts, _thin_no_llm  # noqa: E402


class MvpSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._old = config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR
        config.DATA_DIR = root
        config.POOL_DIR = root / "pool"
        config.CLIPS_DIR = root / "clips"

    def tearDown(self) -> None:
        config.DATA_DIR, config.POOL_DIR, config.CLIPS_DIR = self._old
        self.tmp.cleanup()

    def test_hotword_debounce(self) -> None:
        monitor = HotwordMonitor(["S3"], debounce_seconds=10)
        self.assertEqual(monitor.check("提到 S3", now=100), ["S3"])
        self.assertEqual(monitor.check("再次提到 s3", now=105), [])
        self.assertEqual(monitor.check("再次提到 s3", now=111), ["S3"])

    def test_pool_persists_screen_frames(self) -> None:
        pool = Pool("smoke")
        clip_id = pool.add_clip(
            "L1",
            "热词:S3",
            b"RIFF",
            [{"ts": 12.0, "speaker": "S1", "text": "S3"}],
            screens=[(12.0, b"jpeg")],
        )
        clip = pool.list_clips()[0]
        self.assertEqual(clip["clip_id"], clip_id)
        self.assertEqual(clip["audio"]["start_ts"], 12.0)
        self.assertEqual(len(clip["screen"]["keyframes"]), 1)
        self.assertTrue((config.CLIPS_DIR / clip_id / "screens" / "frame_0000.jpg").exists())

    def test_review_round_trip_and_feedback_summary(self) -> None:
        pool = Pool("review")
        clip_id = pool.add_clip(
            "L1",
            "热词:S3",
            b"RIFF",
            [{"ts": 12.0, "speaker": "S1", "text": "S3"}],
        )
        updated = pool.update_review(clip_id[:8], "useful", "需要回听 PI 的上下文")
        self.assertEqual(updated["review"], {"status": "useful", "note": "需要回听 PI 的上下文"})
        summary = pool.feedback_summary()
        self.assertEqual(summary["by_status"], {"pending": 0, "useful": 1, "useless": 0})
        feedback_path = pool.write_feedback_summary()
        self.assertTrue(feedback_path.exists())
        self.assertEqual(len(pool.list_clips()), 1)

    def test_l2_feedback_calibrates_project_threshold_idempotently(self) -> None:
        from meetingvar.memory.calibration import apply_reviews, load_threshold

        project = Path(self.tmp.name) / "project"
        pool = Pool("calibration")
        clip_id = pool.add_clip(
            "L2",
            "interest_score=0.34",
            b"RIFF",
            [{"ts": 12.0, "speaker": "S1", "text": "项目相关内容"}],
        )
        pool.update_review(clip_id, "useless", "这段不相关")
        # L1 reviews are retained in the clip pool but must not calibrate L2.
        pool.add_clip(
            "L1", "热词:S3", b"RIFF", [{"ts": 20.0, "speaker": "S2", "text": "S3"}]
        )

        first = apply_reviews(project, pool.list_clips())
        self.assertEqual(first["sample_count"], 1)
        self.assertEqual(first["by_status"]["useless"], 1)
        self.assertEqual(first["l2_threshold"], 0.35)
        self.assertEqual(load_threshold(project), 0.35)

        repeated = apply_reviews(project, pool.list_clips())
        self.assertEqual(repeated["l2_threshold"], 0.35)
        self.assertEqual(repeated["new_samples"], 0)

        pool.update_review(clip_id, "useful", "重新评估后保留")
        corrected = apply_reviews(project, pool.list_clips())
        self.assertEqual(corrected["sample_count"], 1)
        self.assertEqual(corrected["by_status"]["useful"], 1)
        self.assertEqual(corrected["l2_threshold"], 0.23)

    def test_session_passes_project_threshold_into_interest_scorer(self) -> None:
        import sys
        import types
        from unittest.mock import patch

        pypinyin_stub = types.ModuleType("pypinyin")
        pypinyin_stub.lazy_pinyin = lambda text: list(text)
        with patch.dict(sys.modules, {"pypinyin": pypinyin_stub}):
            import meetingvar.pipeline as pipeline

            with patch.object(pipeline, "ASR"), patch.object(pipeline, "InterestScorer") as scorer:
                pipeline.Session("threshold", terms=[], focus_docs=["项目记忆"], device="cpu", l2_threshold=0.42)
        self.assertEqual(scorer.call_args.kwargs["threshold"], 0.42)

    def test_term_extraction_degrades_without_jieba(self) -> None:
        from unittest.mock import patch

        from meetingvar.memory.extract import extract_terms

        memory = Path(self.tmp.name) / "memory"
        memory.mkdir()
        (memory / "notes.md").write_text("# 测序污染问题\nUMI\nUMI\n", encoding="utf-8")
        with patch("meetingvar.memory.extract._ensure_jieba", side_effect=ImportError("missing jieba")):
            terms = extract_terms(memory)
        self.assertIn("测序污染问题", terms)
        self.assertIn("UMI", terms)

    def test_relative_timestamp_format(self) -> None:
        self.assertEqual(_fmt_ts(0), "00:00:00")
        self.assertEqual(_fmt_ts(3723), "01:02:03")

    def test_keep_marker_is_not_treated_as_comment(self) -> None:
        from meetingvar.cli import load_terms

        words = Path(self.tmp.name) / "terms.txt"
        words.write_text("# ignored\n#!keep 手动术语\nS3\n", encoding="utf-8")
        self.assertEqual(load_terms(words), ["手动术语", "S3"])

    def test_runtime_documents_are_not_project_memory(self) -> None:
        from meetingvar.cli import load_memory_docs
        from meetingvar.memory.extract import extract_terms

        root = Path(self.tmp.name)
        (root / "notes.md").write_text("# 测序污染", encoding="utf-8")
        for directory in (".venv", ".venv-smoke", ".tools", ".runtimes"):
            nested = root / directory
            nested.mkdir()
            (nested / "notes.md").write_text("# 依赖噪声", encoding="utf-8")
        self.assertEqual(load_memory_docs(root), ["# 测序污染"])
        self.assertNotIn("依赖噪声", extract_terms(root))

    def test_short_chinese_heading_is_a_valid_term(self) -> None:
        from meetingvar.memory.extract import _structured

        terms = {}
        _structured("# 测序污染\n# 一\n# 术语表\n", terms)
        self.assertIn("测序污染", terms)
        self.assertNotIn("术语表", terms)

    def test_thin_minutes_has_overview_chapters_and_explicit_actions(self) -> None:
        transcript = [
            {"ts": 0.0, "speaker": "S1", "text": "今天讨论实验方案"},
            {"ts": 301.0, "speaker": "S2", "text": "会后请确认测序污染结果"},
        ]
        minutes = _thin_no_llm(transcript, {"S3": 1})
        self.assertIn("## 全场速览", minutes)
        self.assertIn("## 分章记录", minutes)
        self.assertIn("## 明确行动项", minutes)
        self.assertIn("[ ] [00:05:01] S2: 会后请确认测序污染结果", minutes)


if __name__ == "__main__":
    unittest.main()
